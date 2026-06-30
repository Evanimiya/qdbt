"""
업로드 파일 처리 파이프라인 (새 스키마용).

흐름:
  1. 파일 형식 감지
  2. 파서로 텍스트 추출
  3. LLM으로 구조화 JSON 추출
  4. DB에 submission_items 저장
  5. submission 상태 업데이트

submission_id는 이미 생성된 상태로 전달받음
(파일 업로드 즉시 DB 레코드 생성 → 처리 중 상태 추적 가능)
"""
import json
import shutil
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import UPLOAD_DIR, EXTRACT_DIR, ALLOWED_EXTENSIONS
from db.queries import update_submission, insert_items_bulk, delete_submission_items
from parsers.parse_xlsx import parse_xlsx
from parsers.parse_pdf  import parse_pdf
from parsers.parse_docx import parse_docx
from extractors.llm_extractor import extract_with_validation, is_api_available

# 청킹: 한 조각의 최대 행 수.
# 근거: 71행 OK, 110행 실패 관측 → 분류 채워져 텍스트 큰 점 감안해 30으로 보수적 설정.
# TODO: LLM/모델 성능에 따라 상향 가능 (예: 40~50). 추출 안정화 후 실측으로 조정.
_CHUNK_MAX_ROWS = 30
_CHUNK_CONCURRENCY = 4   # 조각 병렬 추출 동시 개수 (rate limit 고려)


PARSERS = {
    ".xlsx": parse_xlsx,
    ".pdf":  parse_pdf,
    ".docx": parse_docx,
}


class PipelineError(Exception):
    pass


