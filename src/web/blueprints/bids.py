from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import login_required, require_role
from db.queries import (get_bid, list_submissions, list_deleted_submissions,
                        update_submission, update_bid, get_submission,
                        vendor_name_exists)

bp = Blueprint("bids", __name__)



def _db_connect():
    """직접 DB 연결 — busy_timeout 포함 (worker와의 쓰기 경합 시 5초 대기)."""
    import sqlite3 as _sq
    from db.queries import DB_PATH as _DBP
    conn = _sq.connect(_DBP)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


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


@bp.route("/<bid_id>/rename", methods=["POST"])
@require_role("manager")
def rename_bid(bid_id):
    """입찰명 인라인 수정."""
    bid = get_bid(bid_id)
    if not bid:
        abort(404)
    name = request.form.get("name", "").strip()
    if not name:
        flash("입찰명을 입력하세요.", "error")
    else:
        update_bid(bid_id, name=name)
        flash("입찰명이 수정되었습니다.", "success")
    return redirect(url_for("bids.detail", bid_id=bid_id))


@bp.route("/submission/<submission_id>/rename", methods=["POST"])
@require_role("manager")
def rename_submission(submission_id):
    """제출업체명 인라인 수정. 같은 입찰 내 중복 방지."""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    subd = dict(sub)
    bid_id = subd.get("bid_id")
    vendor_name = request.form.get("vendor_name", "").strip()
    if not vendor_name:
        flash("업체명을 입력하세요.", "error")
    elif vendor_name == subd.get("vendor_name"):
        pass  # 변경 없음
    elif vendor_name_exists(bid_id, vendor_name, exclude_submission_id=submission_id):
        flash(f"같은 입찰에 이미 '{vendor_name}' 업체가 있습니다. 다른 이름을 사용하세요.",
              "error")
    else:
        update_submission(submission_id, vendor_name=vendor_name)
        flash("업체명이 수정되었습니다.", "success")
    return redirect(url_for("bids.detail", bid_id=bid_id))


@bp.route("/<bid_id>/hard-delete", methods=["POST"])
@require_role("admin")
def hard_delete(bid_id):
    """입찰 완전 삭제 — admin 전용, 3중 안전장치.

    ① 철회(cancelled) 상태에서만 허용 ② 입찰명 타이핑 확인
    ③ 단일 트랜잭션 cascade. 업로드 물리 파일은 보존."""
    bid = get_bid(bid_id)
    if not bid:
        abort(404)
    bd = dict(bid)
    if bd.get("status") != "cancelled":
        flash("완전 삭제는 '철회(cancelled)' 상태에서만 가능합니다. 먼저 철회하세요.",
              "error")
        return redirect(url_for("bids.detail", bid_id=bid_id))
    typed = request.form.get("confirm_name", "").strip()
    if typed != (bd.get("name") or "").strip():
        flash(f"입력한 이름이 현재 입찰명 '{bd.get('name')}'과(와) 달라 "
              f"삭제가 취소되었습니다. 최신 입찰명을 확인 후 다시 시도하세요.", "error")
        return redirect(url_for("bids.detail", bid_id=bid_id))
    from db.queries import hard_delete_bid
    n = hard_delete_bid(bid_id)
    flash(f"🗑 입찰 '{bd['name']}' 완전 삭제 완료 — 제출서 {n['submissions']}, "
          f"품목 {n['items']}, 클러스터 {n['clusters']} 삭제. (업로드 파일은 보존됨)",
          "success")
    return redirect(url_for("projects.detail", project_id=bd["project_id"]))


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
        conn = _db_connect()
        conn.execute("UPDATE bids SET status=?, updated_at=datetime('now') WHERE bid_id=?",
                     (status, bid_id))
        conn.commit()
        conn.close()
        flash("입찰 상태가 업데이트되었습니다.", "success")
    return redirect(url_for("bids.detail", bid_id=bid_id))
