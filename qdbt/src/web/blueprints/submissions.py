"""
제출서 업로드 + 추출 처리 Blueprint.

업로드 즉시 DB 레코드(pending) 생성 → 백그라운드 처리 → done/failed.
현재는 동기 처리 (Flask 단일 스레드이지만 threaded=True로 실행).
"""
import os
import tempfile
from pathlib import Path
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, abort, jsonify, session, send_file, g)
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import login_required, require_role
from db.queries import (get_bid, get_submission, get_items,
                        create_submission, update_submission,
                        reset_submission, soft_delete_submission,
                        restore_submission)
from config import ALLOWED_EXTENSIONS

bp = Blueprint("submissions", __name__)


@bp.route("/upload/<bid_id>", methods=["GET", "POST"])
@require_role("manager")
def upload(bid_id):
    bid = get_bid(bid_id)
    if not bid:
        abort(404)

    if request.method == "GET":
        return render_template("submissions/upload.html", bid=bid)

    # ── POST: 파일 업로드 처리 ──────────────────
    vendor_name = request.form.get("vendor_name", "").strip()
    file = request.files.get("file")

    if not vendor_name:
        flash("업체명을 입력하세요.", "error")
        return redirect(request.url)
    if not file or file.filename == "":
        flash("파일을 선택하세요.", "error")
        return redirect(request.url)

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        flash(f"지원하지 않는 형식: {suffix}", "error")
        return redirect(request.url)

    # 임시 저장
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        from extractors.pipeline import save_upload, run_extraction, PipelineError
        from db.queries import get_user_llm_settings

        # 업로드한 사용자의 LLM 설정 조회 (provider + model + api_key)
        llm = get_user_llm_settings(session.get("user_id", ""))

        saved = save_upload(tmp_path, file.filename)

        sid = create_submission(
            bid_id=bid_id,
            vendor_name=vendor_name,
            file_name=file.filename,
            file_path=str(saved),
            file_format=suffix.lstrip("."),
            uploaded_by=session.get("user_id"),
        )

        result = run_extraction(
            sid, saved, vendor_name,
            api_key=llm["api_key"],
            provider_id=llm["provider"],
            model=llm["model"],
        )

        flash(
            f"✅ '{vendor_name}' 제출서가 처리되었습니다. "
            f"({result['n_items']}개 항목, "
            f"공급가액 {result['subtotal']:,.0f}원)",
            "success",
        )
        for w in result.get("warnings", [])[:3]:
            flash(f"⚠️ {w}", "warning")

        return redirect(url_for("bids.detail", bid_id=bid_id))

    except Exception as e:
        flash(f"❌ 처리 실패: {e}", "error")
        return redirect(request.url)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@bp.route("/<submission_id>")
@login_required
def detail(submission_id):
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    items = get_items(submission_id)
    return render_template("submissions/detail.html", sub=sub, items=items)


@bp.route("/<submission_id>/file")
@login_required
def download_file(submission_id):
    """원본 파일 다운로드/열기"""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    file_path = Path(sub["file_path"]) if dict(sub).get("file_path") else None
    if not file_path or not file_path.exists():
        abort(404, description="파일을 찾을 수 없습니다.")
    return send_file(
        file_path,
        as_attachment=False,          # 브라우저에서 바로 열기 (PDF 등)
        download_name=sub["file_name"],
    )


def _extraction_worker(app, submission_id, file_path, vendor_name, llm):
    """백그라운드 스레드에서 LLM 추출 실행"""
    from extractors.pipeline import run_extraction, PipelineError
    with app.app_context():
        try:
            run_extraction(
                submission_id, file_path, vendor_name,
                api_key=llm["api_key"],
                provider_id=llm["provider"],
                model=llm["model"],
            )
        except Exception:
            pass  # run_extraction already writes failed status to DB


@bp.route("/<submission_id>/extract", methods=["POST"])
@require_role("manager")
def extract(submission_id):
    """이미 등록된 제출서에 대해 LLM 추출을 백그라운드로 실행"""
    import threading
    from pathlib import Path
    from flask import current_app
    from db.queries import get_user_llm_settings

    sub = get_submission(submission_id)
    if not sub:
        abort(404)

    tok = getattr(g, "auth_token", "") or ""

    if not sub["file_path"]:
        flash("파일 경로가 없습니다. 다시 업로드하세요.", "error")
        return redirect(url_for("submissions.detail", submission_id=submission_id, _t=tok))

    llm = get_user_llm_settings(session.get("user_id", ""))
    if not llm.get("api_key"):
        flash("API 키가 설정되지 않았습니다. ⚙ 내 프로필에서 먼저 API 키를 입력하세요.", "error")
        return redirect(url_for("submissions.detail", submission_id=submission_id, _t=tok))

    # 폼에서 provider/model 오버라이드 (없으면 프로필 기본값 사용)
    override_provider = request.form.get("provider_id", "").strip()
    override_model    = request.form.get("model", "").strip()
    if override_provider:
        llm = {**llm, "provider": override_provider,
               "model": override_model or None}

    update_submission(submission_id, extraction_status="processing")

    app = current_app._get_current_object()
    t = threading.Thread(
        target=_extraction_worker,
        args=(app, submission_id, Path(sub["file_path"]), sub["vendor_name"], llm),
        daemon=True,
    )
    t.start()

    return redirect(url_for("submissions.detail", submission_id=submission_id, _t=tok))


@bp.route("/<submission_id>/status.json")
@login_required
def status_json(submission_id):
    """추출 상태 폴링용 JSON 엔드포인트"""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    sub = dict(sub)
    return jsonify({
        "status": sub.get("extraction_status"),
        "subtotal": sub.get("subtotal_excl_vat"),
        "grand_total": sub.get("grand_total"),
        "error": sub.get("extraction_error"),
    })


