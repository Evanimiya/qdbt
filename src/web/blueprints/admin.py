"""
Admin Blueprint — 사용자 관리.

/admin/users      사용자 목록
/admin/users/new  신규 사용자 추가
/admin/init-db    DB 초기화 (개발용)
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import require_role, hash_password
from db.queries import list_users, create_user

bp = Blueprint("admin", __name__)


@bp.route("/users")
@require_role("admin")
def users():
    return render_template("admin/users.html", users=list_users())


@bp.route("/users/new", methods=["GET", "POST"])
@require_role("admin")
def new_user():
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        name     = request.form.get("name", "").strip()
        dept     = request.form.get("dept", "").strip()
        role     = request.form.get("role", "viewer")
        password = request.form.get("password", "")

        if not all([email, name, password]):
            flash("이메일, 이름, 비밀번호는 필수입니다.", "error")
            return render_template("admin/user_form.html")

        try:
            create_user(email, name, role=role, dept=dept,
                        password_hash=hash_password(password))
            flash(f"사용자 '{name}' ({role})이 추가되었습니다.", "success")
            return redirect(url_for("admin.users"))
        except Exception as e:
            flash(f"추가 실패: {e}", "error")

    return render_template("admin/user_form.html")


@bp.route("/init-db", methods=["POST"])
@require_role("admin")
def init_db():
    from db.schema import init_db as _init
    _init(reset=request.form.get("reset") == "1")
    flash("DB가 초기화되었습니다.", "success")
    return redirect(url_for("projects.index"))


@bp.route("/changelog")
@require_role("manager")
def changelog():
    """시스템 업데이트 로그 (관리 메뉴)"""
    from pathlib import Path
    log_path = Path(__file__).parent.parent.parent.parent / "docs" / "CHANGELOG.md"
    content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return render_template("admin/changelog.html", content=content)
