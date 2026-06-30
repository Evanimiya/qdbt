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
                        restore_submission,
                        delete_submission_items, insert_items_bulk)
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

    # 2차 제출(탭 선택 후): 이미 저장된 파일 경로 + 선택 시트로 추출
    saved_path = request.form.get("saved_path", "").strip()
    selected_sheets = request.form.getlist("sheets")

    if saved_path:
        # ── 2차: 파일 등록만 (자동 추출 안 함). 추출은 열 매핑 화면에서. ──
        if not vendor_name:
            flash("업체명을 입력하세요.", "error")
            return redirect(request.url)
        saved = Path(saved_path)
        if not saved.exists():
            flash("업로드 파일을 찾을 수 없습니다. 다시 업로드하세요.", "error")
            return redirect(request.url)
        try:
            sid = create_submission(
                bid_id=bid_id, vendor_name=vendor_name,
                file_name=saved.name, file_path=str(saved),
                file_format=saved.suffix.lstrip("."),
                uploaded_by=session.get("user_id"),
            )
            # 추출 대기 상태로 둠 (사용자가 열 매핑 추출로 직접 추출)
            update_submission(sid, extraction_status="pending")
            is_xlsx = saved.suffix.lower() == ".xlsx"
            if is_xlsx:
                flash(f"✅ '{vendor_name}' 제출서가 등록되었습니다. "
                      f"'🧩 열 매핑 추출'로 추출하세요.", "success")
                # xlsx면 바로 열 매핑 화면으로
                return redirect(url_for("submissions.column_map", submission_id=sid))
            else:
                flash(f"✅ '{vendor_name}' 제출서가 등록되었습니다. "
                      f"제출서 상세에서 추출하세요.", "success")
                return redirect(url_for("submissions.detail", submission_id=sid))
        except Exception as e:
            flash(f"❌ 등록 실패: {e}", "error")
            return redirect(request.url)

    # ── 1차: 파일 업로드 ──
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
        from extractors.pipeline import save_upload
        saved = save_upload(tmp_path, file.filename)

        # 추출하지 않고 제출서만 등록 (추출은 열 매핑 화면에서 사용자가 직접)
        sid = create_submission(
            bid_id=bid_id,
            vendor_name=vendor_name,
            file_name=file.filename,
            file_path=str(saved),
            file_format=suffix.lstrip("."),
            uploaded_by=session.get("user_id"),
        )
        update_submission(sid, extraction_status="pending")

        if suffix == ".xlsx":
            flash(f"✅ '{vendor_name}' 제출서가 등록되었습니다. "
                  f"열을 지정해 추출하세요.", "success")
            return redirect(url_for("submissions.column_map", submission_id=sid))
        else:
            flash(f"✅ '{vendor_name}' 제출서가 등록되었습니다. "
                  f"제출서 상세에서 추출하세요.", "success")
            return redirect(url_for("submissions.detail", submission_id=sid))

    except Exception as e:
        flash(f"❌ 등록 실패: {e}", "error")
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

    # 비교 단위(group by) 레벨별 묶음
    from db.queries import group_items_by_level
    level = sub.get("compare_level", 2) if hasattr(sub, "get") else 2
    try:
        level = int(dict(sub).get("compare_level") or 2)
    except Exception:
        level = 2
    grouped = group_items_by_level(submission_id, level=level)

    # 비교 단위 트리 (그룹별 깊이용)
    from db.queries import build_items_tree
    import json as _json
    tree_data = build_items_tree(submission_id)
    # 저장된 compare_units (비교 단위 경로 집합)
    try:
        compare_units = _json.loads(dict(sub).get("compare_units") or "[]")
    except Exception:
        compare_units = []

    # xlsx면 시트 목록을 읽어 재추출 시 선택할 수 있게 전달
    sheet_list = []
    _subd = dict(sub)
    try:
        if _subd.get("file_format") == "xlsx" and _subd.get("file_path"):
            from pathlib import Path as _P
            if _P(_subd["file_path"]).exists():
                from parsers.parse_xlsx import get_xlsx_sheet_names
                sheet_list = get_xlsx_sheet_names(_subd["file_path"])
    except Exception:
        sheet_list = []

    # 재추출 시 지난번 읽은 시트만 기본 선택하기 위해
    try:
        prev_sheets = _json.loads(dict(sub).get("extracted_sheets") or "[]")
    except Exception:
        prev_sheets = []

    return render_template("submissions/detail.html", sub=sub, items=items,
                           sheet_list=sheet_list, prev_sheets=prev_sheets,
                           grouped=grouped, compare_level=level,
                           tree_json=_json.dumps(tree_data, ensure_ascii=False),
                           compare_units_json=_json.dumps(compare_units, ensure_ascii=False))


