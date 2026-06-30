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
    sort_by       = request.args.get("sort", "created")   # created | name_asc | name_desc
    cluster_list  = list_clusters(
        bid_id=bid_id_filter or None,
        status=status if status != "all" else None,
    )
    # 정렬
    if sort_by == "name_asc":
        cluster_list = sorted(cluster_list,
            key=lambda r: (dict(r).get("representative_name") or "").lower())
    elif sort_by == "name_desc":
        cluster_list = sorted(cluster_list,
            key=lambda r: (dict(r).get("representative_name") or "").lower(),
            reverse=True)
    summary    = get_cluster_summary(bid_id=bid_id_filter or None)
    avail_bids = list_bids_with_done_submissions()
    return render_template("catalog/clusters.html",
                           clusters=cluster_list, summary=summary,
                           avail_bids=avail_bids,
                           selected_status=status,
                           selected_bid=bid_id_filter,
                           selected_sort=sort_by)


import threading
_cluster_jobs = {}  # job_id → {'status': ..., 'message': ..., 'n': ...}
_active_cluster_bids = set()   # 현재 클러스터링 진행 중인 bid_id (동시 실행 방지)
_active_cluster_lock = threading.Lock()


def _cluster_worker(app, job_id, bid_id, items_raw, llm):
    """백그라운드에서 LLM 3단계 클러스터링 실행"""
    from extractors.catalog_clusterer import (
        run_clustering, save_clusters,
        run_unmatched_verification, apply_verification_results,
        run_cluster_validation, apply_validation_results,
    )
    from config import DB_PATH
    import sqlite3

    # 동시 실행 방지: 같은 입찰의 클러스터링이 이미 진행 중이면 중단
    with _active_cluster_lock:
        if bid_id in _active_cluster_bids:
            _cluster_jobs[job_id] = {
                "status": "error",
                "message": "이미 이 입찰의 클러스터링이 진행 중입니다. 완료 후 다시 시도하세요.",
                "n": 0,
            }
            return
        _active_cluster_bids.add(bid_id)

    _cluster_jobs[job_id] = {
        "status": "running", "phase": 1,
        "message": f"[1/3] 품목 {len(items_raw)}개 초기 클러스터 탐색 중...",
        "n": 0,
    }
    try:
        with app.app_context():
            # 이미 확정(accepted)된 클러스터에 속한 항목은 재클러스터링 대상에서 제외
            # → 확정 작업 보존 + 확정/미확정 간 중복 방지
            _pre = sqlite3.connect(DB_PATH)
            _pre.row_factory = sqlite3.Row
            accepted_ids = {
                r["catalog_item_id"]
                for r in _pre.execute("""
                    SELECT cm.catalog_item_id
                    FROM catalog_cluster_members cm
                    JOIN catalog_clusters cc ON cm.cluster_id = cc.cluster_id
                    WHERE cc.bid_id = ? AND cc.status = 'accepted'
                """, (bid_id,)).fetchall()
            }
            _pre.close()
            if accepted_ids:
                items_raw = [i for i in items_raw if i["item_id"] not in accepted_ids]

            # ── Phase 1: 초기 클러스터링 ──────────────────────────
            # 기존 카탈로그 품목 조회 (name_canonical + aliases → LLM 이름 우선 참조)
            _cat_conn = sqlite3.connect(DB_PATH)
            _cat_conn.row_factory = sqlite3.Row
            catalog_items_for_llm = [
                dict(r) for r in _cat_conn.execute("""
                    SELECT catalog_item_id, name_canonical, aliases
                    FROM catalog_items WHERE is_active = 1
                    ORDER BY name_canonical
                """).fetchall()
            ]
            _cat_conn.close()

            cluster_list = run_clustering(
                submission_items=items_raw,
                api_key=llm["api_key"],
                provider_id=llm["provider"],
                model=llm["model"],
                base_url=llm.get("base_url") or None,
                verify_ssl=llm.get("verify_ssl", True),
                catalog_items=catalog_items_for_llm or None,
            )
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row

            # 재실행 시 이전 미확정(pending/held) 클러스터를 먼저 제거 → 중복 누적 방지
            # (확정 클러스터는 보존; 위에서 해당 항목을 이미 제외함)
            _old = conn.execute("""
                SELECT cluster_id FROM catalog_clusters
                WHERE bid_id = ? AND status IN ('pending', 'held')
            """, (bid_id,)).fetchall()
            _old_ids = [r["cluster_id"] for r in _old]
            if _old_ids:
                _ph = ",".join("?" * len(_old_ids))
                conn.execute(
                    f"DELETE FROM catalog_cluster_members WHERE cluster_id IN ({_ph})",
                    _old_ids)
                conn.execute(
                    f"DELETE FROM catalog_clusters WHERE cluster_id IN ({_ph})",
                    _old_ids)
                conn.commit()

            n1 = save_clusters(conn, cluster_list, bid_id=bid_id)

            # ── Phase 2: 미분류 항목 재검증 ───────────────────────
            _cluster_jobs[job_id] = {
                "status": "running", "phase": 2,
                "message": f"[2/3] 미분류 항목 재검증 중... (1차: {n1}개 그룹)",
                "n": n1,
            }

            clustered_ids = {
                row["catalog_item_id"]
                for row in conn.execute("""
                    SELECT cm.catalog_item_id
                    FROM catalog_cluster_members cm
                    JOIN catalog_clusters cc ON cm.cluster_id = cc.cluster_id
                    WHERE cc.bid_id = ?
                """, (bid_id,)).fetchall()
            }
            unmatched = [i for i in items_raw if i["item_id"] not in clustered_ids]

            n2_added = 0
            if unmatched and cluster_list:
                existing_clusters = [
                    {
                        "cluster_id":         row["cluster_id"],
                        "representative_name": row["representative_name"],
                        "member_names":        (row["member_names"] or "").split(" | "),
                    }
                    for row in conn.execute("""
                        SELECT cc.cluster_id, cc.representative_name,
                               GROUP_CONCAT(si.name_raw, ' | ') as member_names
                        FROM catalog_clusters cc
                        JOIN catalog_cluster_members cm ON cm.cluster_id = cc.cluster_id
                        JOIN submission_items si ON si.item_id = cm.catalog_item_id
                        WHERE cc.bid_id = ? AND cc.status IN ('pending', 'held')
                        GROUP BY cc.cluster_id
                    """, (bid_id,)).fetchall()
                ]
                additions = run_unmatched_verification(
                    unmatched_items=unmatched,
                    existing_clusters=existing_clusters,
                    api_key=llm["api_key"],
                    provider_id=llm["provider"],
                    model=llm["model"],
                    base_url=llm.get("base_url") or None,
                    verify_ssl=llm.get("verify_ssl", True),
                )
                n2_added = apply_verification_results(conn, additions)

            # ── Phase 3: 클러스터 적정성 재검토 ───────────────────
            _cluster_jobs[job_id] = {
                "status": "running", "phase": 3,
                "message": f"[3/3] 클러스터 적정성 검토 중... (+{n2_added}개 추가됨)",
                "n": n1 + n2_added,
            }

            clusters_with_members = []
            for cl in conn.execute(
                "SELECT cluster_id, representative_name FROM catalog_clusters "
                "WHERE bid_id = ? AND status IN ('pending', 'held')",
                (bid_id,)
            ).fetchall():
                members = [
                    dict(m) for m in conn.execute("""
                        SELECT cm.catalog_item_id as item_id,
                               si.name_raw, si.name_normalized, s.vendor_name
                        FROM catalog_cluster_members cm
                        JOIN submission_items si ON si.item_id = cm.catalog_item_id
                        JOIN submissions s ON si.submission_id = s.submission_id
                        WHERE cm.cluster_id = ?
                    """, (cl["cluster_id"],)).fetchall()
                ]
                clusters_with_members.append({
                    "cluster_id":         cl["cluster_id"],
                    "representative_name": cl["representative_name"],
                    "members":            members,
                })

            validation_results = run_cluster_validation(
                clusters_with_members=clusters_with_members,
                api_key=llm["api_key"],
                provider_id=llm["provider"],
                model=llm["model"],
                base_url=llm.get("base_url") or None,
                verify_ssl=llm.get("verify_ssl", True),
            )
            val = apply_validation_results(conn, validation_results)
            conn.close()

            n_final = sqlite3.connect(DB_PATH).execute(
                "SELECT COUNT(*) FROM catalog_clusters WHERE bid_id = ?", (bid_id,)
            ).fetchone()[0]

            _cluster_jobs[job_id] = {
                "status":  "done",
                "phase":   3,
                "message": (
                    f"{n_final}개 그룹 확정 "
                    f"(1차 {n1}개 → +{n2_added}개 추가 → {val['dissolved']}개 해소)"
                ) if n_final else "유사 품목 없음",
                "n":                n_final,
                "bid_id":           bid_id,
                "phase1":           n1,
                "phase2_added":     n2_added,
                "phase3_dissolved": val["dissolved"],
            }
    except Exception as e:
        _cluster_jobs[job_id] = {
            "status": "error",
            "message": str(e),
            "n": 0,
        }
    finally:
        with _active_cluster_lock:
            _active_cluster_bids.discard(bid_id)


