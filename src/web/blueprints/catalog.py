"""
카탈로그 Blueprint — 품목 카탈로그 관리.

/catalog/                     카탈로그 홈 (품목 목록)
/catalog/items/new            품목 등록
/catalog/items/<id>           품목 상세
/catalog/items/<id>/edit      품목 수정
/catalog/items/<id>/delete    품목 삭제 (소프트)
/catalog/categories           카테고리 관리
/catalog/categories/new       카테고리 추가
/catalog/categories/<id>/delete  카테고리 삭제
"""
import json
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, abort, session)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import login_required, require_role
from db.queries import (
    list_catalog_categories, get_catalog_category,
    create_catalog_category, update_catalog_category, delete_catalog_category,
    list_catalog_items, get_catalog_item,
    create_catalog_item, update_catalog_item, delete_catalog_item,
    catalog_stats,
)

bp = Blueprint("catalog", __name__)


# ─── 홈 ────────────────────────────────────────

@bp.route("/")
@login_required
def index():
    category_id = request.args.get("category_id", "")
    search      = request.args.get("q", "").strip()
    categories  = list_catalog_categories()
    items       = list_catalog_items(
        category_id=category_id or None,
        search=search or None,
    )
    stats = catalog_stats()
    return render_template("catalog/index.html",
                           categories=categories, items=items,
                           stats=stats,
                           selected_category=category_id, q=search)


# ─── 품목 ───────────────────────────────────────

@bp.route("/items/new", methods=["GET", "POST"])
@require_role("manager")
def new_item():
    categories = list_catalog_categories()

    if request.method == "POST":
        name     = request.form.get("name_canonical", "").strip()
        cat_id   = request.form.get("category_id", "").strip() or None
        unit     = request.form.get("unit_std", "").strip() or None
        spec     = request.form.get("spec_template", "").strip() or None
        # 별칭: 줄바꿈으로 구분 입력
        aliases_raw = request.form.get("aliases", "").strip()
        aliases = [a.strip() for a in aliases_raw.splitlines() if a.strip()]

        if not name:
            flash("품목명을 입력하세요.", "error")
            return render_template("catalog/item_form.html",
                                   categories=categories, item=None)

        cid = create_catalog_item(
            name_canonical=name, category_id=cat_id,
            aliases=aliases, spec_template=spec, unit_std=unit,
            created_by=session.get("user_id"),
        )
        flash(f"✅ '{name}' 품목이 등록되었습니다.", "success")
        return redirect(url_for("catalog.item_detail", item_id=cid))

    return render_template("catalog/item_form.html",
                           categories=categories, item=None)


@bp.route("/items/<item_id>")
@login_required
def item_detail(item_id):
    item = get_catalog_item(item_id)
    if not item:
        abort(404)
    try:
        aliases = json.loads(item["aliases"] or "[]")
    except Exception:
        aliases = []

    from db.queries import get_price_history
    price_history = get_price_history(item_id)

    return render_template("catalog/item_detail.html",
                           item=item, aliases=aliases,
                           price_history=price_history)


@bp.route("/items/<item_id>/edit", methods=["GET", "POST"])
@require_role("manager")
def edit_item(item_id):
    item = get_catalog_item(item_id)
    if not item:
        abort(404)
    categories = list_catalog_categories()

    try:
        aliases = json.loads(item["aliases"] or "[]")
    except Exception:
        aliases = []

    if request.method == "POST":
        name   = request.form.get("name_canonical", "").strip()
        cat_id = request.form.get("category_id", "").strip() or None
        unit   = request.form.get("unit_std", "").strip() or None
        spec   = request.form.get("spec_template", "").strip() or None
        aliases_raw = request.form.get("aliases", "").strip()
        new_aliases = [a.strip() for a in aliases_raw.splitlines() if a.strip()]

        if not name:
            flash("품목명을 입력하세요.", "error")
            return render_template("catalog/item_form.html",
                                   categories=categories, item=item,
                                   aliases=aliases)

        update_catalog_item(item_id,
                            name_canonical=name, category_id=cat_id,
                            unit_std=unit, spec_template=spec,
                            aliases=new_aliases, is_active=1)
        flash(f"✅ '{name}' 품목이 수정되었습니다.", "success")
        return redirect(url_for("catalog.item_detail", item_id=item_id))

    return render_template("catalog/item_form.html",
                           categories=categories, item=item,
                           aliases=aliases)


@bp.route("/items/<item_id>/delete", methods=["POST"])
@require_role("manager")
def delete_item(item_id):
    item = get_catalog_item(item_id)
    if not item:
        abort(404)
    delete_catalog_item(item_id)
    flash(f"'{item['name_canonical']}' 품목이 비활성화되었습니다.", "info")
    return redirect(url_for("catalog.index"))


# ─── 카테고리 ────────────────────────────────────

@bp.route("/categories")
@require_role("manager")
def categories():
    cats = list_catalog_categories()
    return render_template("catalog/categories.html", categories=cats)


@bp.route("/categories/new", methods=["POST"])
@require_role("manager")
def new_category():
    name      = request.form.get("name", "").strip()
    parent_id = request.form.get("parent_id", "").strip() or None
    sort_order = int(request.form.get("sort_order", 0) or 0)

    if not name:
        flash("카테고리명을 입력하세요.", "error")
        return redirect(url_for("catalog.categories"))

    create_catalog_category(name, parent_id=parent_id, sort_order=sort_order)
    flash(f"✅ '{name}' 카테고리가 추가되었습니다.", "success")
    return redirect(url_for("catalog.categories"))


@bp.route("/categories/<category_id>/delete", methods=["POST"])
@require_role("manager")
def delete_category(category_id):
    try:
        cat = get_catalog_category(category_id)
        delete_catalog_category(category_id)
        flash(f"'{cat['name']}' 카테고리가 삭제되었습니다.", "info")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("catalog.categories"))