@bp.route("/<submission_id>/compare-level", methods=["POST"])
@require_role("manager")
def set_compare_level(submission_id):
    """비교 단위(group by) 레벨 변경 + 저장."""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    try:
        level = int(request.form.get("level", 2))
    except (ValueError, TypeError):
        level = 2
    level = max(1, min(level, 6))  # 1~6 범위
    update_submission(submission_id, compare_level=level)
    return redirect(url_for("submissions.detail", submission_id=submission_id))


@bp.route("/<submission_id>/compare-units", methods=["POST"])
@require_role("manager")
def set_compare_units(submission_id):
    """비교 단위 경로 집합 저장 (트리에서 펼침으로 정한 그룹별 깊이)."""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    import json as _json
    raw = request.get_json(silent=True) or {}
    units = raw.get("units", [])
    if not isinstance(units, list):
        units = []
    update_submission(submission_id, compare_units=_json.dumps(units, ensure_ascii=False))
    return jsonify({"ok": True, "count": len(units)})


@bp.route("/<submission_id>/grouped.json")
@login_required
def grouped_json(submission_id):
    """레벨별 묶음 데이터 (AJAX로 레벨 즉시 변경용)."""
    from db.queries import group_items_by_level
    try:
        level = int(request.args.get("level", 2))
    except (ValueError, TypeError):
        level = 2
    result = group_items_by_level(submission_id, level=max(1, min(level, 6)))
    # members는 직렬화 가능하게 정리
    out = {
        "level": result["level"],
        "total": result["total"],
        "max_available_level": result["max_available_level"],
        "groups": [
            {
                "key": g["key"], "label": g["label"],
                "amount": g["amount"], "n_items": g["n_items"],
                "max_depth": g["max_depth"],
                "members": [
                    {"name": m.get("name_normalized") or m.get("name_raw") or "",
                     "amount": m.get("amount"), "qty": m.get("quantity"),
                     "unit": m.get("unit"), "line_no": m.get("line_no"),
                     "path": m.get("path")}
                    for m in g["members"]
                ],
            }
            for g in result["groups"]
        ],
    }
    return jsonify(out)


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


