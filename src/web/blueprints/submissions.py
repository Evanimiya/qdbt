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


# [B.ii] classify_workbook 결과 캐시 — {fpath: (sig, {sheet: role})}.
#  시그니처(크기:mtime)가 같으면 재계산 없이 재사용해 시트 전환 지연을 없앤다.
_CLASSIFY_CACHE = {}


def _file_sig(path):
    try:
        st = os.stat(path)
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return None


def _classify_workbook_cached(fpath):
    """워크북 시트별 역할 판정을 파일 시그니처로 캐시(프로세스 메모리)."""
    sig = _file_sig(fpath)
    cached = _CLASSIFY_CACHE.get(fpath)
    if cached and cached[0] == sig:
        return cached[1]
    from extractors.stitch import classify_workbook
    roles = {}
    for _s in classify_workbook(fpath).get("sheets", []):
        roles[_s["name"]] = _s["role"]
    _CLASSIFY_CACHE[fpath] = (sig, roles)
    return roles


def _db_connect():
    """직접 DB 연결 — busy_timeout 포함 (worker와의 쓰기 경합 시 5초 대기)."""
    import sqlite3 as _sq
    from db.queries import DB_PATH as _DBP
    conn = _sq.connect(_DBP)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


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

    # 통화·환율 패널: 통화별 환율 맵 (extracted/manual/missing)
    # 패치 이전에 추출된 제출서는 fx_rates 미계산(NULL) → 진입 시 1회 백필(idempotent)
    try:
        fx_rates = _json.loads(dict(sub).get("fx_rates") or "{}")
    except Exception:
        fx_rates = {}
    if not fx_rates and dict(sub).get("extraction_status") == "done":
        from db.queries import recompute_subtotal
        recompute_subtotal(submission_id)
        sub = get_submission(submission_id)
        try:
            fx_rates = _json.loads(dict(sub).get("fx_rates") or "{}")
        except Exception:
            fx_rates = {}

    # [T7] 스티칭 미해결(residual) 검토용
    stitch_meta, residual_view = _build_residual_view(_subd, items)

    # [연계 캔버스 공유] 상세 페이지에서도 동일 컴포넌트로 연계 트리 표시.
    residual_ids = sorted({rv["item_id"] for rv in residual_view} if residual_view else set(),
                          key=lambda x: str(x))
    try:
        _mc_all = _json.loads(_subd.get("map_config") or "{}") or {}
        manual_ids = [k for k, v in (_mc_all.get("link_overrides") or {}).items() if v == "manual"]
    except Exception:
        manual_ids = []
    level_names = ["대분류", "중분류", "소분류", "세분류", "품명", "부품", "세부"]

    return render_template("submissions/detail.html", sub=sub, items=items,
                           fx_rates=fx_rates,
                           sheet_list=sheet_list, prev_sheets=prev_sheets,
                           grouped=grouped, compare_level=level,
                           stitch_meta=stitch_meta, residual_view=residual_view,
                           tree_json=_json.dumps(tree_data, ensure_ascii=False),
                           canvas_tree_json=_json.dumps(tree_data.get("tree") or [], ensure_ascii=False),
                           residual_ids_json=_json.dumps(residual_ids, ensure_ascii=False),
                           manual_ids_json=_json.dumps(manual_ids, ensure_ascii=False),
                           level_names_json=_json.dumps(level_names, ensure_ascii=False),
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


@bp.route("/<submission_id>/compare-units/function-unit", methods=["POST"])
@require_role("manager")
def set_function_unit_compare(submission_id):
    """[T7·기능단위 비교] compare_units를 '기능단위' 레벨(대개 중/소분류)로 자동 설정.

    스티칭으로 잎(구성성분)까지 저장된 트리를, 지정 레벨(depth)의 분류 경로로 묶어
    compare_units에 저장한다. 이렇게 하면 기존 클러스터링·카탈로그 파이프라인
    (list_submission_items_for_clustering → 클러스터 → catalog_items·price_history)이
    품명이 아닌 '기능단위'로 업체 간 비교를 수행한다.

    payload: {level: int}  (기능단위 깊이. 기본 3 = 소분류. 2 = 중분류)
    """
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    level = int((request.get_json(silent=True) or {}).get("level", 3))
    level = max(1, min(level, 6))
    items = get_items(submission_id, headers=False)
    units = set()
    for it in items:
        d = dict(it)
        p = (d.get("path") or "").split(" > ")
        p = [x for x in p if x.strip()]
        if not p:
            continue
        # 지정 레벨까지 자른 상위 경로 = 기능단위 묶음 지점
        units.add(" > ".join(p[:min(level, len(p))]))
    units = sorted(units)
    update_submission(submission_id, compare_units=_json.dumps(units, ensure_ascii=False),
                      compare_level=level)
    return jsonify({"ok": True, "level": level, "count": len(units), "units": units[:50]})


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


@bp.route("/<submission_id>/reupload", methods=["POST"])
@require_role("manager")
def reupload(submission_id):
    """첨부 파일 다시 올리기 — 재추출용.

    데이터 정합성 원칙: 재업로드는 '추출 초기화 상태'에서만 안전하다.
    이미 추출된(done) 제출서는 compare_units·is_nego·클러스터 소속이
    옛 item_id를 참조하므로, 파일만 교체하면 참조가 새 파일 항목과
    어긋난다. 따라서 항목이 남아 있으면 사용자가 명시적으로 초기화에
    동의(confirm_reset=1)한 경우에만 reset_submission()으로 초기화한 뒤
    파일을 교체한다.

    이전 물리 파일은 감사·재현 근거일 수 있어 삭제하지 않고 보존한다
    (새 파일은 타임스탬프명으로 별도 저장되어 공존, 고아 참조 없음).
    """
    from flask import current_app

    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    subd = dict(sub)
    tok = getattr(g, "auth_token", "") or ""

    def _back():
        return redirect(url_for("submissions.detail",
                                submission_id=submission_id, _t=tok))

    # ── 파일 검증 ──
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("파일을 선택하세요.", "error")
        return _back()
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        flash(f"지원하지 않는 형식: {suffix}", "error")
        return _back()

    # ── 초기화 필요 여부 판정 (항목 존재 = done 이거나 추출물 있음) ──
    existing_items = get_items(submission_id)  # nego 포함 전체 라인
    needs_reset = bool(existing_items) or subd.get("extraction_status") == "done"
    confirmed = request.form.get("confirm_reset") == "1"
    if needs_reset and not confirmed:
        # 프런트에서 confirm을 거치지 않은 요청은 안전하게 차단
        flash("기존 추출 데이터가 있어 재업로드하려면 초기화 동의가 필요합니다.",
              "warning")
        return _back()

    # ── 새 파일 저장 (이전 파일은 유지) ──
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name
    try:
        from extractors.pipeline import save_upload
        saved = save_upload(tmp_path, file.filename)
    except Exception as e:
        flash(f"❌ 파일 저장 실패: {e}", "error")
        return _back()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # ── 추출 데이터 초기화 (동의된 경우) ──
    if needs_reset:
        reset_submission(submission_id)

    # ── 제출서 레코드 갱신: 새 파일로 교체 + 이전 파일 기준 매핑 폐기 ──
    # extracted_sheets·map_config는 이전 파일 구조 기준이므로 새 파일과
    # 어긋날 수 있어 초기화한다(재추출 시 새로 지정).
    update_submission(
        submission_id,
        file_name=saved.name,
        file_path=str(saved),
        file_format=suffix.lstrip("."),
        extracted_sheets=None,
        map_config=None,
        extraction_status="pending",
    )

    # ── 형식에 맞는 후속 추출 경로로 유도 ──
    if suffix == ".xlsx":
        flash("✅ 파일을 다시 올렸습니다. '🧩 열 매핑 추출'로 재추출하세요.",
              "success")
        return redirect(url_for("submissions.column_map",
                                submission_id=submission_id, _t=tok))
    flash("✅ 파일을 다시 올렸습니다. 제출서 상세에서 재추출하세요.", "success")
    return _back()


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


@bp.route("/<submission_id>/fx-rate", methods=["POST"])
@require_role("manager")
def set_fx_rate(submission_id):
    """통화별 환율 수동 설정 → 해당 통화 항목 원화 재계산 (통화→KRW 쌍 명기).

    확정 정책: 수정 환율로 재계산(견적서 원화 덮어쓰기). 원통화(unit_price_orig)
    가 보존되므로 재추출 없이 환율만 교정 가능.
    """
    from db.queries import set_submission_fx_rate
    currency = request.form.get("currency", "").strip().upper()
    rate_raw = request.form.get("rate", "").strip().replace(",", "")
    try:
        rate = float(rate_raw)
        r = set_submission_fx_rate(submission_id, currency, rate)
        flash(f"✅ 환율 적용: {r['currency']} → KRW = {r['rate']:,.2f} "
              f"({r['items_updated']}개 항목 원화 재계산)", "success")
    except Exception as e:
        flash(f"❌ 환율 설정 실패: {e}", "error")
    return redirect(url_for("submissions.detail", submission_id=submission_id))


@bp.route("/<submission_id>/fx-rate/remove", methods=["POST"])
@require_role("manager")
def remove_fx_rate(submission_id):
    """통화 환율 삭제 → 미확인 상태로 되돌림 (원통화 환산 취소)."""
    from db.queries import remove_submission_fx_rate
    currency = request.form.get("currency", "").strip().upper()
    try:
        r = remove_submission_fx_rate(submission_id, currency)
        flash(f"🗑 {r['currency']} 환율을 삭제했습니다 "
              f"({r['items_reverted']}개 항목 원통화로 복귀).", "success")
    except Exception as e:
        flash(f"❌ 환율 삭제 실패: {e}", "error")
    return redirect(url_for("submissions.detail", submission_id=submission_id))
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

            conn = _db_connect()
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

    conn = _db_connect()
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
    conn = _db_connect()
    conn.execute("PRAGMA foreign_keys = ON")
    # FK 자식 레코드 먼저 삭제 (price_history, catalog_suggestions → submission_items 참조)
    conn.execute("DELETE FROM price_history WHERE submission_id=?", (submission_id,))
    conn.execute("DELETE FROM catalog_suggestions WHERE submission_id=?", (submission_id,))
    conn.execute("""DELETE FROM item_match WHERE item_id IN (
        SELECT item_id FROM submission_items WHERE submission_id=?)""", (submission_id,))
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
    # 저장된 시트가 있으면 그 시트로 (쿼리파라미터 우선).
    # [B.ii 성능] 실제로 시트가 바뀔 때만 재오픈(같은 시트 중복 load_workbook 방지).
    if (saved_config and not request.args.get("sheet")
            and saved_config.get("sheet") and saved_config["sheet"] != sheet):
        sheet = saved_config["sheet"]
        try:
            grid_data = read_grid(fpath, sheet)
            suggestion = suggest_column_mapping(fpath, sheet)
        except Exception:
            pass

    # [매핑 보존] 현재 시트의 저장된 매핑(다중시트 드래프트 포함)을 우선 복원.
    cur_saved = None
    if saved_config:
        _sheets_cfg = saved_config.get("sheets") or {}
        if sheet in _sheets_cfg:
            cur_saved = _sheets_cfg[sheet]                 # 다중시트 드래프트
        elif saved_config.get("sheet") == sheet and saved_config.get("mapping"):
            cur_saved = saved_config                       # 단일시트(하위호환)

    # 합계·소계 의심 행 감지 — [버그2] 반드시 '현재 시트'의 매핑으로만 판단.
    from extractors.extract_by_mapping import detect_total_rows
    total_suggestion = []
    try:
        _map_for_detect = (cur_saved.get("mapping") if cur_saved else None) \
                          or suggestion.get("mapping") or {}
        _hr_for_detect = (cur_saved.get("header_row") if cur_saved else None) \
                         or suggestion.get("header_row") or 1
        _map_for_detect = {int(k): v for k, v in _map_for_detect.items()}
        if _map_for_detect:
            total_suggestion = detect_total_rows(fpath, sheet, _map_for_detect, _hr_for_detect)
    except Exception:
        total_suggestion = []

    # 도메인 분류 바인딩: 대분류(cat1)를 도메인 표준 분류에 정합시키기 위한 참조.
    # 매핑 화면에서 대분류가 표준을 벗어나면 시각 경고(층위 1: 사전 바인딩).
    from db.queries import get_domain_category_binding
    _domain = subd.get("bid_domain") or "공통"
    _binding = get_domain_category_binding(_domain)
    domain_binding = {
        "domain": _domain,
        "standard": _binding["standard"],           # 표준 분류명 목록
        "lookup": _binding["lookup"],                # 정규화키→표준명 (별칭 포함)
        "alias_map": _binding["alias_map"],
    }

    # 시트별 역할 자동 판정(갑지·설명 시트 사전 해제용). 데이터 없는 시트는 'unknown'.
    # [B.ii 성능] classify_workbook 은 워크북을 시트당 ~2회 재오픈해 매우 비싸므로,
    #  파일 시그니처(크기+mtime)로 프로세스 메모리에 캐시해 시트 전환마다 재실행하지 않는다.
    sheet_roles = {}
    try:
        if len(sheets) > 1:
            sheet_roles = _classify_workbook_cached(fpath)
    except Exception:
        sheet_roles = {}

    # [B.i] 사용자가 명시 선택한 통합추출 시트 목록(정본). 없으면 프런트가 역할로 기본 판정.
    saved_selection = (saved_config or {}).get("sheet_selection")

    return render_template("submissions/column_map.html",
                           sub=subd, sheets=sheets, current_sheet=sheet,
                           sheet_roles=sheet_roles,
                           grid_json=_json.dumps(grid_data, ensure_ascii=False),
                           suggest_json=_json.dumps(suggestion, ensure_ascii=False),
                           saved_config_json=_json.dumps(saved_config, ensure_ascii=False),
                           cur_saved_json=_json.dumps(cur_saved, ensure_ascii=False),
                           total_suggestion_json=_json.dumps(total_suggestion, ensure_ascii=False),
                           domain_binding_json=_json.dumps(domain_binding, ensure_ascii=False),
                           sheet_selection_json=_json.dumps(saved_selection, ensure_ascii=False))


@bp.route("/<submission_id>/category-binding", methods=["GET"])
@require_role("manager")
def category_binding_check(submission_id):
    """제출서 대분류의 도메인 표준 정합 검증 결과 반환 (층위 2)."""
    from db.queries import (validate_submission_categories,
                            get_domain_category_binding, get_submission)
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    try:
        result = validate_submission_categories(submission_id)
        binding = get_domain_category_binding(result["domain"])
        result["standard"] = binding["standard"]
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 200


@bp.route("/<submission_id>/category-binding/apply", methods=["POST"])
@require_role("manager")
def category_binding_apply(submission_id):
    """미매칭·편차 대분류를 표준 분류로 일괄 재지정 (+ 선택적 별칭 학습)."""
    from db.queries import apply_category_binding, get_submission
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    payload = request.get_json(silent=True) or {}
    mapping = payload.get("mapping") or {}      # {원본대분류: 표준분류}
    add_as_alias = bool(payload.get("add_as_alias"))
    try:
        r = apply_category_binding(submission_id, mapping, add_as_alias=add_as_alias)
        return jsonify({"ok": True, **r})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 200


_RESIDUAL_REASON_KO = {
    "ambiguous_join_key": "모호한 연결(같은 분류명이 여러 상위에 걸림)",
    "skeleton_unmatched": "상위 분류 미매칭",
    "level_skip": "레벨 생략(상위 분류 비어 상속 추정)",
    "seq_parent_missing": "번호계층 상위 이름 없음",
    "summary_item": "요약 항목(‘외 N종’) — 세부 시트로 대체 필요",
    "summary_unmatched": "요약행이나 상세와 금액 정합 안 됨 — 확인 필요",
}


def _build_residual_view(subd, items):
    """map_config.stitch.residuals ↔ 현재 항목 매칭해 검토용 목록 생성.
    반환: (stitch_meta, residual_view[{item_id,name,reason,reason_ko,current_path,candidates}])"""
    import json as _json
    try:
        mc = _json.loads(subd.get("map_config") or "{}") or {}
        stitch_meta = mc.get("stitch") or {}
    except Exception:
        stitch_meta = {}
    view = []
    seen_ids = set()
    item_paths = {dict(it).get("path") for it in items if dict(it).get("path")}
    skel = set(stitch_meta.get("candidates") or [])
    all_paths = sorted(skel | item_paths)
    if stitch_meta and stitch_meta.get("residuals"):
        ln_map = {dict(it).get("line_no"): dict(it) for it in items}
        for rs in stitch_meta["residuals"]:
            it = ln_map.get(f"R{rs.get('row')}")
            if not it:
                continue
            seen_ids.add(it.get("item_id"))
            view.append({
                "item_id": it.get("item_id"),
                "name": rs.get("name") or it.get("name_normalized"),
                "reason": rs.get("reason"),
                "reason_ko": _RESIDUAL_REASON_KO.get(rs.get("reason"), rs.get("reason")),
                "current_path": it.get("path"),
                "amount": it.get("amount"),
                # 현재 경로도 포함(모호 조인은 현재값이 정답일 수 있음 → '확인' 선택지)
                "candidates": all_paths,
            })
    # [연계 E] 수기로 '미연계(빨강)'로 되돌린 항목(link_overrides)도 residual에 포함.
    try:
        overrides = mc.get("link_overrides") or {}
    except Exception:
        overrides = {}
    if overrides:
        by_id = {dict(it).get("item_id"): dict(it) for it in items}
        for iid, st in overrides.items():
            if st != "residual" or iid in seen_ids:
                continue
            it = by_id.get(iid)
            if not it:
                continue
            seen_ids.add(iid)
            view.append({
                "item_id": iid, "name": it.get("name_normalized"),
                "reason": "manual_residual",
                "reason_ko": "수기 지정(재확인 필요)",
                "current_path": it.get("path"), "amount": it.get("amount"),
                "candidates": all_paths,
            })
    return stitch_meta, view


def _heuristic_pick(name, candidates):
    """LLM 미가용 시 폴백: 토큰 겹침 + 공백제거 부분일치로 최적 상위 경로 선택.

    한국어는 표기 시 띄어쓰기가 제각각('관리서버' vs '관리 서버')이라, 토큰 집합만으론
    부족하다. 공백 제거 후 세그먼트 포함 관계까지 점수화해 매칭 강건성을 높인다.
    """
    import re as _re
    def toks(s):
        return set(t.lower() for t in _re.split(r"[\s>·/\-()]+", s or "") if len(t) >= 2)
    def ns(s):
        return _re.sub(r"[\s>·/\-()]+", "", (s or "").lower())
    nt = toks(name)
    name_ns = ns(name)
    best, best_score = None, 0
    for c in candidates:
        score = len(nt & toks(c))
        # 공백제거 세그먼트 포함 관계 (가장 하위 세그먼트일수록 가중)
        for seg in [s for s in (c or "").split(" > ") if s.strip()]:
            seg_ns = ns(seg)
            if seg_ns and (seg_ns in name_ns or name_ns in seg_ns):
                score += max(2, len(seg_ns) // 2)
        if score > best_score:
            best, best_score = c, score
    return best, best_score


@bp.route("/<submission_id>/residuals/suggest", methods=["POST"])
@require_role("manager")
def residuals_suggest(submission_id):
    """[T7·LLM] 미해결 residual의 상위 분류를 LLM(가용 시) 또는 휴리스틱으로 1차 제안.
    사람이 최종 확인(적용)하는 흐름 — 자동 반영하지 않는다."""
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    items = get_items(submission_id)
    _stitch, view = _build_residual_view(dict(sub), items)
    if not view:
        return jsonify({"ok": True, "suggestions": {}, "method": "none"})

    suggestions = {}
    method = "heuristic"
    # 1) LLM 시도 (사용자 키 보유 시)
    try:
        from db.queries import get_user_llm_settings
        llm = get_user_llm_settings(session.get("user_id", ""))
    except Exception:
        llm = {}
    if llm.get("api_key"):
        try:
            from extractors.providers import get_provider
            prov = get_provider(llm["provider"])
            payload = [{"id": v["item_id"], "name": v["name"],
                        "current_path": v["current_path"],
                        "candidates": v["candidates"]} for v in view]
            sys_prompt = (
                "너는 조달 견적서 분류 보조자다. 각 품목(name)을 의미적으로 가장 알맞은 "
                "상위 분류 경로에 매칭하라. 반드시 그 품목의 candidates 목록 중에서 하나를 "
                "고르고, 애매하면 빈 문자열을 반환하라. 영어·한국어 동의어(GPU서버=GPU Server, "
                "방화벽=Firewall 등)를 고려하라. 오직 JSON만 출력: "
                '{"assignments":[{"id":"...","path":"..."}]}')
            raw = prov.extract(_json.dumps(payload, ensure_ascii=False), sys_prompt,
                               api_key=llm["api_key"], model=llm.get("model"),
                               base_url=llm.get("base_url"),
                               verify_ssl=llm.get("verify_ssl", True))
            data = _json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            valid = {v["item_id"]: set(v["candidates"]) for v in view}
            for a in data.get("assignments", []):
                iid, p = a.get("id"), (a.get("path") or "").strip()
                if iid in valid and p in valid[iid]:
                    suggestions[iid] = {"path": p, "method": "llm"}
            method = "llm"
        except Exception as e:
            method = f"heuristic (LLM 실패: {type(e).__name__})"

    # 2) LLM이 못 채운 항목은 휴리스틱으로 보완
    for v in view:
        if v["item_id"] in suggestions:
            continue
        pick, score = _heuristic_pick(v["name"], v["candidates"])
        if pick and score > 0:
            suggestions[v["item_id"]] = {"path": pick, "method": "heuristic"}

    return jsonify({"ok": True, "suggestions": suggestions, "method": method,
                    "n": len(suggestions)})


@bp.route("/<submission_id>/map/save-sheet", methods=["POST"])
@require_role("manager")
def map_save_sheet(submission_id):
    """[T7 매핑 보존] 추출하지 않고 '한 시트의 매핑 지정사항'만 map_config에 드래프트 저장.

    시트 전환·재진입 시 지정사항이 사라지던 문제 해결의 핵심. 시트를 바꾸기 전에
    현재 시트 매핑을 여기로 저장해 두면, 새로고침·재진입 후에도 복원된다.
    payload: {sheet, mapping:{col:role}, header_row, mount_path, compare_units, excluded_rows, nego_rows}
    """
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    p = request.get_json(silent=True) or {}
    sheet = p.get("sheet")
    if not sheet:
        return jsonify({"ok": False, "error": "sheet 필요"}), 200
    try:
        mc = _json.loads(dict(sub).get("map_config") or "{}") or {}
    except Exception:
        mc = {}
    sheets = mc.get("sheets") or {}
    sheets[sheet] = {
        "mapping": {str(k): v for k, v in (p.get("mapping") or {}).items() if v and v != "ignore"},
        "header_row": int(p.get("header_row") or 1),
        "mount_path": (p.get("mount_path") or "").strip(),
        "compare_units": p.get("compare_units") or [],
        "excluded_rows": sorted(p.get("excluded_rows") or []),
        "nego_rows": sorted(p.get("nego_rows") or []),
    }
    order = mc.get("sheet_order") or []
    if sheet not in order:
        order.append(sheet)
    mc["sheets"] = sheets
    mc["sheet_order"] = order
    mc["version"] = 2 if len(sheets) > 1 else mc.get("version", 1)
    mc["draft"] = True   # 아직 추출 전 드래프트임을 표시
    update_submission(submission_id, map_config=_json.dumps(mc, ensure_ascii=False))
    return jsonify({"ok": True, "saved_sheets": sorted(sheets.keys())})


@bp.route("/<submission_id>/map/sheet-preview", methods=["GET"])
@require_role("manager")
def map_sheet_preview(submission_id):
    """[추출화면 C] 이미 조정한 시트의 헤더 + 조정 매핑 + 상위 몇 행 미리보기.
    다른 시트의 레벨(대/중/소/세/품명) 조정을 참고하도록 on-demand로 제공(경량)."""
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    subd = dict(sub)
    fpath = subd.get("file_path")
    sheet = request.args.get("sheet")
    if not sheet or not fpath:
        return jsonify({"ok": False, "error": "sheet 필요"}), 200
    try:
        mc = _json.loads(subd.get("map_config") or "{}") or {}
    except Exception:
        mc = {}
    sv = (mc.get("sheets") or {}).get(sheet) or {}
    mapping = {int(k): v for k, v in (sv.get("mapping") or {}).items()}
    header_row = int(sv.get("header_row") or 1)
    from extractors.extract_by_mapping import read_grid
    try:
        grid = read_grid(fpath, sheet, max_rows=header_row + 5)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 200
    rows = grid.get("grid") or []
    header = rows[header_row - 1] if len(rows) >= header_row else []
    data_rows = rows[header_row:header_row + 5]
    return jsonify({"ok": True, "sheet": sheet, "header_row": header_row,
                    "header": header, "rows": data_rows,
                    "mapping": {str(k): v for k, v in mapping.items()}})


@bp.route("/<submission_id>/map/selection", methods=["POST"])
@require_role("manager")
def map_save_selection(submission_id):
    """[B.i] 통합추출 시트 선택(체크박스) 상태를 map_config에 정본으로 저장.

    시트 전환은 전체 페이지 리로드라 체크 상태가 초기화되던 문제 해결의 핵심.
    체크가 바뀔 때마다 이 경로로 저장 → 재진입·시트이동 후에도 선택이 유지된다.
    payload: {sheets:[선택된 시트명, ...]}
    """
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    p = request.get_json(silent=True) or {}
    sel = p.get("sheets")
    if not isinstance(sel, list):
        return jsonify({"ok": False, "error": "sheets 배열이 필요합니다."}), 200
    try:
        mc = _json.loads(dict(sub).get("map_config") or "{}") or {}
    except Exception:
        mc = {}
    mc["sheet_selection"] = [str(s) for s in sel]
    update_submission(submission_id, map_config=_json.dumps(mc, ensure_ascii=False))
    return jsonify({"ok": True, "sheet_selection": mc["sheet_selection"]})


@bp.route("/<submission_id>/map/auto-stitch", methods=["POST"])
@require_role("manager")
def map_auto_stitch(submission_id):
    """[T7] 원클릭: 모든 시트의 헤더를 자동 인식해 한 번에 스티칭 추출.

    시트별 매핑을 수동으로 추가하지 않아도, 코드가 각 시트 헤더를 제안하고
    밴드/시퀀스/올인원을 자동 판별해 하나의 잎-보존 트리로 통합한다.
    (자동 인식이 어긋나면 기존 '열 역할 지정 → + 이 시트 추가 → 통합 추출' 수동 흐름 사용.)
    """
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    subd = dict(sub)
    fpath = subd.get("file_path")
    _raw_sheets = (request.get_json(silent=True) or {}).get("sheets")
    if _raw_sheets is not None and len(_raw_sheets) == 0:
        return jsonify({"ok": False, "error": "추출할 시트를 1개 이상 선택하세요."}), 200
    only_sheets = _raw_sheets or None
    # [매핑 보존] 저장된 시트별 매핑이 있으면 그것을 사용, 없는 시트만 자동 제안.
    try:
        _mc = _json.loads(subd.get("map_config") or "{}") or {}
    except Exception:
        _mc = {}
    _saved_sheets = _mc.get("sheets") or {}
    try:
        from extractors.stitch import stitch_sheets, stitch_workbook
        from parsers.parse_xlsx import get_xlsx_sheet_names
        from extractors.extract_by_mapping import suggest_column_mapping
        all_names = [s for s in get_xlsx_sheet_names(fpath) if not s.startswith("_")]
        target = [s for s in (only_sheets or all_names) if s in all_names]
        specs = []
        for sh in target:
            sv = _saved_sheets.get(sh)
            if sv and sv.get("mapping"):
                specs.append({"sheet": sh,
                              "mapping": {int(k): v for k, v in sv["mapping"].items()},
                              "header_row": sv.get("header_row", 1)})
            else:
                sug = suggest_column_mapping(fpath, sh)
                specs.append({"sheet": sh, "mapping": sug["mapping"],
                              "header_row": sug["header_row"]})
        sres = stitch_sheets(fpath, specs)
    except Exception as e:
        return jsonify({"ok": False, "error": f"[auto-stitch] {type(e).__name__}: {e}"}), 200

    items = sres["items"]
    from db.queries import delete_submission_items, insert_items_bulk, get_items
    delete_submission_items(submission_id, keep_nego=True)
    insert_items_bulk(submission_id, items)
    leaf_items = get_items(submission_id, headers=False)
    subtotal = sum((dict(it).get("amount") or 0) for it in leaf_items)

    sheets_meta = sres.get("sheets", [])
    map_config = {
        "version": 2 if len(sheets_meta) > 1 else 1,
        "sheets": {
            s["name"]: {
                "mapping": {str(k): v for k, v in s["mapping"].items()},
                "header_row": s["header_row"], "mount_path": "",
                "compare_units": [], "excluded_rows": [], "nego_rows": [],
            } for s in sheets_meta
        },
        "sheet_order": [s["name"] for s in sheets_meta],
        "stitch": {
            "mode": sres["mode"], "n_items": sres["n_items"],
            "n_residuals": sres["n_residuals"],
            "residuals": sres["residuals"][:50],
            "candidates": sres.get("candidates", [])[:300],
            "reconciliation": sres.get("reconciliation"),   # [중복 병합] 요약↔상세 정리 리포트
            "n_dropped": sres.get("n_dropped", 0),
            "sheet_roles": [{"name": s["name"], "role": s["role"]}
                            for s in sres.get("sheets", [])],
        },
    }
    update_submission(submission_id, extraction_status="done",
                      subtotal_excl_vat=subtotal,
                      extracted_sheets=_json.dumps([s["name"] for s in sheets_meta], ensure_ascii=False),
                      map_config=_json.dumps(map_config, ensure_ascii=False))
    n_kept = sum(1 for it in items if not it.get("merge_status"))
    return jsonify({"ok": True, "n_items": n_kept,
                    "stitch": map_config["stitch"],
                    "redirect": url_for("submissions.detail", submission_id=submission_id)})


@bp.route("/<submission_id>/map/extract", methods=["POST"])
@require_role("manager")
def column_map_extract(submission_id):
    """확인된 열 매핑으로 코드 추출 실행 + 비교 단위 저장.

    [T6] 다중 시트 지원: payload에 sheets(시트별 매핑 배열)가 오면 각 시트를
    개별 매핑으로 추출해 하나의 제출서로 누적 통합한다. 삭제는 1회, 삽입은 누적.
    단일 sheet payload도 그대로 처리(하위 호환).
    """
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    subd = dict(sub)
    fpath = subd.get("file_path")

    payload = request.get_json(silent=True) or {}

    # 시트별 매핑 목록 구성: 다중(sheets) 또는 단일(sheet) → 공통 형태로 정규화
    sheet_specs = []
    if payload.get("sheets"):
        # 다중 시트: [{sheet, mapping, header_row, mount_path, compare_units,
        #              excluded_rows, nego_rows}, ...]
        for sp in payload["sheets"]:
            sheet_specs.append({
                "sheet": sp.get("sheet"),
                "mapping": {int(k): v for k, v in (sp.get("mapping") or {}).items()
                            if v and v != "ignore"},
                "header_row": int(sp.get("header_row") or 1),
                "mount_path": (sp.get("mount_path") or "").strip(),
                "compare_units": sp.get("compare_units") or [],
                "excluded_rows": set(sp.get("excluded_rows") or []),
                "nego_rows": set(sp.get("nego_rows") or []),
            })
    else:
        # 단일 시트 (하위 호환)
        sheet_specs.append({
            "sheet": payload.get("sheet"),
            "mapping": {int(k): v for k, v in (payload.get("mapping") or {}).items()
                        if v and v != "ignore"},
            "header_row": int(payload.get("header_row") or 1),
            "mount_path": "",
            "compare_units": payload.get("compare_units") or [],
            "excluded_rows": set(payload.get("excluded_rows") or []),
            "nego_rows": set(payload.get("nego_rows") or []),
        })

    from extractors.extract_by_mapping import extract_by_mapping
    all_items = []
    all_compare_units = []
    extracted_sheet_names = [spec["sheet"] for spec in sheet_specs]
    stitch_info = None

    # ── [T7] 다중시트 스티칭 분기 ─────────────────────────────
    #  겹치는 밴드([대/중/소]·[중/소/세]·[세/품목])나 목록/트리/세부처럼
    #  '여러 시트를 하나의 트리로 꿰매야' 하는 경우, mount_path 이어붙이기 대신
    #  스티칭 엔진으로 공유레벨/번호계층을 조인해 통합 잎 데이터셋을 만든다.
    #  요청에 stitch=true가 오거나, 다중시트이고 mount_path가 없고 자동판정이
    #  band/seq이면 스티칭. (단일시트·mount_path 지정 시는 기존 T6 경로 유지.)
    use_stitch = False
    if len(sheet_specs) > 1 and not any(sp["mount_path"] for sp in sheet_specs):
        # 외화(통화 환산) 매핑이 있으면 스티칭이 원화 파이프라인을 타지 않으므로 T6 유지.
        _has_currency = any(r in ("currency", "price_krw", "amount_krw")
                            for sp in sheet_specs for r in sp["mapping"].values())
        if payload.get("stitch") is True:
            use_stitch = True
        elif payload.get("stitch") is None and not _has_currency:
            # 다중 시트(견적서+상세 등)는 스티칭 엔진으로 — 총계행 정확 제외 +
            # 요약↔상세 중복 병합(총액 불변). mount_path 명시 배치는 위에서 제외됨.
            use_stitch = True

    if use_stitch:
        try:
            from extractors.stitch import stitch_sheets
            sres = stitch_sheets(fpath, sheet_specs)
        except Exception as e:
            return jsonify({"ok": False,
                            "error": f"[stitch] {type(e).__name__}: {e}"}), 200
        # nego 행 반영(스티칭 잎 중 지정 nego 행은 차감)
        nego_all = set()
        for sp in sheet_specs:
            nego_all |= set(sp["nego_rows"])
        for it in sres["items"]:
            ln = it.get("line_no", "")
            rownum = int(ln[1:]) if isinstance(ln, str) and ln[1:].isdigit() else None
            if rownum in nego_all:
                it["is_nego"] = True
                for f in ("amount", "unit_price"):
                    if it.get(f) is not None:
                        it[f] = -abs(it[f])
        all_items = sres["items"]
        for sp in sheet_specs:
            all_compare_units.extend(sp["compare_units"])
        stitch_info = {
            "mode": sres["mode"],
            "n_items": sres["n_items"],
            "n_residuals": sres["n_residuals"],
            "residuals": sres["residuals"][:50],   # UI 노출용 상한
            "candidates": sres.get("candidates", [])[:300],  # 정상 분류경로(재지정 후보)
            "reconciliation": sres.get("reconciliation"),   # [중복 병합] 요약↔상세 정리 리포트
            "n_dropped": sres.get("n_dropped", 0),
            "sheet_roles": [{"name": s["name"], "role": s["role"]}
                            for s in sres.get("sheets", [])],
        }
    else:
        # ── 기존 T6 경로: 시트별 추출 후 mount_path 누적 ──
        for spec in sheet_specs:
            try:
                result = extract_by_mapping(fpath, spec["sheet"], spec["mapping"],
                                            spec["header_row"],
                                            excluded_rows=spec["excluded_rows"],
                                            nego_rows=spec["nego_rows"])
            except Exception as e:
                return jsonify({"ok": False,
                                "error": f"[{spec['sheet']}] {type(e).__name__}: {e}"}), 200
            sheet_items = result["items"]
            mp = spec["mount_path"]
            for it in sheet_items:
                if mp:
                    cur_path = it.get("path") or it.get("parent_path", "")
                    it["path"] = f"{mp} > {cur_path}" if cur_path else mp
            all_items.extend(sheet_items)
            all_compare_units.extend(spec["compare_units"])

    # DB 저장: 삭제 1회(수기 nego 보존) + 누적 삽입 1회
    from db.queries import delete_submission_items, insert_items_bulk, get_items
    delete_submission_items(submission_id, keep_nego=True)
    insert_items_bulk(submission_id, all_items)

    # 공급가액 = 잎(헤더/소계 제외) amount 합.
    leaf_items = get_items(submission_id, headers=False)
    subtotal = sum((dict(it).get("amount") or 0) for it in leaf_items)

    # map_config v2: 시트별 매핑 보존 (재진입 복원용)
    map_config = {
        "version": 2 if len(sheet_specs) > 1 else 1,
        "sheets": {
            spec["sheet"]: {
                "mapping": {str(k): v for k, v in spec["mapping"].items()},
                "header_row": spec["header_row"],
                "mount_path": spec["mount_path"],
                "compare_units": spec["compare_units"],
                "excluded_rows": sorted(spec["excluded_rows"]),
                "nego_rows": sorted(spec["nego_rows"]),
            } for spec in sheet_specs
        },
        "sheet_order": extracted_sheet_names,
        "stitch": stitch_info,   # [T7] 스티칭 결과(mode·residual). None이면 미사용.
    }
    # 단일 시트는 기존 평면 형태도 병기(하위 호환 복원)
    if len(sheet_specs) == 1:
        sp = sheet_specs[0]
        map_config.update({
            "sheet": sp["sheet"],
            "header_row": sp["header_row"],
            "mapping": {str(k): v for k, v in sp["mapping"].items()},
            "compare_units": sp["compare_units"],
            "excluded_rows": sorted(sp["excluded_rows"]),
            "nego_rows": sorted(sp["nego_rows"]),
        })
    update_submission(submission_id,
                      extraction_status="done",
                      subtotal_excl_vat=subtotal,
                      compare_units=_json.dumps(all_compare_units, ensure_ascii=False),
                      extracted_sheets=_json.dumps(extracted_sheet_names, ensure_ascii=False),
                      map_config=_json.dumps(map_config, ensure_ascii=False))

    # 추출 직후 도메인 분류 정합 검증 (층위 2: 검증 게이트).
    # 표준을 벗어난 대분류가 있으면 상세 화면에서 재지정하도록 신호.
    from db.queries import validate_submission_categories
    try:
        cat_check = validate_submission_categories(submission_id)
        binding_warn = {
            "unmatched": cat_check["unmatched"],
            "n_unmatched_items": cat_check["n_unmatched_items"],
        }
    except Exception:
        binding_warn = {"unmatched": [], "n_unmatched_items": 0}

    n_kept = sum(1 for it in all_items if not it.get("merge_status"))
    return jsonify({"ok": True, "n_items": n_kept,
                    "binding_warn": binding_warn,
                    "stitch": stitch_info,
                    "redirect": url_for("submissions.detail", submission_id=submission_id)})


@bp.route("/<submission_id>/item/<item_id>/reassign-path", methods=["POST"])
@require_role("manager")
def reassign_item_path(submission_id, item_id):
    """[T7] 스티칭 residual 수동 보정: 항목의 분류 경로(path)를 사람이 재지정.

    새 경로로 path·depth·category를 갱신하고, 해당 residual을 map_config에서 제거해
    상세 재진입 시 미해결 목록에서 사라지게 한다. (금액 불변 → 합계 영향 없음)
    """
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    new_path = ((request.get_json(silent=True) or {}).get("path") or "").strip()
    if not new_path:
        return jsonify({"ok": False, "error": "새 경로(path)가 필요합니다."}), 200
    parts = [p for p in new_path.split(" > ") if p.strip()]
    depth = len(parts)
    category = parts[0] if parts else "기타"

    from db.queries import get_conn
    line_no = None
    with get_conn() as c:
        row = c.execute("SELECT line_no FROM submission_items "
                        "WHERE item_id=? AND submission_id=?",
                        (item_id, submission_id)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "항목을 찾을 수 없습니다."}), 200
        line_no = dict(row).get("line_no")
        c.execute("UPDATE submission_items SET path=?, depth=?, category=? "
                  "WHERE item_id=? AND submission_id=?",
                  (new_path, depth, category, item_id, submission_id))

    # map_config.stitch.residuals 에서 이 행(line_no=R{row}) 제거
    try:
        subd = dict(get_submission(submission_id))
        mc = _json.loads(subd.get("map_config") or "{}") or {}
        st = mc.get("stitch") or {}
        resids = st.get("residuals") or []
        target_row = int(line_no[1:]) if isinstance(line_no, str) and line_no[1:].isdigit() else None
        if target_row is not None:
            st["residuals"] = [r for r in resids if r.get("row") != target_row]
            st["n_residuals"] = len(st["residuals"])
            mc["stitch"] = st
        # [연계 캔버스] 드래그로 수기 연결한 항목 → manual(초록) 표시 지속(link_overrides).
        ov = mc.get("link_overrides") or {}
        ov[item_id] = "manual"
        mc["link_overrides"] = ov
        update_submission(submission_id, map_config=_json.dumps(mc, ensure_ascii=False))
    except Exception:
        pass

    return jsonify({"ok": True, "path": new_path, "depth": depth})


@bp.route("/<submission_id>/link", methods=["GET"])
@login_required
def link_view(submission_id):
    """[T7] 다중시트 연계 캔버스 (목업 4단계: 추출→정합성 제안→수기 조정→확정).
    목업(QDBT_다중시트연계_목업.html) 기반. 드래그 연결 = reassign-path 호출,
    확정 = 통합 트리 스냅샷(버전) 저장."""
    import json as _json
    from db.queries import build_items_tree, get_latest_snapshot
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    subd = dict(sub)
    items = get_items(submission_id)
    tree = build_items_tree(submission_id)
    stitch_meta, residual_view = _build_residual_view(subd, items)
    residual_ids = {rv["item_id"] for rv in residual_view}

    levels = {}   # depth -> [nodes]
    flat = []     # 확정 트리(STEP4)용 선행순회 목록
    def _parent_path(p):
        parts = [x for x in (p or "").split(" > ") if x.strip()]
        return " > ".join(parts[:-1]) if len(parts) > 1 else ""
    def _walk(n, depth):
        ld = n.get("leaf_data") or {}
        path = n.get("path") or ""
        levels.setdefault(depth, []).append({
            "name": n.get("name"), "path": path,
            "parent": _parent_path(path),   # SVG 연결선·부모 매칭용
            "amount": n.get("amount") or 0, "is_leaf": n.get("is_leaf"),
            "item_id": ld.get("item_id"),
            "residual": ld.get("item_id") in residual_ids if ld.get("item_id") else False,
        })
        flat.append({"name": n.get("name"), "depth": depth,
                     "amount": n.get("amount") or 0, "is_leaf": n.get("is_leaf")})
        for ch in (n.get("children") or []):
            _walk(ch, depth + 1)
    roots = tree.get("tree")
    roots = roots if isinstance(roots, list) else ([roots] if roots else [])
    for r in roots:
        if r:
            _walk(r, 0)
    max_depth = max(levels) if levels else 0

    # STEP 2(정합성 자동제안): 미연계 잎의 추천 상위 경로(휴리스틱).
    #  후보 = 트리의 분류(가지) 경로 ∪ 스티칭 스켈레톤 후보. 파선 제안 + 원클릭 수락용.
    branch_paths = [n["path"] for d in levels for n in levels[d]
                    if not n["is_leaf"] and n["path"]]
    cand_parents = sorted(set(branch_paths) | set(stitch_meta.get("candidates") or []))
    for d in levels:
        for n in levels[d]:
            n["suggest"] = ""
            if n["residual"] and n["is_leaf"] and n.get("name"):
                pick, score = _heuristic_pick(n["name"], cand_parents)
                # 현재(잘못된) 부모와 다른 경로만 제안
                if pick and score > 0 and pick != n.get("parent"):
                    n["suggest"] = pick

    # STEP 1(추출) 표시용: 시트별 인식 결과(역할). map_config에서 복원.
    _role_ko = {"leaf": "세부(품목·단가)", "band": "밴드(분류)",
                "seq_leaf": "세부(번호계층)", "seq_tree": "트리(번호계층)",
                "seq_list": "목록", "unknown": "미상"}
    sheets_view = []
    try:
        mc = _json.loads(subd.get("map_config") or "{}") or {}
        roles = {s.get("name"): s.get("role") for s in (stitch_meta.get("sheet_roles") or [])}
        order = mc.get("sheet_order") or list((mc.get("sheets") or {}).keys())
        for nm in order:
            rl = roles.get(nm, "")
            sheets_view.append({"name": nm, "role": rl, "role_ko": _role_ko.get(rl, rl or "—")})
    except Exception:
        sheets_view = []

    # [연계 D] 레벨 → 사용자 분류명 매핑(대/중/소/세/품명/부품…). 깊이 순서 기본값.
    level_names = ["대분류", "중분류", "소분류", "세분류", "품명", "부품", "세부"]

    # [연계 캔버스] 잎에 item_id·residual/manual 플래그를 실어 나른다.
    residual_ids = sorted(residual_view and {rv["item_id"] for rv in residual_view} or set(),
                          key=lambda x: str(x))
    try:
        _mc_all = _json.loads(subd.get("map_config") or "{}") or {}
        manual_ids = [k for k, v in (_mc_all.get("link_overrides") or {}).items() if v == "manual"]
    except Exception:
        manual_ids = []

    snap = get_latest_snapshot(submission_id)
    return render_template("submissions/link.html", sub=subd,
                           levels=levels, max_depth=max_depth, flat_tree=flat,
                           tree_json=_json.dumps(tree.get("tree") or [], ensure_ascii=False),
                           residual_ids_json=_json.dumps(residual_ids, ensure_ascii=False),
                           manual_ids_json=_json.dumps(manual_ids, ensure_ascii=False),
                           level_names_json=_json.dumps(level_names, ensure_ascii=False),
                           total=tree.get("total") or 0,
                           n_residual=len(residual_view),
                           stitch_meta=stitch_meta or {},
                           sheets_view=sheets_view,
                           snapshot_version=(snap or {}).get("version") or 0)


@bp.route("/<submission_id>/link/confirm", methods=["POST"])
@require_role("manager")
def link_confirm(submission_id):
    """[T7] 연계 확정 — 현재 통합 트리를 버전 스냅샷으로 동결 보존.
    금액 불변(스냅샷은 표시/이력용, submission_items는 그대로)."""
    import json as _json
    from db.queries import build_items_tree, create_submission_snapshot
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    items = get_items(submission_id)
    _sm, residual_view = _build_residual_view(dict(sub), items)
    tree = build_items_tree(submission_id)
    version = create_submission_snapshot(
        submission_id,
        tree_json=_json.dumps(tree.get("tree"), ensure_ascii=False),
        total=tree.get("total") or 0,
        n_residual=len(residual_view),
        note=(request.get_json(silent=True) or {}).get("note"))
    return jsonify({"ok": True, "version": version,
                    "total": tree.get("total") or 0,
                    "n_residual": len(residual_view)})


@bp.route("/<submission_id>/link/node", methods=["POST"])
@require_role("manager")
def link_node_status(submission_id):
    """[연계 E·F] 트리 노드 상태 조정(비파괴 우선).

    action:
      · exclude  : 항목을 합계·트리에서 제외(is_header=1, 보존·되돌리기 가능)
      · restore  : 제외 해제(is_header=0)
      · residual : 자동연계(녹색)를 '미연계(빨강)'로 되돌림(link_overrides 저장)
      · unresidual: 미연계 지정 해제
      · delete   : 항목 영구 삭제
    """
    import json as _json
    from db.queries import (set_item_excluded, delete_single_item,
                            recompute_subtotal)
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    p = request.get_json(silent=True) or {}
    item_id = (p.get("item_id") or "").strip()
    action = (p.get("action") or "").strip()
    if not item_id or action not in ("exclude", "restore", "residual",
                                     "unresidual", "delete"):
        return jsonify({"ok": False, "error": "item_id·action 필요"}), 200
    try:
        mc = _json.loads(dict(sub).get("map_config") or "{}") or {}
    except Exception:
        mc = {}
    ov = mc.get("link_overrides") or {}
    if action == "delete":
        delete_single_item(item_id)
        ov.pop(item_id, None)
    elif action == "exclude":
        set_item_excluded(item_id, True)
        ov.pop(item_id, None)
    elif action == "restore":
        set_item_excluded(item_id, False)
    elif action == "residual":
        ov[item_id] = "residual"
    elif action == "unresidual":
        ov.pop(item_id, None)
    mc["link_overrides"] = ov
    update_submission(submission_id, map_config=_json.dumps(mc, ensure_ascii=False))
    recompute_subtotal(submission_id)
    return jsonify({"ok": True})


@bp.route("/<submission_id>/tree/delete-branch", methods=["POST"])
@require_role("manager")
def delete_branch(submission_id):
    """[비교단위 트리] 가지(분류 경로) 삭제 — 그 경로와 하위의 모든 품목을 함께 삭제."""
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    path = ((request.get_json(silent=True) or {}).get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "path가 필요합니다."}), 200
    from db.queries import get_conn, recompute_subtotal
    with get_conn() as c:
        cur = c.execute(
            "DELETE FROM submission_items WHERE submission_id=? AND (path=? OR path LIKE ?)",
            (submission_id, path, path + " > %"))
        n = cur.rowcount
    if n:
        recompute_subtotal(submission_id)
    return jsonify({"ok": True, "deleted": n})


