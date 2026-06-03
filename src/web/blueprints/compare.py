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