def _extraction_worker(app, submission_id, file_path, vendor_name, llm,
                       sheet_names=None):
    """백그라운드 스레드에서 LLM 추출 실행"""
    from extractors.pipeline import run_extraction, PipelineError
    with app.app_context():
        try:
            run_extraction(
                submission_id, file_path, vendor_name,
                api_key=llm["api_key"],
                provider_id=llm["provider"],
                model=llm["model"],
                base_url=llm.get("base_url") or None,
                verify_ssl=llm.get("verify_ssl", True),
                sheet_names=sheet_names,
            )
        except Exception as e:
            # 스레드에서 조용히 죽는 것 방지: 원인을 터미널에 출력
            import traceback, sys
            print(f"[_extraction_worker] 추출 실패 (submission={submission_id}): "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc()
            # status를 failed로 기록 (run_extraction이 이미 했을 수 있지만 보강)
            try:
                update_submission(submission_id, extraction_status="failed",
                                  extraction_error=str(e)[:500])
            except Exception:
                pass


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

    # 폼에서 선택한 시트 (xlsx 재추출 시). 없으면 전체.
    selected_sheets = request.form.getlist("sheets") or None

    # 읽은 시트를 기억 (재추출 시 그 시트만 기본 선택하기 위해)
    import json as _json
    if selected_sheets:
        update_submission(submission_id,
                          extracted_sheets=_json.dumps(selected_sheets, ensure_ascii=False))

    app = current_app._get_current_object()
    t = threading.Thread(
        target=_extraction_worker,
        args=(app, submission_id, Path(sub["file_path"]), sub["vendor_name"], llm),
        kwargs={"sheet_names": selected_sheets},
        daemon=True,
    )
    t.start()

    return redirect(url_for("submissions.detail", submission_id=submission_id, _t=tok))


@bp.route("/<submission_id>/cancel", methods=["POST"])
@require_role("manager")
def cancel_extraction(submission_id):
    """진행 중인 추출을 중지(취소) 표시.

    실제 백그라운드 작업을 강제 종료하진 못하지만, 상태를 'failed'로
    바꿔 화면 폴링을 멈추고 사용자가 빠져나올 수 있게 한다.
    (멈춘 추출이 끝나도 done으로 못 바꾸도록 cancelled 표시)
    """
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    update_submission(
        submission_id,
        extraction_status="failed",
        extraction_error="사용자가 추출을 중지했습니다.",
    )
    flash("추출을 중지했습니다. 다시 시도하거나 파일을 확인하세요.", "info")
    return redirect(url_for("submissions.detail", submission_id=submission_id))


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


# ══════════════════════════════════════════════════════════
# 코드 기반 추출 (새 아키텍처): 인터랙티브 열 매핑 화면
# ══════════════════════════════════════════════════════════

@bp.route("/<submission_id>/map", methods=["GET"])
@require_role("manager")
def column_map(submission_id):
    """인터랙티브 열 매핑 화면 — 엑셀을 표로 보여주고 열 역할 지정 + 비교단위 선택."""
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    subd = dict(sub)
    fpath = subd.get("file_path")
    if not fpath or subd.get("file_format") != "xlsx":
        flash("xlsx 파일만 열 매핑 추출이 가능합니다.", "warning")
        return redirect(url_for("submissions.detail", submission_id=submission_id))

    from pathlib import Path as _P
    if not _P(fpath).exists():
        flash("파일을 찾을 수 없습니다.", "error")
        return redirect(url_for("submissions.detail", submission_id=submission_id))

    # 시트 선택 (쿼리파라미터 or 첫 시트)
    from parsers.parse_xlsx import get_xlsx_sheet_names
    from extractors.extract_by_mapping import read_grid, suggest_column_mapping
    try:
        sheets = get_xlsx_sheet_names(fpath)
        sheet = request.args.get("sheet") or (sheets[0] if sheets else None)
        grid_data = read_grid(fpath, sheet)
        suggestion = suggest_column_mapping(fpath, sheet)
    except Exception as e:
        # BadZipFile 등: 정상 xlsx가 아니거나 보안/암호화/손상
        ename = type(e).__name__
        if "BadZipFile" in ename or "zip" in str(e).lower():
            msg = ("이 파일을 열 수 없습니다. xlsx 형식이 아니거나 "
                   "보안(암호화)이 걸려 있거나 손상된 파일일 수 있습니다. "
                   "엑셀에서 파일을 열어 '다른 이름으로 저장 → .xlsx'로 "
                   "다시 저장한 뒤 업로드해 보세요.")
        else:
            msg = f"파일을 읽는 중 오류가 발생했습니다: {ename}"
        flash(msg, "error")
        return redirect(url_for("submissions.detail", submission_id=submission_id))

    # 저장된 매핑 설정 (재진입 시 복원)
    try:
        saved_config = _json.loads(subd.get("map_config") or "null")
    except Exception:
        saved_config = None
    # 저장된 시트가 있으면 그 시트로 (쿼리파라미터 우선)
    if saved_config and not request.args.get("sheet") and saved_config.get("sheet"):
        sheet = saved_config["sheet"]
        try:
            grid_data = read_grid(fpath, sheet)
            suggestion = suggest_column_mapping(fpath, sheet)
        except Exception:
            pass

    return render_template("submissions/column_map.html",
                           sub=subd, sheets=sheets, current_sheet=sheet,
                           grid_json=_json.dumps(grid_data, ensure_ascii=False),
                           suggest_json=_json.dumps(suggestion, ensure_ascii=False),
                           saved_config_json=_json.dumps(saved_config, ensure_ascii=False))


@bp.route("/<submission_id>/map/extract", methods=["POST"])
@require_role("manager")
def column_map_extract(submission_id):
    """확인된 열 매핑으로 코드 추출 실행 + 비교 단위 저장."""
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    subd = dict(sub)
    fpath = subd.get("file_path")

    payload = request.get_json(silent=True) or {}
    sheet = payload.get("sheet")
    # mapping: {"2":"cat1", ...} → {int: role}
    mapping = {int(k): v for k, v in (payload.get("mapping") or {}).items()
               if v and v != "ignore"}
    header_row = int(payload.get("header_row") or 1)
    compare_units = payload.get("compare_units") or []
    excluded_rows = set(payload.get("excluded_rows") or [])
    nego_rows = set(payload.get("nego_rows") or [])

    from extractors.extract_by_mapping import extract_by_mapping
    try:
        result = extract_by_mapping(fpath, sheet, mapping, header_row,
                                    excluded_rows=excluded_rows,
                                    nego_rows=nego_rows)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 200

    items = result["items"]
    # DB 저장 (기존 추출 항목 교체, 단 수기 nego는 보존)
    from db.queries import delete_submission_items, insert_items_bulk, get_items
    delete_submission_items(submission_id, keep_nego=True)
    insert_items_bulk(submission_id, items)

    # 공급가액 = 잎(헤더/소계 제외) amount 합. 트리 합계와 동일 방식.
    # special nego는 음수로 저장돼 있어 합산 시 자동 차감됨.
    leaf_items = get_items(submission_id, headers=False)
    subtotal = sum((dict(it).get("amount") or 0) for it in leaf_items)

    # 비교 단위 + 추출 방식 + 매핑 설정 저장 (재진입 시 복원용)
    map_config = {
        "sheet": sheet,
        "header_row": header_row,
        "mapping": {str(k): v for k, v in mapping.items()},
        "compare_units": compare_units,
        "excluded_rows": sorted(excluded_rows),
        "nego_rows": sorted(nego_rows),
    }
    update_submission(submission_id,
                      extraction_status="done",
                      subtotal_excl_vat=subtotal,
                      compare_units=_json.dumps(compare_units, ensure_ascii=False),
                      extracted_sheets=_json.dumps([sheet], ensure_ascii=False),
                      map_config=_json.dumps(map_config, ensure_ascii=False))

    return jsonify({"ok": True, "n_items": len(items),
                    "redirect": url_for("submissions.detail", submission_id=submission_id)})


@bp.route("/<submission_id>/item/<item_id>/delete", methods=["POST"])
@require_role("manager")
def delete_item(submission_id, item_id):
    """잘못 추출된 단일 아이템 삭제 (라인 아이템 / 트리 양쪽에서 호출)."""
    from db.queries import delete_single_item, recompute_subtotal
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    n = delete_single_item(item_id)
    if n:
        recompute_subtotal(submission_id)  # 합계 일치 유지
    if request.is_json or request.headers.get("X-Requested-With"):
        return jsonify({"ok": bool(n), "deleted": n})
    flash("항목을 삭제했습니다." if n else "항목을 찾을 수 없습니다.",
          "success" if n else "warning")
    tok = getattr(g, "auth_token", "") or ""
    return redirect(url_for("submissions.detail", submission_id=submission_id, _t=tok))


@bp.route("/<submission_id>/nego/add", methods=["POST"])
@require_role("manager")
def nego_add(submission_id):
    """special nego 항목 추가 (수기)."""
    from db.queries import add_nego_item, recompute_subtotal
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    payload = request.get_json(silent=True) or {}
    label = (payload.get("label") or "").strip() or "Special Nego"
    amount = payload.get("amount") or 0
    iid = add_nego_item(submission_id, label, amount)
    recompute_subtotal(submission_id)
    return jsonify({"ok": True, "item_id": iid})


@bp.route("/<submission_id>/nego/<item_id>/update", methods=["POST"])
@require_role("manager")
def nego_update(submission_id, item_id):
    """special nego 항목 수정."""
    from db.queries import update_nego_item, recompute_subtotal
    payload = request.get_json(silent=True) or {}
    update_nego_item(item_id,
                     label=payload.get("label"),
                     amount=payload.get("amount"))
    recompute_subtotal(submission_id)
    return jsonify({"ok": True})


@bp.route("/<submission_id>/nego/list", methods=["GET"])
@require_role("manager")
def nego_list(submission_id):
    """special nego 항목 목록."""
    from db.queries import list_nego_items
    return jsonify({"items": list_nego_items(submission_id)})
