"""
카탈로그 Blueprint — 품목 카탈로그 + 도메인 관리.

/catalog/                     카탈로그 홈
/catalog/items/*              품목 CRUD
/catalog/categories/*         카테고리 관리 (도메인 필터, 비활성화)
/catalog/domains              도메인 관리
/catalog/domains/new          도메인 추가
/catalog/domains/<id>/edit    도메인 수정 (연쇄 업데이트 포함)
/catalog/domains/<id>/toggle  도메인 활성/비활성화
/catalog/domains/<id>/delete  도메인 삭제 (연결 데이터 없을 때만)
"""
import json
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, abort, session, g)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import login_required, require_role
from db.queries import (
    list_catalog_categories, get_catalog_category,
    create_catalog_category, update_catalog_category,
    delete_catalog_category, toggle_catalog_category,
    list_catalog_items, get_catalog_item,
    create_catalog_item, update_catalog_item, delete_catalog_item,
    catalog_stats, get_price_history,
    list_domains, get_domain, create_domain, update_domain,
    toggle_domain, delete_domain, get_domain_impact,
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
    from db.queries import DOMAIN_LIST
    domain_filter = request.args.get("domain", "")
    show_inactive = request.args.get("inactive", "") == "1"

    cats = list_catalog_categories(
        domain=domain_filter or None,
        active_only=not show_inactive,
    )
    return render_template("catalog/categories.html",
                           categories=cats,
                           domain_list=DOMAIN_LIST,
                           selected_domain=domain_filter,
                           show_inactive=show_inactive)


@bp.route("/categories/new", methods=["POST"])
@require_role("manager")
def new_category():
    from db.queries import DOMAIN_LIST
    name        = request.form.get("name", "").strip()
    domain      = request.form.get("domain", "IT").strip()
    description = request.form.get("description", "").strip() or None
    parent_id   = request.form.get("parent_id", "").strip() or None
    sort_order  = int(request.form.get("sort_order", 0) or 0)

    if not name:
        flash("카테고리명을 입력하세요.", "error")
        return redirect(url_for("catalog.categories"))
    if domain not in DOMAIN_LIST:
        domain = "IT"

    create_catalog_category(name, domain=domain, parent_id=parent_id,
                             sort_order=sort_order, description=description)
    flash(f"✅ '[{domain}] {name}' 카테고리가 추가되었습니다.", "success")
    return redirect(url_for("catalog.categories"))


@bp.route("/categories/<category_id>/toggle", methods=["POST"])
@require_role("manager")
def toggle_category(category_id):
    """카테고리 활성/비활성화"""
    from db.queries import toggle_catalog_category
    cat = get_catalog_category(category_id)
    if not cat:
        abort(404)
    new_state = dict(cat).get("is_active", 1) == 0  # 현재 비활성이면 → 활성으로
    toggle_catalog_category(category_id, new_state)
    state_str = "활성화" if new_state else "비활성화"
    flash(f"'{cat['name']}' 카테고리가 {state_str}되었습니다.", "info")
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


# ─── 도메인 관리 ─────────────────────────────────

@bp.route("/domains")
@require_role("manager")
def domains():
    tok  = getattr(g, "auth_token", "") or ""
    doms = list_domains(active_only=False)
    return render_template("catalog/domains.html", domains=doms)


@bp.route("/domains/new", methods=["POST"])
@require_role("manager")
def new_domain():
    tok         = getattr(g, "auth_token", "") or ""
    name        = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    sort_order  = int(request.form.get("sort_order", 0) or 0)

    if not name:
        flash("도메인명을 입력하세요.", "error")
        return redirect(url_for("catalog.domains", _t=tok))

    try:
        create_domain(name, description=description, sort_order=sort_order)
        flash(f"✅ '{name}' 도메인이 추가되었습니다.", "success")
    except Exception as e:
        flash(f"❌ 추가 실패: {e}", "error")

    return redirect(url_for("catalog.domains", _t=tok))


@bp.route("/domains/<domain_id>/edit", methods=["GET", "POST"])
@require_role("manager")
def edit_domain(domain_id):
    tok    = getattr(g, "auth_token", "") or ""
    domain = get_domain(domain_id)
    if not domain:
        abort(404)

    impact = get_domain_impact(domain_id)

    if request.method == "POST":
        new_name    = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        sort_order  = int(request.form.get("sort_order", 0) or 0)
        old_name    = dict(domain)["name"]

        if not new_name:
            flash("도메인명을 입력하세요.", "error")
            return render_template("catalog/domain_edit.html",
                                   domain=domain, impact=impact)

        cascaded = update_domain(
            domain_id, old_name=old_name,
            name=new_name, description=description, sort_order=sort_order
        )

        msg = f"✅ '{old_name}' → '{new_name}' 수정 완료"
        if old_name != new_name:
            msg += (f" (입찰 {cascaded['bids']}개, "
                    f"카테고리 {cascaded['categories']}개 연쇄 업데이트)")
        flash(msg, "success")
        return redirect(url_for("catalog.domains", _t=tok))

    return render_template("catalog/domain_edit.html",
                           domain=domain, impact=impact)


@bp.route("/domains/<domain_id>/toggle", methods=["POST"])
@require_role("manager")
def toggle_domain_route(domain_id):
    tok    = getattr(g, "auth_token", "") or ""
    domain = get_domain(domain_id)
    if not domain:
        abort(404)

    d = dict(domain)
    new_state = d.get("is_active", 1) == 0
    toggle_domain(domain_id, new_state)
    state_str = "활성화" if new_state else "비활성화"
    flash(f"'{d['name']}' 도메인이 {state_str}되었습니다.", "info")
    return redirect(url_for("catalog.domains", _t=tok))


@bp.route("/domains/<domain_id>/delete", methods=["POST"])
@require_role("manager")
def delete_domain_route(domain_id):
    tok    = getattr(g, "auth_token", "") or ""
    domain = get_domain(domain_id)
    if not domain:
        abort(404)

    try:
        delete_domain(domain_id)
        flash(f"'{dict(domain)['name']}' 도메인이 삭제되었습니다.", "info")
    except ValueError as e:
        flash(str(e), "error")

    return redirect(url_for("catalog.domains", _t=tok))


# ─── 유사 품목 클러스터링 (Phase 3-B) ─────────────

@bp.route("/clusters")
@login_required
def clusters():
    from db.queries import list_clusters, get_cluster_summary, list_domains
    tok     = getattr(g, "auth_token", "") or ""
    status  = request.args.get("status", "pending")
    cluster_list = list_clusters(status=status if status != "all" else None)
    summary = get_cluster_summary()
    domains = list_domains(active_only=True)
    return render_template("catalog/clusters.html",
                           clusters=cluster_list, summary=summary,
                           domains=domains, selected_status=status)


@bp.route("/clusters/run", methods=["POST"])
@require_role("manager")
def run_clusters():
    """LLM 클러스터링 실행"""
    from db.queries import (list_catalog_items, list_clusters,
                             get_user_llm_settings)
    from extractors.catalog_clusterer import run_clustering, save_clusters
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""
    auth_data = getattr(g, "auth_data", None) or {}
    uid = auth_data.get("user_id", "")
    domain_filter = request.form.get("domain", "").strip() or None

    llm = get_user_llm_settings(uid)
    if not llm.get("api_key"):
        flash("API 키가 설정되지 않았습니다. ⚙ 내 프로필에서 설정하세요.", "error")
        return redirect(url_for("catalog.clusters", _t=tok))

    try:
        items_raw = [dict(i) for i in list_catalog_items(active_only=True)]
        if len(items_raw) < 2:
            flash("카탈로그 품목이 2개 이상이어야 클러스터링이 가능합니다.", "warning")
            return redirect(url_for("catalog.clusters", _t=tok))

        cluster_list = run_clustering(
            catalog_items=items_raw,
            api_key=llm["api_key"],
            provider_id=llm["provider"],
            model=llm["model"],
            domain=domain_filter,
        )
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        n = save_clusters(conn, cluster_list)
        conn.close()

        if n:
            flash(f"✅ 클러스터링 완료 — {n}개 유사 그룹 감지됨", "success")
        else:
            flash("유사 품목이 감지되지 않았습니다.", "info")
    except Exception as e:
        flash(f"❌ 클러스터링 실패: {e}", "error")

    return redirect(url_for("catalog.clusters", _t=tok))


@bp.route("/clusters/<cluster_id>")
@login_required
def cluster_detail(cluster_id):
    from db.queries import get_cluster, list_catalog_items
    cluster, members = get_cluster(cluster_id)
    if not cluster:
        abort(404)
    # 대표 품목 교체용 — 전체 활성 품목 목록
    all_items = list_catalog_items(active_only=True)
    tok = getattr(g, "auth_token", "") or ""
    return render_template("catalog/cluster_detail.html",
                           cluster=cluster, members=members,
                           all_items=all_items)


@bp.route("/clusters/<cluster_id>/accept", methods=["POST"])
@require_role("manager")
def accept_cluster(cluster_id):
    from extractors.catalog_clusterer import accept_cluster as do_accept
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""
    auth_data = getattr(g, "auth_data", None) or {}
    uid = auth_data.get("user_id", "")
    representative_id = request.form.get("representative_id", "").strip() or None

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        result = do_accept(conn, cluster_id, uid, representative_id)
        conn.close()
        flash(
            f"✅ 병합 완료 — 대표 품목으로 {result['merged_count']}개 통합, "
            f"별칭 {result['aliases_added']}개",
            "success"
        )
    except Exception as e:
        flash(f"❌ 병합 실패: {e}", "error")

    return redirect(url_for("catalog.clusters", _t=tok))


@bp.route("/clusters/<cluster_id>/reject", methods=["POST"])
@require_role("manager")
def reject_cluster(cluster_id):
    from extractors.catalog_clusterer import reject_cluster as do_reject
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""
    auth_data = getattr(g, "auth_data", None) or {}
    uid = auth_data.get("user_id", "")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    do_reject(conn, cluster_id, uid)
    conn.close()
    flash("제안이 거부되었습니다.", "info")
    return redirect(url_for("catalog.clusters", _t=tok))
