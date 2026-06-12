from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import login_required, require_role
from db.queries import get_bid, list_submissions, list_deleted_submissions, update_submission

bp = Blueprint("bids", __name__)


@bp.route("/<bid_id>")
@login_required
def detail(bid_id):
    bid = get_bid(bid_id)
    if not bid:
        abort(404)
    submissions = list_submissions(bid_id)
    deleted_submissions = list_deleted_submissions(bid_id)
    return render_template("bids/detail.html", bid=bid,
                           submissions=submissions,
                           deleted_submissions=deleted_submissions)


@bp.route("/<bid_id>/status", methods=["POST"])
@require_role("manager")
def update_status(bid_id):
    bid = get_bid(bid_id)
    if not bid:
        abort(404)
    status = request.form.get("status")
    if status in ("open", "closed", "awarded", "cancelled"):
        from db.queries import update_submission  # 재사용 불가, 별도 함수 필요
        import sqlite3
        from config import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE bids SET status=?, updated_at=datetime('now') WHERE bid_id=?",
                     (status, bid_id))
        conn.commit()
        conn.close()
        flash("입찰 상태가 업데이트되었습니다.", "success")
    return redirect(url_for("bids.detail", bid_id=bid_id))
