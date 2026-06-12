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
    from db.queries import (list_clusters, get_cluster_summary,
                             list_bids_with_done_submissions)
    tok           = getattr(g, "auth_token", "") or ""
    status        = request.args.get("status", "pending")
    bid_id_filter = request.args.get("bid_id", "")
    cluster_list  = list_clusters(
        bid_id=bid_id_filter or None,
        status=status if status != "all" else None,
    )
    summary    = get_cluster_summary(bid_id=bid_id_filter or None)
    avail_bids = list_bids_with_done_submissions()
    return render_template("catalog/clusters.html",
                           clusters=cluster_list, summary=summary,
                           avail_bids=avail_bids,
                           selected_status=status,
                           selected_bid=bid_id_filter)


import threading
_cluster_jobs = {}  # job_id → {'status': ..., 'message': ..., 'n': ...}


def _cluster_worker(app, job_id, bid_id, items_raw, llm):
    """백그라운드에서 LLM 클러스터링 실행"""
    from extractors.catalog_clusterer import run_clustering, save_clusters
    from config import DB_PATH
    import sqlite3

    _cluster_jobs[job_id] = {
        "status": "running",
        "message": f"품목 {len(items_raw)}개 분석 중...",
        "n": 0,
    }
    try:
        with app.app_context():
            cluster_list = run_clustering(
                submission_items=items_raw,
                api_key=llm["api_key"],
                provider_id=llm["provider"],
                model=llm["model"],
            )
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            n = save_clusters(conn, cluster_list, bid_id=bid_id)
            conn.close()
            _cluster_jobs[job_id] = {
                "status": "done",
                "message": f"{n}개 유사 그룹 감지됨" if n else "유사 품목 없음",
                "n": n,
                "bid_id": bid_id,
            }
    except Exception as e:
        _cluster_jobs[job_id] = {
            "status": "error",
            "message": str(e),
            "n": 0,
        }


@bp.route("/clusters/run", methods=["POST"])
@require_role("manager")
def run_clusters():
    """LLM 클러스터링 — 백그라운드 실행 후 job_id 반환"""
    import uuid as _uuid
    from flask import current_app
    from db.queries import (list_submission_items_for_clustering,
                             get_user_llm_settings)

    tok    = getattr(g, "auth_token", "") or ""
    auth_data = getattr(g, "auth_data", None) or {}
    uid    = auth_data.get("user_id", "")
    bid_id = request.form.get("bid_id", "").strip()

    if not bid_id:
        flash("분석할 입찰을 선택하세요.", "error")
        return redirect(url_for("catalog.clusters", _t=tok))

    llm = get_user_llm_settings(uid)
    if not llm.get("api_key"):
        flash("API 키가 설정되지 않았습니다. ⚙ 내 프로필에서 설정하세요.", "error")
        return redirect(url_for("catalog.clusters", _t=tok))

    items_raw = [dict(i) for i in list_submission_items_for_clustering(bid_id)]
    if len(items_raw) < 2:
        flash("추출 완료 견적서가 2개 이상인 입찰을 선택하세요.", "warning")
        return redirect(url_for("catalog.clusters", _t=tok))

    job_id = str(_uuid.uuid4())
    app = current_app._get_current_object()
    t = threading.Thread(
        target=_cluster_worker,
        args=(app, job_id, bid_id, items_raw, llm),
        daemon=True,
    )
    t.start()

    # 진행현황 페이지로 redirect
    return redirect(url_for("catalog.cluster_progress",
                            job_id=job_id, bid_id=bid_id, _t=tok))


@bp.route("/clusters/progress/<job_id>")
@login_required
def cluster_progress(job_id):
    """클러스터링 진행현황 페이지 (polling UI)"""
    bid_id = request.args.get("bid_id", "")
    tok    = getattr(g, "auth_token", "") or ""
    return render_template("catalog/cluster_progress.html",
                           job_id=job_id, bid_id=bid_id)


