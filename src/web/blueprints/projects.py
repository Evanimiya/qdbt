from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, g, session
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import login_required, require_role
from db.queries import (
    list_projects, get_project, create_project, update_project,
    list_bids, create_bid, reset_project_submissions
)

bp = Blueprint("projects", __name__)


@bp.route("/")
@login_required
def index():
    projects = list_projects()
    return render_template("projects/index.html", projects=projects)


@bp.route("/projects/new", methods=["GET", "POST"])
@require_role("manager")
def new_project():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        desc = request.form.get("description", "").strip()
        if not name:
            flash("프로젝트명을 입력하세요.", "error")
            return render_template("projects/form.html")

        pid = create_project(name, desc, owner_id=session.get("user_id"))
        flash(f"프로젝트 '{name}'이 생성되었습니다.", "success")
        return redirect(url_for("projects.detail", project_id=pid))

    return render_template("projects/form.html")


@bp.route("/projects/<project_id>")
@login_required
def detail(project_id):
    project = get_project(project_id)
    if not project:
        abort(404)
    bids = list_bids(project_id)
    return render_template("projects/detail.html", project=project, bids=bids)


@bp.route("/projects/<project_id>/bids/new", methods=["GET", "POST"])
@require_role("manager")
def new_bid(project_id):
    project = get_project(project_id)
    if not project:
        abort(404)

    from db.queries import list_domains
    tok = getattr(g, "auth_token", "") or ""
    active_domains = list_domains(active_only=True)

    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        due_date = request.form.get("due_date", "").strip() or None
        desc     = request.form.get("description", "").strip()
        domain   = request.form.get("domain", "IT").strip()

        if not name:
            flash("입찰명을 입력하세요.", "error")
            return render_template("projects/bid_form.html",
                                   project=project, domains=active_domains)

        valid_names = [dict(d)["name"] for d in active_domains]
        if domain not in valid_names:
            domain = valid_names[0] if valid_names else "IT"

        bid_id = create_bid(project_id, name, due_date, desc,
                            created_by=session.get("user_id"),
                            domain=domain)
        flash(f"입찰 '{name}' ({domain})이 생성되었습니다.", "success")
        return redirect(url_for("bids.detail", bid_id=bid_id, _t=tok))

    return render_template("projects/bid_form.html",
                           project=project, domains=active_domains)


@bp.route("/projects/<project_id>/status", methods=["POST"])
@require_role("manager")
def update_status(project_id):
    status = request.form.get("status")
    if status in ("active", "closed", "archived"):
        update_project(project_id, status=status)
        flash("프로젝트 상태가 업데이트되었습니다.", "success")
    return redirect(url_for("projects.detail", project_id=project_id))


@bp.route("/projects/<project_id>/reset", methods=["POST"])
@require_role("manager")
def reset_demo(project_id):
    """데모용: 모든 제출서 추출 결과 초기화 → pending 상태로 되돌리기"""
    project = get_project(project_id)
    if not project:
        abort(404)
    n = reset_project_submissions(project_id)
    flash(f"🔄 데모 초기화 완료 — {n}개 제출서가 pending 상태로 초기화되었습니다.", "success")
    return redirect(url_for("projects.detail", project_id=project_id))