@bp.route("/clusters/run", methods=["POST"])
@require_role("manager")
def run_clusters():
    """LLM 클러스터링 — 백그라운드 실행 후 job_id 반환"""
    import uuid as _uuid
    from flask import current_app
    from db.queries import (list_submission_items_for_clustering,
                             get_user_llm_settings)

    tok    = getattr(g, "auth_token", "") or ""
    uid    = session.get("user_id", "")
    bid_id = request.form.get("bid_id", "").strip()

    if not bid_id:
        flash("분석할 입찰을 선택하세요.", "error")
        return redirect(url_for("catalog.clusters", _t=tok))

    llm = get_user_llm_settings(uid)
    if not llm.get("api_key"):
        flash("API 키가 설정되지 않았습니다. ⚙ 내 프로필에서 설정하세요.", "error")
        return redirect(url_for("catalog.clusters", _t=tok))

    # 폼에서 provider/model 오버라이드 (없으면 프로필 기본값 사용)
    override_provider = request.form.get("provider_id", "").strip()
    override_model    = request.form.get("model", "").strip()
    if override_provider:
        llm = {**llm, "provider": override_provider,
               "model": override_model or None}

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
    uid  = session.get("user_id", "")
    rep_name = request.form.get("representative_name", "").strip() or None

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        result = do_accept(conn, cluster_id, uid, rep_name)
        conn.close()
        msg = f"✅ '{result['representative_name']}' 확정"
        if result.get('catalog_item_id'):
            msg += f" — 카탈로그 등록 완료 (가격 이력 {result.get('price_history_count', 0)}건)"
        flash(msg, "success")
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
    uid = session.get("user_id", "")

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
    uid = session.get("user_id", "")

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


