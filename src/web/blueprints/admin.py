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
from db.queries import (list_users, create_user,
                        list_catalog_categories, create_catalog_category,
                        update_catalog_category, toggle_catalog_category,
                        delete_catalog_category, get_catalog_category,
                        DOMAIN_LIST, list_domain_names,
                        list_domains, create_domain, update_domain,
                        toggle_domain, delete_domain, get_domain)

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


# ─── 카테고리(분류) 관리 ─────────────────────────

@bp.route("/categories")
@require_role("manager")
def categories():
    """도메인별 카테고리(분류) 관리 화면."""
    domain = request.args.get("domain", "").strip() or None
    # 활성/비활성 모두 표시(관리 목적)
    cats = list_catalog_categories(domain=domain, active_only=False)
    # 도메인별 그룹핑
    grouped = {}
    for c in cats:
        cd = dict(c)
        grouped.setdefault(cd["domain"], []).append(cd)
    return render_template("admin/categories.html",
                           grouped=grouped, domains=list_domain_names(active_only=False),
                           domain_rows=list_domains(active_only=False),
                           selected_domain=domain)


@bp.route("/categories/new", methods=["POST"])
@require_role("manager")
def category_new():
    name   = request.form.get("name", "").strip()
    domain = request.form.get("domain", "IT").strip() or "IT"
    try:
        sort_order = int(request.form.get("sort_order", "0") or 0)
    except ValueError:
        sort_order = 0
    desc = request.form.get("description", "").strip() or None
    if not name:
        flash("분류명을 입력하세요.", "error")
    else:
        create_catalog_category(name, domain=domain,
                                sort_order=sort_order, description=desc)
        flash(f"분류 '{name}' ({domain})이 추가되었습니다.", "success")
    return redirect(url_for("admin.categories", domain=domain))


@bp.route("/categories/<category_id>/update", methods=["POST"])
@require_role("manager")
def category_update(category_id):
    cat = get_catalog_category(category_id)
    if not cat:
        flash("분류를 찾을 수 없습니다.", "error")
        return redirect(url_for("admin.categories"))
    fields = {}
    name = request.form.get("name", "").strip()
    if name:
        fields["name"] = name
    if request.form.get("sort_order", "").strip():
        try:
            fields["sort_order"] = int(request.form["sort_order"])
        except ValueError:
            pass
    if "description" in request.form:
        fields["description"] = request.form.get("description", "").strip() or None
    if "aliases" in request.form:
        # 쉼표·줄바꿈 구분 텍스트 → JSON 배열
        import json as _json
        raw = request.form.get("aliases", "")
        parts = [a.strip() for a in raw.replace("\n", ",").split(",") if a.strip()]
        # 중복 제거(순서 보존)
        seen, uniq = set(), []
        for a in parts:
            if a not in seen:
                seen.add(a); uniq.append(a)
        fields["aliases"] = _json.dumps(uniq, ensure_ascii=False) if uniq else None
    if fields:
        update_catalog_category(category_id, **fields)
        flash("분류가 수정되었습니다.", "success")
    return redirect(url_for("admin.categories", domain=dict(cat).get("domain")))


@bp.route("/categories/<category_id>/toggle", methods=["POST"])
@require_role("manager")
def category_toggle(category_id):
    cat = get_catalog_category(category_id)
    if not cat:
        flash("분류를 찾을 수 없습니다.", "error")
        return redirect(url_for("admin.categories"))
    cd = dict(cat)
    new_active = not bool(cd.get("is_active"))
    toggle_catalog_category(category_id, new_active)
    flash(f"분류 '{cd['name']}'이(가) {'활성화' if new_active else '비활성화'}되었습니다.",
          "success")
    return redirect(url_for("admin.categories", domain=cd.get("domain")))


