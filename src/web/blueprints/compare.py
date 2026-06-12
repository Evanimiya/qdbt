"""
비교 분석 Blueprint.

Phase 1+3:
  GET /compare/bid/<bid_id>          — 입찰 내 N개사 비교
  GET /compare/bid/<bid_id>/excel    — Excel 보고서 다운로드
  GET /compare/search?q=&project_id= — 품목명으로 가격 이력 검색
"""
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, abort, send_file)
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import login_required
from db.queries import get_bid, compare_bid_submissions, cross_project_price, cross_bid_price

bp = Blueprint("compare", __name__)


@bp.route("/bid/<bid_id>")
@login_required
def bid_compare(bid_id):
    """입찰 내 N개사 항목별 비교"""
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
    auth_data = getattr(g, "auth_data", None) or {}
    uid = auth_data.get("user_id", "")

    llm = get_user_llm_settings(uid)
    if not llm.get("api_key"):
        flash("API 키가 설정되지 않았습니다. ⚙ 내 프로필에서 설정하세요.", "error")
        return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))

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
    auth_data = getattr(g, "auth_data", None) or {}
    uid = auth_data.get("user_id", "")
    rep_name = request.form.get("representative_name", "").strip() or None

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        result = do_accept(conn, cluster_id, uid, rep_name)
        conn.close()
        flash(f"✅ '{result['representative_name']}' 확정됨", "success")
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
    auth_data = getattr(g, "auth_data", None) or {}
    uid = auth_data.get("user_id", "")

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
    auth_data = getattr(g, "auth_data", None) or {}
    uid = auth_data.get("user_id", "")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    do_reject(conn, cluster_id, uid)
    conn.close()
    flash("거부 처리되었습니다.", "info")
    return redirect(url_for("compare.bid_compare", bid_id=bid_id, _t=tok))
