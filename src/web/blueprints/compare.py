"""
비교 분석 Blueprint.

Phase 1+3:
  GET /compare/bid/<bid_id>          — 입찰 내 N개사 비교
  GET /compare/bid/<bid_id>/excel    — Excel 보고서 다운로드
  GET /compare/search?q=&project_id= — 품목명으로 가격 이력 검색
"""
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, abort, send_file, session, g, jsonify)
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import login_required
from db.queries import get_bid, compare_bid_submissions, cross_project_price, cross_bid_price

bp = Blueprint("compare", __name__)


# 임베딩 코사인 유사도가 이 값 이상이면 "병합 추천" 대상으로 표시
# (text-embedding-3-small 은 느슨히 관련된 항목도 0.45~0.5 를 주므로 0.55 로 상향)
_EMBED_SUGGEST_THRESHOLD = 0.55
# 임베딩 사용 불가 시 어휘 유사도(SequenceMatcher) 임계값
_LEXICAL_SUGGEST_THRESHOLD = 0.55


def _pick_openai_key() -> str:
    """임베딩에 쓸 유효한 OpenAI 키 선택.

    우선순위: (1) 로그인 사용자의 LLM 키가 OpenAI(sk-) 형식이면 그것,
    (2) 환경변수 OPENAI_API_KEY 가 sk- 형식이면 그것. 없으면 빈 문자열.
    (형식 검사로 잘못된 값에 대한 무의미한 401 호출을 방지)
    """
    try:
        from db.queries import get_user_llm_settings
        uid = session.get("user_id", "")
        if uid:
            s = get_user_llm_settings(uid)
            k = (s.get("api_key") or "").strip()
            if k.startswith("sk-"):
                return k
    except Exception:
        pass
    import os
    env_k = (os.environ.get("OPENAI_API_KEY", "") or "").strip()
    return env_k if env_k.startswith("sk-") else ""


def _pick_openai_conf():
    """임베딩용 (api_key, base_url, verify_ssl) 묶음 반환.

    게이트웨이(base_url) 설정이 있으면 함께 반환하여,
    임베딩도 게이트웨이로 시도하게 한다(없으면 폴백).
    """
    try:
        from db.queries import get_user_llm_settings
        uid = session.get("user_id", "")
        if uid:
            s = get_user_llm_settings(uid)
            k = (s.get("api_key") or "").strip()
            if k.startswith("sk-"):
                return (k, (s.get("base_url") or "").strip() or None,
                        s.get("verify_ssl", True))
    except Exception:
        pass
    import os
    env_k = (os.environ.get("OPENAI_API_KEY", "") or "").strip()
    if env_k.startswith("sk-"):
        return (env_k, None, True)
    return ("", None, True)


def _embed_texts(texts: list, api_key: str, base_url: str = None,
                 verify_ssl: bool = True):
    """OpenAI 임베딩으로 텍스트 벡터화. 실패 시 None 반환(어휘 폴백 신호).

    base_url(게이트웨이)이 설정된 경우:
      - 임베딩 모델(text-embedding-3-small)이 게이트웨이에 없을 수 있음.
      - 그 경우 조용히 None 반환 → 어휘 기반 유사도로 폴백.
      - 즉 임베딩 실패가 클러스터링 전체를 죽이지 않음.
    """
    if not api_key:
        return None
    try:
        from openai import OpenAI
        import httpx
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
        if not verify_ssl:
            kwargs["http_client"] = httpx.Client(verify=False)
        client = OpenAI(**kwargs)
        safe = [(t or " ")[:2000] for t in texts]
        resp = client.embeddings.create(
            model="text-embedding-3-small", input=safe
        )
        return [d.embedding for d in resp.data]
    except Exception:
        # 게이트웨이에 임베딩 모델이 없거나 연결 불가 → 어휘 폴백
        return None