@bp.route("/categories/<category_id>/delete", methods=["POST"])
@require_role("admin")
def category_delete(category_id):
    cat = get_catalog_category(category_id)
    if not cat:
        flash("분류를 찾을 수 없습니다.", "error")
        return redirect(url_for("admin.categories"))
    cd = dict(cat)
    try:
        delete_catalog_category(category_id)
        flash(f"분류 '{cd['name']}'이(가) 삭제되었습니다.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("admin.categories", domain=cd.get("domain")))


# ═══════════════════════════════════════════
# 도메인 관리 (항목 10) — 분류 관리 화면 상단에서 CRUD
# ═══════════════════════════════════════════

@bp.route("/domains/new", methods=["POST"])
@require_role("manager")
def domain_new():
    name = request.form.get("name", "").strip()
    desc = request.form.get("description", "").strip() or None
    if not name:
        flash("도메인명을 입력하세요.", "error")
    else:
        try:
            create_domain(name, description=desc)
            flash(f"도메인 '{name}'이(가) 추가되었습니다.", "success")
        except Exception as e:
            flash(f"도메인 추가 실패: {e}", "error")
    return redirect(url_for("admin.categories", domain=name))


@bp.route("/domains/<domain_id>/update", methods=["POST"])
@require_role("manager")
def domain_update(domain_id):
    d = get_domain(domain_id)
    if not d:
        flash("도메인을 찾을 수 없습니다.", "error")
        return redirect(url_for("admin.categories"))
    fields = {}
    if request.form.get("name", "").strip():
        fields["name"] = request.form["name"].strip()
    if request.form.get("description") is not None:
        fields["description"] = request.form.get("description", "").strip() or None
    if request.form.get("sort_order", "").strip():
        try:
            fields["sort_order"] = int(request.form["sort_order"])
        except ValueError:
            pass
    if fields:
        update_domain(domain_id, **fields)
        flash("도메인이 수정되었습니다.", "success")
    return redirect(url_for("admin.categories"))


@bp.route("/domains/<domain_id>/toggle", methods=["POST"])
@require_role("manager")
def domain_toggle(domain_id):
    d = get_domain(domain_id)
    if not d:
        flash("도메인을 찾을 수 없습니다.", "error")
        return redirect(url_for("admin.categories"))
    dd = dict(d)
    toggle_domain(domain_id, not bool(dd.get("is_active")))
    flash(f"도메인 '{dd['name']}'이(가) "
          f"{'비활성화' if dd.get('is_active') else '활성화'}되었습니다.", "success")
    return redirect(url_for("admin.categories"))


@bp.route("/domains/<domain_id>/delete", methods=["POST"])
@require_role("admin")
def domain_delete(domain_id):
    d = get_domain(domain_id)
    if not d:
        flash("도메인을 찾을 수 없습니다.", "error")
        return redirect(url_for("admin.categories"))
    dd = dict(d)
    if delete_domain(domain_id):
        flash(f"도메인 '{dd['name']}'이(가) 삭제되었습니다.", "success")
    else:
        flash(f"도메인 '{dd['name']}'은(는) 사용 중이라 삭제할 수 없습니다. "
              f"(입찰·분류에서 참조 중) 비활성화를 권장합니다.", "error")
    return redirect(url_for("admin.categories"))


# ═══════════════════════════════════════════
# 기준정보 관리 (항목 11) — project_attr_defs CRUD
# ═══════════════════════════════════════════

@bp.route("/attr-defs")
@require_role("manager")
def attr_defs():
    """기준정보 정의(항목) 관리 화면."""
    from db.queries import list_attr_defs
    defs = list_attr_defs(active_only=False)
    return render_template("admin/attr_defs.html", defs=defs)


@bp.route("/attr-defs/new", methods=["POST"])
@require_role("manager")
def attr_def_new():
    from db.queries import create_attr_def
    key = request.form.get("attr_key", "").strip()
    label = request.form.get("label", "").strip()
    if not label:
        flash("기준정보 항목명을 입력하세요.", "error")
    else:
        try:
            create_attr_def(label=label, attr_key=key or None)
            flash(f"기준정보 항목 '{label}'이(가) 추가되었습니다.", "success")
        except Exception as e:
            flash(f"추가 실패: {e}", "error")
    return redirect(url_for("admin.attr_defs"))


@bp.route("/attr-defs/<attr_key>/update", methods=["POST"])
@require_role("manager")
def attr_def_update(attr_key):
    from db.queries import update_attr_def
    fields = {}
    if request.form.get("label", "").strip():
        fields["label"] = request.form["label"].strip()
    if request.form.get("sort_order", "").strip():
        try:
            fields["sort_order"] = int(request.form["sort_order"])
        except ValueError:
            pass
    if fields:
        update_attr_def(attr_key, **fields)
        flash("기준정보 항목이 수정되었습니다.", "success")
    return redirect(url_for("admin.attr_defs"))


@bp.route("/attr-defs/<attr_key>/toggle", methods=["POST"])
@require_role("manager")
def attr_def_toggle(attr_key):
    from db.queries import toggle_attr_def, get_attr_def
    d = get_attr_def(attr_key)
    if d:
        toggle_attr_def(attr_key, not bool(dict(d).get("is_active")))
        flash("기준정보 항목 상태가 변경되었습니다.", "success")
    return redirect(url_for("admin.attr_defs"))