@bp.route("/clusters/status/<job_id>.json")
@login_required
def cluster_status_json(job_id):
    """클러스터링 상태 polling 엔드포인트"""
    from flask import jsonify
    job = _cluster_jobs.get(job_id, {"status": "unknown", "message": "작업을 찾을 수 없습니다.", "n": 0})
    return jsonify(job)


@bp.route("/clusters/<cluster_id>")
@login_required
def cluster_detail(cluster_id):
    from db.queries import (get_cluster, list_submission_items_for_clustering)
    from extractors.catalog_clusterer import is_high_confidence
    from config import DB_PATH
    import sqlite3

    cluster, members = get_cluster(cluster_id)
    if not cluster:
        abort(404)

    # 90% 이상 확정 버튼 활성화 여부 판단
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    high_conf = is_high_confidence(conn, cluster_id, threshold=0.9)

    # 이 클러스터의 입찰에서 추가 가능한 아이템 목록
    avail_items = []
    if dict(cluster).get("bid_id"):
        existing_ids = {dict(m)["catalog_item_id"] for m in members}
        all_items = list_submission_items_for_clustering(dict(cluster)["bid_id"])
        avail_items = [dict(i) for i in all_items
                       if i["item_id"] not in existing_ids]
    conn.close()

    tok = getattr(g, "auth_token", "") or ""
    return render_template("catalog/cluster_detail.html",
                           cluster=cluster, members=members,
                           high_conf=high_conf, avail_items=avail_items)


@bp.route("/clusters/<cluster_id>/accept", methods=["POST"])
@require_role("manager")
def accept_cluster(cluster_id):
    from extractors.catalog_clusterer import accept_cluster as do_accept
    from config import DB_PATH
    import sqlite3

    tok  = getattr(g, "auth_token", "") or ""
    auth_data = getattr(g, "auth_data", None) or {}
    uid  = auth_data.get("user_id", "")
    rep_name = request.form.get("representative_name", "").strip() or None

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        result = do_accept(conn, cluster_id, uid, rep_name)
        conn.close()
        flash(f"✅ 확정 — 대표 품목명: '{result['representative_name']}'", "success")
    except Exception as e:
        flash(f"❌ 확정 실패: {e}", "error")

    return redirect(url_for("catalog.clusters", _t=tok))


@bp.route("/clusters/<cluster_id>/hold", methods=["POST"])
@require_role("manager")
def hold_cluster(cluster_id):
    """클러스터 보류 (5-3)"""
    from extractors.catalog_clusterer import hold_cluster as do_hold
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""
    auth_data = getattr(g, "auth_data", None) or {}
    uid = auth_data.get("user_id", "")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    do_hold(conn, cluster_id, uid)
    conn.close()
    flash("클러스터가 보류 처리되었습니다.", "info")
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


@bp.route("/clusters/<cluster_id>/remove-member", methods=["POST"])
@require_role("manager")
def remove_cluster_member(cluster_id):
    """클러스터에서 특정 아이템 제외 (5-1)"""
    from extractors.catalog_clusterer import remove_member
    from config import DB_PATH
    import sqlite3

    tok     = getattr(g, "auth_token", "") or ""
    item_id = request.form.get("item_id", "").strip()

    if item_id:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        remove_member(conn, cluster_id, item_id)
        conn.close()
        flash("항목이 제외되었습니다.", "info")

    return redirect(url_for("catalog.cluster_detail",
                            cluster_id=cluster_id, _t=tok))


@bp.route("/clusters/<cluster_id>/add-member", methods=["POST"])
@require_role("manager")
def add_cluster_member(cluster_id):
    """클러스터에 아이템 수동 추가 (5-2)"""
    from extractors.catalog_clusterer import add_member
    from config import DB_PATH
    import sqlite3

    tok     = getattr(g, "auth_token", "") or ""
    item_id = request.form.get("item_id", "").strip()

    if item_id:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        add_member(conn, cluster_id, item_id)
        conn.close()
        flash("항목이 추가되었습니다.", "success")

    return redirect(url_for("catalog.cluster_detail",
                            cluster_id=cluster_id, _t=tok))