@bp.route("/<submission_id>/items.json")
@login_required
def items_json(submission_id):
    items = get_items(submission_id)
    return jsonify([dict(i) for i in items])


@bp.route("/<submission_id>/match", methods=["GET", "POST"])
@require_role("manager")
def match(submission_id):
    """LLM 매칭 실행 + 검수 화면"""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)

    from db.queries import (get_items_with_match, get_match_summary,
                             get_user_llm_settings, list_catalog_items)
    from config import DB_PATH
    import sqlite3

    if request.method == "POST" and request.form.get("action") == "run_match":
        # LLM 매칭 실행
        llm = get_user_llm_settings(session.get("user_id", ""))
        if not llm["api_key"]:
            flash("API 키가 설정되지 않았습니다. 프로필에서 설정하세요.", "error")
            return redirect(url_for("submissions.match", submission_id=submission_id))

        # 폼에서 provider/model 오버라이드
        override_provider = request.form.get("provider_id", "").strip()
        override_model    = request.form.get("model", "").strip()
        if override_provider:
            llm = {**llm, "provider": override_provider,
                   "model": override_model or None}

        try:
            from extractors.matcher import run_llm_matching, save_match_suggestions
            items_raw = get_items(submission_id)
            catalog_raw = list_catalog_items()

            # sqlite3.Row → dict 변환
            items = [dict(i) for i in items_raw]
            catalog = [dict(c) for c in catalog_raw]

            matches = run_llm_matching(
                items, catalog,
                api_key=llm["api_key"],
                provider_id=llm["provider"],
                model=llm["model"],
            )

            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            save_match_suggestions(conn, matches)
            conn.close()

            n_matched = sum(1 for m in matches if m.get("catalog_item_id"))
            flash(
                f"✅ LLM 매칭 완료 — {len(matches)}개 중 {n_matched}개 매칭 추천. "
                "아래에서 검수 후 확정하세요.",
                "success"
            )
        except Exception as e:
            flash(f"❌ 매칭 실패: {e}", "error")

        return redirect(url_for("submissions.match", submission_id=submission_id))

    # GET: 검수 화면
    items = get_items_with_match(submission_id)
    summary = get_match_summary(submission_id)
    catalog_all = list_catalog_items()

    return render_template("submissions/match.html",
                           sub=sub, items=items,
                           summary=summary, catalog_all=catalog_all)


@bp.route("/<submission_id>/match/confirm", methods=["POST"])
@require_role("manager")
def confirm_match(submission_id):
    """매칭 확정 처리 (담당자가 개별 또는 전체 확정)"""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)

    from extractors.matcher import confirm_match as do_confirm
    from config import DB_PATH
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 폼 데이터: item_id별 catalog_item_id
    confirmed_count = 0
    form = request.form.to_dict()

    for key, value in form.items():
        if key.startswith("item_"):
            item_id = key[5:]  # "item_" 제거
            catalog_item_id = value if value else None
            do_confirm(conn, item_id, catalog_item_id, submission_id)
            confirmed_count += 1

    conn.close()

    flash(f"✅ {confirmed_count}개 항목 매칭이 확정되었습니다.", "success")
    return redirect(url_for("submissions.match", submission_id=submission_id))


@bp.route("/<submission_id>/reset", methods=["POST"])
@require_role("manager")
def reset(submission_id):
    """추출 데이터만 초기화 — 제출서 레코드 및 원본 파일은 유지"""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    tok = getattr(g, "auth_token", "") or ""
    reset_submission(submission_id)
    flash(f"'{sub['vendor_name']}' 추출 데이터가 초기화되었습니다. 재추출을 실행할 수 있습니다.", "info")
    return redirect(url_for("submissions.detail", submission_id=submission_id, _t=tok))


@bp.route("/<submission_id>/soft-delete", methods=["POST"])
@require_role("manager")
def soft_delete(submission_id):
    """소프트 삭제 — 데이터 보존, 목록에서 숨김"""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    bid_id = sub["bid_id"]
    tok = getattr(g, "auth_token", "") or ""
    soft_delete_submission(submission_id)
    flash(f"'{sub['vendor_name']}' 제출서가 숨김 처리되었습니다. 하단에서 복원할 수 있습니다.", "info")
    return redirect(url_for("bids.detail", bid_id=bid_id, _t=tok))


@bp.route("/<submission_id>/restore", methods=["POST"])
@require_role("manager")
def restore(submission_id):
    """소프트 삭제 복원"""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    bid_id = sub["bid_id"]
    tok = getattr(g, "auth_token", "") or ""
    restore_submission(submission_id)
    flash(f"'{sub['vendor_name']}' 제출서가 복원되었습니다.", "success")
    return redirect(url_for("bids.detail", bid_id=bid_id, _t=tok))


@bp.route("/<submission_id>/delete", methods=["POST"])
@require_role("manager")
def delete(submission_id):
    """영구 삭제 (복원 불가)"""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    bid_id = sub["bid_id"]
    tok = getattr(g, "auth_token", "") or ""

    import sqlite3
    from config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    # FK 자식 레코드 먼저 삭제 (price_history, catalog_suggestions → submission_items 참조)
    conn.execute("DELETE FROM price_history WHERE submission_id=?", (submission_id,))
    conn.execute("DELETE FROM catalog_suggestions WHERE submission_id=?", (submission_id,))
    conn.execute("DELETE FROM submission_items WHERE submission_id=?", (submission_id,))
    conn.execute("DELETE FROM submissions WHERE submission_id=?", (submission_id,))
    conn.commit()
    conn.close()

    flash(f"'{sub['vendor_name']}' 제출서가 완전히 삭제되었습니다.", "info")
    return redirect(url_for("bids.detail", bid_id=bid_id, _t=tok))