@bp.route("/<submission_id>/tree/reparent-branch", methods=["POST"])
@require_role("manager")
def reparent_branch(submission_id):
    """[연계 캔버스] 가지(묶음) 재부모화 — 최상위로 잘못 잡힌 묶음을 다른 상위 분류
    아래로 통째로 이동. 서브트리 전체(중/소/세/품명/부품)의 path를 새 부모 기준으로
    다시 쓴다. 금액 무변경 → 총액 불변(Δ=0).

    payload: {path: 이동할 가지 경로, target: 대상 상위 경로, merge: 동명 충돌 시 병합 여부}
      · 대상의 자식이 됨(레벨 한 단계 하강): new_base = target + ' > ' + 가지이름.
      · 순환 방지: target이 가지 자신/하위면 거부.
      · 동명 충돌(대상에 같은 이름 자식 존재) & merge=False → '이름 (2)'로 별도 유지(기본).
    """
    import json as _json
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    p = request.get_json(silent=True) or {}
    branch = (p.get("path") or "").strip()
    target = (p.get("target") or "").strip()
    merge = bool(p.get("merge"))
    SEP = " > "
    if not branch or not target:
        return jsonify({"ok": False, "error": "path·target가 필요합니다."}), 200
    if target == branch or target.startswith(branch + SEP):
        return jsonify({"ok": False, "error": "자기 자신 또는 하위로는 이동할 수 없습니다."}), 200
    root_name = branch.split(SEP)[-1]
    new_base = target + SEP + root_name
    if new_base == branch:
        return jsonify({"ok": True, "moved": 0, "note": "변경 없음(이미 해당 위치)"})

    from db.queries import get_conn, recompute_subtotal
    moved_ids = []
    with get_conn() as c:
        rows = c.execute(
            "SELECT item_id, path FROM submission_items "
            "WHERE submission_id=? AND (path=? OR path LIKE ?)",
            (submission_id, branch, branch + SEP + "%")).fetchall()
        moved_ids = [dict(r)["item_id"] for r in rows]
        if not moved_ids:
            return jsonify({"ok": False, "error": "이동할 항목을 찾을 수 없습니다."}), 200

        # 동명 충돌 시(기본) 별도 유지: new_base 를 '이름 (n)'으로 디스앰비규에이션.
        def _collides(nb):
            ph = ",".join("?" * len(moved_ids))
            q = c.execute(
                "SELECT 1 FROM submission_items WHERE submission_id=? "
                "AND (path=? OR path LIKE ?) AND item_id NOT IN (%s) LIMIT 1" % ph,
                [submission_id, nb, nb + SEP + "%"] + moved_ids).fetchone()
            return q is not None
        if not merge and _collides(new_base):
            i = 2
            while _collides(target + SEP + root_name + " (" + str(i) + ")"):
                i += 1
            new_base = target + SEP + root_name + " (" + str(i) + ")"

        # branch 프리픽스를 new_base 로 교체(하위 전체).
        n = 0
        for r in rows:
            d = dict(r)
            old = d["path"] or ""
            newp = new_base + old[len(branch):]
            parts = [x for x in newp.split(SEP) if x.strip()]
            c.execute(
                "UPDATE submission_items SET path=?, depth=?, category=? WHERE item_id=?",
                (newp, len(parts), parts[0] if parts else "기타", d["item_id"]))
            n += 1

    # 수기 이동 표시(초록) + map_config 갱신
    try:
        subd = dict(get_submission(submission_id))
        mc = _json.loads(subd.get("map_config") or "{}") or {}
        ov = mc.get("link_overrides") or {}
        for iid in moved_ids:
            ov[iid] = "manual"
        mc["link_overrides"] = ov
        update_submission(submission_id, map_config=_json.dumps(mc, ensure_ascii=False))
    except Exception:
        pass
    recompute_subtotal(submission_id)   # 금액 불변(재부모화) — 총액 보존
    return jsonify({"ok": True, "moved": n, "new_base": new_base})


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


@bp.route("/<submission_id>/unit-count", methods=["POST"])
@require_role("manager")
def set_unit_count(submission_id):
    """[추출 기준정보] 장비 대수 설정. 대당 단가 표시에 사용(총액/대수)."""
    from db.queries import set_submission_unit_count
    sub = get_submission(submission_id)
    if not sub:
        abort(404)
    p = request.get_json(silent=True) or {}
    n = set_submission_unit_count(submission_id, p.get("unit_count"))
    return jsonify({"ok": True, "unit_count": n})


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