def save_upload(tmp_path: str, original_filename: str) -> Path:
    """임시 파일을 uploads/에 영구 저장 (타임스탬프로 중복 방지)"""
    suffix = Path(original_filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise PipelineError(f"지원하지 않는 형식: {suffix}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(original_filename).stem
    dest = UPLOAD_DIR / f"{stem}_{ts}{suffix}"
    shutil.copy2(tmp_path, dest)
    return dest


def _extract_xlsx_chunked(file_path, sheet_names, vendor_name,
                          api_key, provider_id, model, base_url, verify_ssl,
                          submission_id, log):
    """xlsx를 묶음(중분류) 단위로 조각내 추출하고 합친다.

    조각이 1개뿐이거나 xlsx가 아니면 None 반환 → 호출측이 기존 단일 추출 사용.
    여러 조각이면 각 조각을 추출해 items를 합쳐 반환.
    """
    if file_path.suffix.lower() != ".xlsx":
        return None

    from parsers.parse_xlsx import parse_xlsx_chunks
    from extractors.llm_extractor import extract_with_llm

    try:
        ck = parse_xlsx_chunks(str(file_path), sheet_names=sheet_names,
                               max_rows=_CHUNK_MAX_ROWS)
    except Exception as e:
        log(f"  청킹 분석 실패({type(e).__name__}), 단일 추출로 폴백")
        return None

    chunks = ck["chunks"]
    if len(chunks) <= 1:
        # 조각이 1개면 청킹 의미 없음 → 기존 단일 추출
        return None

    log(f"  청킹: {len(chunks)}개 조각으로 분할 추출 (병렬 {_CHUNK_CONCURRENCY}개씩)")

    currency = "KRW"
    currency_unit = "원"

    # 조각별 추출 함수 (병렬 실행용)
    def _extract_one(idx_chunk):
        idx, chunk = idx_chunk
        r = extract_with_llm(
            chunk, vendor_name=vendor_name,
            api_key=api_key, provider_id=provider_id, model=model,
            base_url=base_url or None, verify_ssl=verify_ssl,
        )
        return idx, r

    # 병렬 호출 (동시 개수 제한). 결과는 idx로 다시 정렬해 순서 보존.
    import concurrent.futures as _cf
    results_by_idx = {}
    done_count = [0]
    with _cf.ThreadPoolExecutor(max_workers=_CHUNK_CONCURRENCY) as ex:
        futures = {ex.submit(_extract_one, (i, c)): i
                   for i, c in enumerate(chunks)}
        for fut in _cf.as_completed(futures):
            try:
                idx, r = fut.result()
                results_by_idx[idx] = r
            except Exception as e:
                idx = futures[fut]
                log(f"  조각 {idx+1} 실패({type(e).__name__}) — 건너뜀")
                results_by_idx[idx] = {"items": []}
            done_count[0] += 1
            update_submission(submission_id,
                              extraction_status="processing",
                              extraction_error=f"추출 중 {done_count[0]}/{len(chunks)}")
            log(f"  진행 {done_count[0]}/{len(chunks)} 완료")

    # 원래 조각 순서대로 합치기 (순서 보존)
    merged_items = []
    for i in range(len(chunks)):
        r = results_by_idx.get(i, {})
        merged_items.extend(r.get("items", []))
        if r.get("currency"):
            currency = r["currency"]
        if r.get("currency_unit"):
            currency_unit = r["currency_unit"]

    # 합친 결과로 정합성/소계 계산
    items_sum = sum(
        it.get("amount") or 0 for it in merged_items
        if not it.get("is_category_header")
    )
    result = {
        "items": merged_items,
        "currency": currency,
        "currency_unit": currency_unit,
        "amount_summary": {"subtotal_excl_vat": items_sum},
        "validation": {
            "items_sum_value": items_sum,
            "n_chunks": len(chunks),
            "warnings": [],
        },
    }
    return result


def parse_file(file_path: Path, sheet_names=None) -> tuple[str, str]:
    """파일 파싱 → (format, parsed_text)

    sheet_names: xlsx인 경우 추출할 시트 목록. None이면 전체.
    """
    suffix = file_path.suffix.lower()
    parser = PARSERS.get(suffix)
    if not parser:
        raise PipelineError(f"파서 없음: {suffix}")
    # xlsx 파서만 sheet_names를 받는다 (다른 파서엔 전달 안 함)
    if suffix == ".xlsx" and sheet_names:
        return suffix.lstrip("."), parser(str(file_path), sheet_names=sheet_names)
    return suffix.lstrip("."), parser(str(file_path))


def run_extraction(submission_id: str, file_path: Path,
                   vendor_name: str, api_key: str = "",
                   provider_id: str = "claude", model: str = None,
                   base_url: str = None, verify_ssl: bool = True,
                   sheet_names=None) -> dict:
    """
    파일 처리 전체 실행.

    Args:
        submission_id: DB submission ID
        file_path:     저장된 파일 경로
        vendor_name:   업체명
        api_key:       사용자 API 키
        provider_id:   LLM provider ('claude' | 'gpt' | ...)
        model:         모델 지정 (None이면 provider 기본값)
        base_url:      커스텀 API 엔드포인트 (None이면 공식 엔드포인트)
        verify_ssl:    SSL 인증서 검증 여부 (폐쇄망 환경에서 False로 설정)
    """
    import sys, time as _time
    def _log(msg):
        print(f"[추출:{submission_id[:8]}] {msg}", file=sys.stderr, flush=True)

    try:
        _t0 = _time.time()
        update_submission(submission_id, extraction_status="processing")
        _log("1. 파싱 시작")

        fmt, parsed_text = parse_file(file_path, sheet_names=sheet_names)
        _log(f"2. 파싱 완료 ({len(parsed_text):,}자, {_time.time()-_t0:.1f}초)")

        if not is_api_available(api_key):
            raise PipelineError(
                "API 키가 설정되지 않았습니다. "
                "프로필(⚙ 내 프로필)에서 API 키를 입력하세요."
            )

        _log("3. LLM 호출 시작 (응답 대기...)")
        # xlsx이고 청킹 가능하면 조각별 추출, 아니면 기존 단일 추출
        extraction = _extract_xlsx_chunked(
            file_path, sheet_names, vendor_name,
            api_key, provider_id, model, base_url, verify_ssl,
            submission_id, _log,
        )
        if extraction is None:
            # 청킹 비대상(비 xlsx 등) → 기존 단일 추출
            extraction = extract_with_validation(
                parsed_text, vendor_name=vendor_name,
                api_key=api_key, provider_id=provider_id, model=model,
                base_url=base_url or None, verify_ssl=verify_ssl,
            )
        _log(f"4. LLM 응답 받음 ({len(extraction.get('items', []))}개 항목, {_time.time()-_t0:.1f}초)")

        # 추출 JSON 저장
        ext_path = EXTRACT_DIR / f"{submission_id}.json"
        ext_path.write_text(json.dumps(extraction, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        _log("5. JSON 저장 완료")

        # ── USD 항목 사후처리 ──────────────────────────────────────
        # unit_price_currency_in_source='USD'이고 amount(KRW)가 있는 항목에서
        # 환율 도출 후 unit_price를 원화로 변환.
        items = extraction.get("items", [])
        fx_rate_derived = None
        for it in items:
            currency_in_src = it.get("unit_price_currency_in_source", "")
            if currency_in_src not in ("USD", "$"):
                continue
            up   = it.get("unit_price")
            qty  = it.get("quantity")
            amt  = it.get("amount")
            if up and qty and amt and up > 0:
                candidate = amt / (up * qty)
                # 합리적 환율 범위 (500 ~ 2000)
                if 500 <= candidate <= 2000:
                    fx_rate_derived = round(candidate)
                    break

        if fx_rate_derived:
            for it in items:
                if it.get("unit_price_currency_in_source") in ("USD", "$"):
                    up = it.get("unit_price")
                    if up is not None:
                        it["unit_price_orig"] = up
                        it["unit_price"] = round(up * fx_rate_derived)

        # ── DB 저장 (기존 아이템 먼저 삭제 후 재삽입) ───────────
        _log(f"6. DB 저장 시작 ({len(items)}개 항목)")
        delete_submission_items(submission_id)
        insert_items_bulk(submission_id, items)
        _log(f"7. DB 저장 완료 ({_time.time()-_t0:.1f}초)")

        n_items = sum(1 for it in items if not it.get("is_category_header"))

        has_usd = bool(fx_rate_derived) or extraction.get("currency") == "USD"

        # submission 메타 업데이트 (이전 오류 메시지도 초기화)
        update_submission(
            submission_id,
            extraction_status="done",
            extraction_error=None,
            currency=extraction.get("currency", "KRW"),
            currency_unit=extraction.get("currency_unit", "원"),
            subtotal_excl_vat=extraction.get("amount_summary", {}).get("subtotal_excl_vat"),
            vat=extraction.get("amount_summary", {}).get("vat"),
            grand_total=extraction.get("amount_summary", {}).get("grand_total"),
            has_usd_items=1 if has_usd else 0,
            fx_rate_used=fx_rate_derived,
        )

        warnings = extraction.get("validation", {}).get("warnings", [])
        return {
            "status": "success",
            "n_items": n_items,
            "subtotal": extraction.get("amount_summary", {}).get("subtotal_excl_vat"),
            "warnings": warnings,
        }

    except PipelineError as e:
        update_submission(submission_id,
                          extraction_status="failed",
                          extraction_error=str(e))
        raise

    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        update_submission(submission_id,
                          extraction_status="failed",
                          extraction_error=msg)
        raise PipelineError(msg)
