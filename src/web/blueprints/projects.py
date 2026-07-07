from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import login_required, require_role
from db.queries import (
    list_projects, get_project, create_project, update_project,
    list_bids, create_bid, reset_project_submissions,
    list_attr_defs, get_project_attrs, set_project_attrs, attr_value_options
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
    from db.queries import list_domain_names
    domains = list_domain_names()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        desc = request.form.get("description", "").strip()
        domain = request.form.get("domain", "").strip() or (domains[0] if domains else "공통")
        if domain not in domains:
            domain = domains[0] if domains else "공통"
        if not name:
            flash("프로젝트명을 입력하세요.", "error")
            return render_template("projects/form.html", domains=domains)

        from flask import session
        pid = create_project(name, desc, owner_id=session.get("user_id"), domain=domain)
        flash(f"프로젝트 '{name}'이 생성되었습니다.", "success")
        return redirect(url_for("projects.detail", project_id=pid))

    return render_template("projects/form.html", domains=domains)


@bp.route("/projects/<project_id>")
@login_required
def detail(project_id):
    project = get_project(project_id)
    if not project:
        abort(404)
    bids = list_bids(project_id)
    # 기준정보: 이 프로젝트 도메인의 항목(공통 + 도메인 전용)만
    _pdomain = (dict(project).get("domain")) or "공통"
    attr_defs = [dict(d) for d in list_attr_defs(domain=_pdomain)]
    attrs = get_project_attrs(project_id)
    attr_options = {d["attr_key"]: attr_value_options(d["attr_key"])
                    for d in attr_defs}
    from db.queries import list_domain_names
    return render_template("projects/detail.html", project=project, bids=bids,
                           attr_defs=attr_defs, attrs=attrs,
                           attr_options=attr_options,
                           domains=list_domain_names())


@bp.route("/projects/<project_id>/attrs", methods=["POST"])
@require_role("manager")
def save_attrs(project_id):
    """프로젝트 기준정보 저장 (사전에 정의된 속성만 수용)."""
    project = get_project(project_id)
    if not project:
        abort(404)
    from db.queries import list_domain_names, update_project_domain
    # 도메인 변경이 함께 온 경우 먼저 반영 → 그 도메인의 항목을 수용
    new_domain = request.form.get("domain", "").strip()
    if new_domain and new_domain in list_domain_names():
        update_project_domain(project_id, new_domain)
        eff_domain = new_domain
    else:
        eff_domain = (dict(project).get("domain")) or "공통"
    values = {d["attr_key"]: request.form.get(f"attr_{d['attr_key']}", "")
              for d in list_attr_defs(domain=eff_domain)}
    set_project_attrs(project_id, values)
    flash("기준정보가 저장되었습니다.", "success")
    return redirect(url_for("projects.detail", project_id=project_id))


@bp.route("/projects/<project_id>/bids/new", methods=["GET", "POST"])
@require_role("manager")
def new_bid(project_id):
    from db.queries import list_domain_names
    project = get_project(project_id)
    if not project:
        abort(404)
    domains = list_domain_names()
    # 프로젝트 기본 도메인 → 입찰 도메인의 디폴트로 승계
    project_domain = (dict(project).get("domain")) or (domains[0] if domains else "공통")

    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        due_date = request.form.get("due_date", "").strip() or None
        desc     = request.form.get("description", "").strip()
        domain   = request.form.get("domain", "").strip() or project_domain
        if domain not in domains:
            domain = project_domain
        if not name:
            flash("입찰명을 입력하세요.", "error")
            return render_template("projects/bid_form.html", project=project,
                                   domains=domains, project_domain=project_domain)

        from flask import session
        bid_id = create_bid(project_id, name, due_date, desc,
                            created_by=session.get("user_id"), domain=domain)
        flash(f"입찰 '{name}'이 생성되었습니다.", "success")
        return redirect(url_for("bids.detail", bid_id=bid_id))

    return render_template("projects/bid_form.html", project=project,
                           domains=domains, project_domain=project_domain)


@bp.route("/projects/<project_id>/rename", methods=["POST"])
@require_role("manager")
def rename_project(project_id):
    """프로젝트명 인라인 수정."""
    project = get_project(project_id)
    if not project:
        abort(404)
    name = request.form.get("name", "").strip()
    if not name:
        flash("프로젝트명을 입력하세요.", "error")
    else:
        update_project(project_id, name=name)
        flash("프로젝트명이 수정되었습니다.", "success")
    return redirect(url_for("projects.detail", project_id=project_id))


@bp.route("/projects/<project_id>/hard-delete", methods=["POST"])
@require_role("admin")
def hard_delete(project_id):
    """프로젝트 완전 삭제 — admin 전용, 3중 안전장치.

    ① 보관(archived) 상태에서만 허용(2단계 강제)
    ② 프로젝트명 타이핑 확인(서버 검증)
    ③ 자식→부모 단일 트랜잭션 cascade. 업로드 물리 파일은 보존."""
    project = get_project(project_id)
    if not project:
        abort(404)
    pd = dict(project)
    if pd.get("status") != "archived":
        flash("완전 삭제는 '보관(archived)' 상태에서만 가능합니다. 먼저 보관 처리하세요.",
              "error")
        return redirect(url_for("projects.detail", project_id=project_id))
    typed = request.form.get("confirm_name", "").strip()
    if typed != (pd.get("name") or "").strip():
        flash(f"입력한 이름이 현재 프로젝트명 '{pd.get('name')}'과(와) 달라 "
              f"삭제가 취소되었습니다. 최신 프로젝트명을 확인 후 다시 시도하세요.", "error")
        return redirect(url_for("projects.detail", project_id=project_id))
    from db.queries import hard_delete_project
    n = hard_delete_project(project_id)
    flash(f"🗑 프로젝트 '{pd['name']}' 완전 삭제 완료 — 입찰 {n['bids']}, "
          f"제출서 {n['submissions']}, 품목 {n['items']}, 클러스터 {n['clusters']} 삭제. "
          f"(업로드 파일은 보존됨)", "success")
    return redirect(url_for("projects.index"))


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