def _cluster_similarities(for_id: str, text_by_cluster: dict):
    """현재 클러스터(for_id) 대비 각 클러스터의 유사도 dict + 사용한 방식 반환.

    1순위: OpenAI 임베딩 코사인(교차 언어 의미 유사도 — 예: lodging ↔ 숙박비).
    폴백:  어휘 기반 SequenceMatcher 비율.
    """
    ids = list(text_by_cluster.keys())
    if for_id not in ids or len(ids) < 2:
        return {}, "none"

    _ek, _ebu, _evs = _pick_openai_conf()
    vecs = _embed_texts([text_by_cluster[i] for i in ids], _ek, _ebu, _evs)
    if vecs:
        import math
        base = vecs[ids.index(for_id)]

        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            return dot / (na * nb) if na and nb else 0.0

        return {cid: cos(base, vecs[i]) for i, cid in enumerate(ids)}, "embedding"

    # ── 폴백: 어휘 유사도 ──
    from difflib import SequenceMatcher
    base_text = text_by_cluster[for_id]
    return (
        {cid: SequenceMatcher(None, base_text, t).ratio()
         for cid, t in text_by_cluster.items()},
        "lexical",
    )


@bp.route("/bid/<bid_id>/clusters-json")
@login_required
def clusters_json(bid_id):
    """입찰 내 클러스터 목록(JSON). `?for=<cluster_id>` 지정 시 해당 클러스터와의
    유사도를 계산하여 병합 추천 대상(suggested)을 표시하고 유사도순 정렬."""
    from db.queries import list_clusters
    from config import DB_PATH
    from collections import defaultdict
    import sqlite3

    for_id = request.args.get("for", "").strip()

    clusters = list_clusters(bid_id=bid_id)

    # 클러스터별 멤버 품명/스펙 수집 (유사도 텍스트 구성용)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT cm.cluster_id,
               si.name_normalized, si.name_raw, si.spec
        FROM catalog_cluster_members cm
        JOIN submission_items si ON cm.catalog_item_id = si.item_id
        JOIN catalog_clusters  cl ON cm.cluster_id = cl.cluster_id
        WHERE cl.bid_id = ?
    """, (bid_id,)).fetchall()
    conn.close()

    names_by_cluster = defaultdict(list)
    specs_by_cluster = defaultdict(list)
    for r in rows:
        nm = (r["name_normalized"] or r["name_raw"] or "").strip()
        if nm:
            names_by_cluster[r["cluster_id"]].append(nm)
        sp = (r["spec"] or "").strip()
        if sp:
            specs_by_cluster[r["cluster_id"]].append(sp)

    out = []
    text_by_cluster = {}
    for c in clusters:
        cid = c["cluster_id"]
        rep = c["representative_name"] or ""
        mnames = names_by_cluster.get(cid, [])
        mspecs = specs_by_cluster.get(cid, [])
        text_by_cluster[cid] = " ".join([rep] + mnames + mspecs).strip() or rep or " "
        out.append({
            "cluster_id":          cid,
            "representative_name":  rep,
            "status":               c["status"],
            "member_count":         c["member_count"],
            "similarity":           None,
            "suggested":            False,
        })

    method = "none"
    if for_id and for_id in text_by_cluster:
        sims, method = _cluster_similarities(for_id, text_by_cluster)
        for o in out:
            if o["cluster_id"] != for_id and o["cluster_id"] in sims:
                o["similarity"] = round(float(sims[o["cluster_id"]]), 4)

        threshold = (_EMBED_SUGGEST_THRESHOLD if method == "embedding"
                     else _LEXICAL_SUGGEST_THRESHOLD)
        candidates = [
            o for o in out
            if o["cluster_id"] != for_id
            and o["status"] != "rejected"
            and o["similarity"] is not None
        ]
        candidates.sort(key=lambda o: o["similarity"], reverse=True)
        for o in candidates[:3]:
            if o["similarity"] >= threshold:
                o["suggested"] = True

    # 추천 → 유사도 높은 순 → 이름 순 정렬
    out.sort(key=lambda o: (
        not o["suggested"],
        -(o["similarity"] if o["similarity"] is not None else -1.0),
        o["representative_name"] or "",
    ))

    return jsonify({"clusters": out, "method": method, "for": for_id})


@bp.route("/bid/<bid_id>")
@login_required
def bid_compare(bid_id):
    """입찰 내 N개사 항목별 비교 (기존 기능 유지 + 비교 단위 묶음)"""
    bid = get_bid(bid_id)
    if not bid:
        abort(404)

    data = compare_bid_submissions(bid_id)

    if not data["vendors"]:
        flash("추출 완료된 제출서가 없습니다. 먼저 파일을 업로드하세요.", "warning")
        return redirect(url_for("bids.detail", bid_id=bid_id))

    return render_template("compare/bid.html", bid=bid, data=data)


@bp.route("/bid/<bid_id>/excel")
@login_required
def bid_excel(bid_id):
    """입찰 내 비교 Excel 보고서 다운로드"""
    bid = get_bid(bid_id)
    if not bid:
        abort(404)

    data = compare_bid_submissions(bid_id)
    if not data["vendors"]:
        flash("비교할 데이터가 없습니다.", "error")
        return redirect(url_for("compare.bid_compare", bid_id=bid_id))

    try:
        from reports.excel_report import generate_bid_report
        report_path = generate_bid_report(bid, data)
        return send_file(
            report_path,
            as_attachment=True,
            download_name=f"입찰비교_{bid['name']}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        flash(f"보고서 생성 실패: {e}", "error")
        return redirect(url_for("compare.bid_compare", bid_id=bid_id))


@bp.route("/search")
@login_required
def search():
    """품목명으로 전체 가격 이력 검색 (입찰 간 비교)"""
    q          = request.args.get("q", "").strip()
    project_id = request.args.get("project_id", "").strip()
    results    = []

    if q:
        if project_id:
            results = cross_bid_price(project_id, q)
        else:
            results = cross_project_price(q)

    # 프로젝트 목록 (필터용)
    from db.queries import list_projects
    projects = list_projects()

    return render_template("compare/search.html",
                           q=q, project_id=project_id,
                           results=results, projects=projects)


# ─── 비교 페이지 클러스터 액션 ───────────────────

@bp.route("/bid/<bid_id>/cluster/run", methods=["POST"])
def run_cluster_from_compare(bid_id):
    """비교 페이지에서 직접 클러스터링 실행"""
    from flask import g, session, current_app
    from auth.auth import require_role
    import threading, uuid as _uuid
    from db.queries import list_submission_items_for_clustering, get_user_llm_settings
    from web.blueprints.catalog import _cluster_worker, _cluster_jobs

    tok = getattr(g, "auth_token", "") or ""
    uid = session.get("user_id", "")

    llm = get_user_llm_settings(uid)
    if not llm.get("api_key"):
        flash("API 키가 설정되지 않았습니다. ⚙ 내 프로필에서 설정하세요.", "error")
        return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))

    # 폼에서 provider/model 오버라이드 (없으면 프로필 기본값 사용)
    override_provider = request.form.get("provider_id", "").strip()
    override_model    = request.form.get("model", "").strip()
    if override_provider:
        llm = {**llm, "provider": override_provider,
               "model": override_model or None}

    items_raw = [dict(i) for i in list_submission_items_for_clustering(bid_id)]
    if len(items_raw) < 2:
        flash("추출 완료 견적서가 2개 이상 필요합니다.", "warning")
        return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))

    job_id = str(_uuid.uuid4())
    app = current_app._get_current_object()
    threading.Thread(
        target=_cluster_worker,
        args=(app, job_id, bid_id, items_raw, llm),
        daemon=True,
    ).start()

    return redirect(url_for("catalog.cluster_progress",
                            job_id=job_id, bid_id=bid_id,
                            return_to=bid_id, _t=tok))


@bp.route("/bid/<bid_id>/cluster/<cluster_id>/accept", methods=["POST"])
def accept_cluster_from_compare(bid_id, cluster_id):
    from flask import g
    from extractors.catalog_clusterer import accept_cluster as do_accept
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""
    uid = session.get("user_id", "")
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

    return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))


@bp.route("/bid/<bid_id>/cluster/<cluster_id>/hold", methods=["POST"])
def hold_cluster_from_compare(bid_id, cluster_id):
    from flask import g
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
    return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))


@bp.route("/bid/<bid_id>/cluster/<cluster_id>/reject", methods=["POST"])
def reject_cluster_from_compare(bid_id, cluster_id):
    from flask import g
    from extractors.catalog_clusterer import reject_cluster as do_reject
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""
    uid = session.get("user_id", "")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    do_reject(conn, cluster_id, uid)
    conn.close()
    flash("거부 처리되었습니다.", "info")
    return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))


@bp.route("/bid/<bid_id>/cluster/move-items", methods=["POST"])
def move_items_to_cluster(bid_id):
    """
    선택한 아이템(item_id 목록)을 클러스터로 이동.
    cluster_id = 'new' 이면 새 클러스터 생성.
    """
    from flask import g
    from extractors.catalog_clusterer import (move_items_to_cluster as do_move,
                                               create_cluster_from_items)
    from config import DB_PATH
    import sqlite3, json

    tok = getattr(g, "auth_token", "") or ""
    uid = session.get("user_id", "")

    cluster_id   = request.form.get("cluster_id", "").strip()
    rep_name     = request.form.get("representative_name", "").strip()
    category     = request.form.get("category", "").strip()  # 새 클러스터 카테고리
    item_ids_raw = request.form.getlist("item_ids")
    if not item_ids_raw:
        try:
            item_ids_raw = json.loads(request.form.get("item_ids_json", "[]"))
        except Exception:
            item_ids_raw = []

    item_ids = [i for i in item_ids_raw if i]

    if not item_ids:
        flash("이동할 항목을 선택하세요.", "error")
        return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(item_ids))

        # 이동 전: 각 아이템이 속한 소스 클러스터 기록
        src_rows = conn.execute(
            f"SELECT DISTINCT cluster_id FROM catalog_cluster_members "
            f"WHERE catalog_item_id IN ({placeholders})",
            item_ids
        ).fetchall()
        src_cluster_ids = [r["cluster_id"] for r in src_rows]

        if cluster_id == "new":
            if not rep_name:
                row = conn.execute(
                    "SELECT name_normalized, name_raw FROM submission_items WHERE item_id=?",
                    (item_ids[0],)
                ).fetchone()
                rep_name = (row["name_normalized"] or row["name_raw"]) if row else "신규 클러스터"

            new_cid = create_cluster_from_items(
                conn, bid_id, item_ids, rep_name, uid, category=category or None
            )
            flash(f"✅ 새 클러스터 '{rep_name}' 생성 ({len(item_ids)}개 항목)", "success")

        else:
            # 기존 클러스터에 추가
            n = do_move(conn, cluster_id, item_ids)
            cl = conn.execute(
                "SELECT representative_name FROM catalog_clusters WHERE cluster_id=?",
                (cluster_id,)
            ).fetchone()
            name = cl["representative_name"] if cl else cluster_id
            flash(f"✅ '{name}'에 {n}개 항목 추가됨", "success")

        # ① 카테고리 변경 — submission_items.category 업데이트
        #    (cl_cat은 멤버 다수결로 결정되므로 실제 category 컬럼을 바꿔야 반영됨)
        if category:
            conn.execute(
                f"UPDATE submission_items SET category = ? WHERE item_id IN ({placeholders})",
                [category] + item_ids
            )

        # ② 이동 후 멤버 없는 소스 클러스터 삭제
        dest_id = new_cid if cluster_id == "new" else cluster_id
        for src_cid in src_cluster_ids:
            if src_cid == dest_id:
                continue
            remaining = conn.execute(
                "SELECT COUNT(*) as cnt FROM catalog_cluster_members WHERE cluster_id = ?",
                (src_cid,)
            ).fetchone()
            if remaining and remaining["cnt"] == 0:
                conn.execute(
                    "DELETE FROM catalog_clusters WHERE cluster_id = ?", (src_cid,)
                )

        conn.commit()
        conn.close()

    except Exception as e:
        flash(f"❌ 이동 실패: {e}", "error")

    return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))


@bp.route("/bid/<bid_id>/cluster/merge", methods=["POST"])
@login_required
def merge_clusters_from_compare(bid_id):
    """선택한 여러 클러스터를 대상(target) 클러스터로 병합."""
    from flask import g
    from extractors.catalog_clusterer import merge_clusters
    from config import DB_PATH
    import sqlite3, json

    tok = getattr(g, "auth_token", "") or ""
    uid = session.get("user_id", "")

    target_id = request.form.get("target_cluster_id", "").strip()
    ids_raw   = request.form.getlist("cluster_ids")
    if not ids_raw:
        try:
            ids_raw = json.loads(request.form.get("cluster_ids_json", "[]"))
        except Exception:
            ids_raw = []
    cluster_ids = [c for c in ids_raw if c]

    if len(cluster_ids) < 2:
        flash("병합하려면 2개 이상의 클러스터를 선택하세요.", "error")
        return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))
    if not target_id:
        flash("병합 대상 클러스터를 선택하세요.", "error")
        return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))

    src_ids = [c for c in cluster_ids if c != target_id]
    if not src_ids:
        flash("병합 대상 외 클러스터를 선택하세요.", "error")
        return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        # 보안: target + 모든 src 클러스터가 이 입찰(bid_id)에 속하는지 검증.
        # (URL bid_id를 위조해 타 입찰 클러스터를 병합하는 것을 차단)
        all_ids = [target_id] + src_ids
        placeholders = ",".join("?" * len(all_ids))
        owned = {
            r["cluster_id"] for r in conn.execute(
                f"SELECT cluster_id FROM catalog_clusters "
                f"WHERE cluster_id IN ({placeholders}) AND bid_id = ?",
                (*all_ids, bid_id),
            ).fetchall()
        }
        if not owned.issuperset(all_ids):
            conn.close()
            flash("❌ 이 입찰에 속하지 않은 클러스터는 병합할 수 없습니다.", "error")
            return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))

        result = merge_clusters(conn, target_id, src_ids, uid)
        conn.close()
        flash(
            f"✅ {result['merged_cluster_count']}개 클러스터 병합 완료 "
            f"({result['merged_item_count']}개 항목 이전)", "success"
        )
    except Exception as e:
        flash(f"❌ 병합 실패: {e}", "error")

    return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))


@bp.route("/bid/<bid_id>/cluster/reset", methods=["POST"])
def reset_clusters_from_compare(bid_id):
    """비교 페이지에서 클러스터 전체 리셋"""
    from flask import g
    from extractors.catalog_clusterer import reset_bid_clusters
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        n = reset_bid_clusters(conn, bid_id)
        conn.close()
        flash(f"✅ {n}개 클러스터가 초기화되었습니다.", "success")
    except Exception as e:
        flash(f"❌ 초기화 실패: {e}", "error")

    return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))


@bp.route("/bid/<bid_id>/cluster/<cluster_id>/reopen", methods=["POST"])
def reopen_cluster_from_compare(bid_id, cluster_id):
    """비교 페이지에서 클러스터 재검토 처리"""
    from flask import g
    from extractors.catalog_clusterer import reopen_cluster as do_reopen
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""
    uid = session.get("user_id", "")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        do_reopen(conn, cluster_id, uid)
        conn.close()
        flash("클러스터를 검토 대기로 되돌렸습니다.", "info")
    except Exception as e:
        flash(f"❌ 실패: {e}", "error")

    return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))


@bp.route("/bid/<bid_id>/cluster/<cluster_id>/delete", methods=["POST"])
def delete_cluster_from_compare(bid_id, cluster_id):
    """비교 페이지에서 클러스터 삭제"""
    from flask import g
    from extractors.catalog_clusterer import delete_cluster as do_delete
    from config import DB_PATH
    import sqlite3

    tok = getattr(g, "auth_token", "") or ""

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        do_delete(conn, cluster_id)
        conn.close()
        flash("클러스터가 삭제되었습니다.", "info")
    except Exception as e:
        flash(f"❌ 삭제 실패: {e}", "error")

    return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))


@bp.route("/bid/<bid_id>/cluster/exclude-items", methods=["POST"])
def exclude_items_from_cluster(bid_id):
    """클러스터에서 선택 항목 제외 → 미분류로 복귀 (catalog_cluster_members 삭제)"""
    from flask import g
    from config import DB_PATH
    import sqlite3, json

    tok = getattr(g, "auth_token", "") or ""

    item_ids_raw = request.form.getlist("item_ids")
    if not item_ids_raw:
        try:
            item_ids_raw = json.loads(request.form.get("item_ids_json", "[]"))
        except Exception:
            item_ids_raw = []
    item_ids = [i for i in item_ids_raw if i]

    if not item_ids:
        flash("제외할 항목을 선택하세요.", "error")
        return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(item_ids))
        conn.execute(f"""
            DELETE FROM catalog_cluster_members
            WHERE catalog_item_id IN ({placeholders})
        """, item_ids)
        # submission_items의 match_status도 해제 (NOT NULL이므로 'unmatched'으로)
        conn.execute(f"""
            UPDATE submission_items
            SET catalog_item_id = NULL, match_status = 'unmatched'
            WHERE item_id IN ({placeholders})
        """, item_ids)
        conn.commit()
        conn.close()
        flash(f"✅ {len(item_ids)}개 항목을 미분류로 제외했습니다.", "success")
    except Exception as e:
        flash(f"❌ 제외 실패: {e}", "error")

    return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))