@bp.route("/clusters/<cluster_id>/rename", methods=["POST"])
@require_role("manager")
def rename_cluster_route(cluster_id):
    """클러스터 이름 수정 (확정 여부 무관)"""
    from extractors.catalog_clusterer import rename_cluster
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""
    uid = session.get("user_id", "")
    new_name = request.form.get("new_name", "").strip()

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rename_cluster(conn, cluster_id, new_name, uid)
        conn.close()
        flash(f"✅ 클러스터 이름이 '{new_name}'으로 변경됐습니다.", "success")
    except Exception as e:
        flash(f"❌ 이름 변경 실패: {e}", "error")

    return_bid = request.args.get("return_bid_id", "")
    if return_bid:
        return redirect(url_for("compare.bid_compare", bid_id=return_bid, _t=tok))
    return redirect(url_for("catalog.cluster_detail", cluster_id=cluster_id))


@bp.route("/clusters/<cluster_id>/reopen", methods=["POST"])
@require_role("manager")
def reopen_cluster_route(cluster_id):
    """확정/거부 클러스터를 검토 대기로 되돌림"""
    from extractors.catalog_clusterer import reopen_cluster
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""
    uid = session.get("user_id", "")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        reopen_cluster(conn, cluster_id, uid)
        conn.close()
        flash("클러스터를 검토 대기로 되돌렸습니다.", "info")
    except Exception as e:
        flash(f"❌ 실패: {e}", "error")

    return_bid = request.args.get("return_bid_id", "")
    if return_bid:
        return redirect(url_for("compare.bid_compare", bid_id=return_bid, _t=tok))
    return redirect(url_for("catalog.cluster_detail", cluster_id=cluster_id, _t=tok))


@bp.route("/clusters/<cluster_id>/delete", methods=["POST"])
@require_role("manager")
def delete_cluster_route(cluster_id):
    """클러스터 삭제"""
    from extractors.catalog_clusterer import delete_cluster
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        delete_cluster(conn, cluster_id)
        conn.close()
        flash("클러스터가 삭제되었습니다.", "info")
    except Exception as e:
        flash(f"❌ 삭제 실패: {e}", "error")

    return_bid = request.args.get("return_bid_id", "")
    if return_bid:
        return redirect(url_for("compare.bid_compare", bid_id=return_bid, _t=tok))
    return redirect(url_for("catalog.clusters", _t=tok))


@bp.route("/clusters/reset", methods=["POST"])
@require_role("manager")
def reset_clusters():
    """입찰의 모든 클러스터 리셋"""
    from extractors.catalog_clusterer import reset_bid_clusters
    from config import DB_PATH
    import sqlite3

    tok    = getattr(g, "auth_token", "") or ""
    bid_id = request.form.get("bid_id", "").strip()

    if not bid_id:
        flash("입찰을 선택하세요.", "error")
        return redirect(url_for("catalog.clusters", _t=tok))

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        n = reset_bid_clusters(conn, bid_id)
        conn.close()
        flash(f"✅ {n}개 클러스터가 초기화되었습니다.", "success")
    except Exception as e:
        flash(f"❌ 초기화 실패: {e}", "error")

    return redirect(url_for("catalog.clusters", bid_id=bid_id, _t=tok))


@bp.route("/clusters/bulk-action", methods=["POST"])
@require_role("manager")
def clusters_bulk_action():
    """클러스터 목록에서 일괄 작업 (병합, 삭제)"""
    from config import DB_PATH
    import sqlite3, json

    tok    = getattr(g, "auth_token", "") or ""
    uid    = session.get("user_id", "")
    action = request.form.get("action", "")
    bid_id = request.form.get("bid_id", "")

    cluster_ids_raw = request.form.getlist("cluster_ids")
    if not cluster_ids_raw:
        try:
            cluster_ids_raw = json.loads(request.form.get("cluster_ids_json", "[]"))
        except Exception:
            cluster_ids_raw = []
    cluster_ids = [i for i in cluster_ids_raw if i]

    if not cluster_ids:
        flash("작업할 클러스터를 선택하세요.", "error")
        return redirect(url_for("catalog.clusters", bid_id=bid_id, _t=tok))

    if action == "merge":
        target_id = request.form.get("target_cluster_id", "").strip()
        if not target_id:
            flash("병합 대상 클러스터를 선택하세요.", "error")
            return redirect(url_for("catalog.clusters", bid_id=bid_id, _t=tok))
        from extractors.catalog_clusterer import merge_clusters
        src_ids = [i for i in cluster_ids if i != target_id]
        if not src_ids:
            flash("병합할 클러스터(대상 외)를 선택하세요.", "error")
            return redirect(url_for("catalog.clusters", bid_id=bid_id, _t=tok))
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            result = merge_clusters(conn, target_id, src_ids, uid)
            conn.close()
            flash(f"✅ {result['merged_cluster_count']}개 클러스터 병합 완료 ({result['merged_item_count']}개 항목 이전)", "success")
        except Exception as e:
            flash(f"❌ 병합 실패: {e}", "error")

    elif action == "delete":
        from extractors.catalog_clusterer import delete_cluster
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            for cid in cluster_ids:
                delete_cluster(conn, cid)
            conn.close()
            flash(f"✅ {len(cluster_ids)}개 클러스터가 삭제되었습니다.", "success")
        except Exception as e:
            flash(f"❌ 삭제 실패: {e}", "error")

    else:
        flash("알 수 없는 작업입니다.", "error")

    return redirect(url_for("catalog.clusters", bid_id=bid_id, _t=tok))


@bp.route("/clusters/<cluster_id>/merge", methods=["POST"])
@require_role("manager")
def merge_clusters_route(cluster_id):
    """다른 클러스터를 현재 클러스터에 병합"""
    from extractors.catalog_clusterer import merge_clusters
    from config import DB_PATH
    import sqlite3, json

    tok = getattr(g, "auth_token", "") or ""
    uid = session.get("user_id", "")

    src_ids_raw = request.form.getlist("merge_cluster_ids")
    if not src_ids_raw:
        try:
            src_ids_raw = json.loads(request.form.get("merge_cluster_ids_json", "[]"))
        except Exception:
            src_ids_raw = []
    src_ids = [i for i in src_ids_raw if i and i != cluster_id]

    if not src_ids:
        flash("병합할 클러스터를 선택하세요.", "error")
        return redirect(url_for("catalog.cluster_detail", cluster_id=cluster_id))

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        result = merge_clusters(conn, cluster_id, src_ids, uid)
        conn.close()
        flash(
            f"✅ {result['merged_cluster_count']}개 클러스터 병합 완료 "
            f"({result['merged_item_count']}개 항목 이전)", "success"
        )
    except Exception as e:
        flash(f"❌ 병합 실패: {e}", "error")

    return_bid = request.args.get("return_bid_id", "")
    if return_bid:
        return redirect(url_for("compare.bid_compare", bid_id=return_bid, _t=tok))
    return redirect(url_for("catalog.cluster_detail", cluster_id=cluster_id))
