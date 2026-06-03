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
from db.queries import update_submission, insert_items_bulk
from parsers.parse_xlsx import parse_xlsx
from parsers.parse_pdf  import parse_pdf
from parsers.parse_docx import parse_docx
from extractors.llm_extractor import extract_with_validation, is_api_available


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


def parse_file(file_path: Path) -> tuple[str, str]:
    """파일 파싱 → (format, parsed_text)"""
    suffix = file_path.suffix.lower()
    parser = PARSERS.get(suffix)
    if not parser:
        raise PipelineError(f"파서 없음: {suffix}")
    return suffix.lstrip("."), parser(str(file_path))


def run_extraction(submission_id: str, file_path: Path, vendor_name: str) -> dict:
    """
    파일 처리 전체 실행.

    submission의 extraction_status를 단계별로 업데이트:
      pending → processing → done (또는 failed)
    """
    try:
        update_submission(submission_id, extraction_status="processing")

        # 파싱
        fmt, parsed_text = parse_file(file_path)

        if not is_api_available():
            raise PipelineError(
                "ANTHROPIC_API_KEY가 설정되지 않았습니다. "
                "Replit Secrets에 키를 추가하세요."
            )

        # LLM 추출
        extraction = extract_with_validation(parsed_text, vendor_name=vendor_name)

        # 추출 JSON 저장
        ext_path = EXTRACT_DIR / f"{submission_id}.json"
        ext_path.write_text(json.dumps(extraction, ensure_ascii=False, indent=2),
                            encoding="utf-8")

        # DB 저장
        items = extraction.get("items", [])
        insert_items_bulk(submission_id, items)

        n_items = sum(1 for it in items if not it.get("is_category_header"))

        # submission 메타 업데이트
        update_submission(
            submission_id,
            extraction_status="done",
            currency=extraction.get("currency", "KRW"),
            currency_unit=extraction.get("currency_unit", "원"),
            subtotal_excl_vat=extraction.get("amount_summary", {}).get("subtotal_excl_vat"),
            vat=extraction.get("amount_summary", {}).get("vat"),
            grand_total=extraction.get("amount_summary", {}).get("grand_total"),
            has_usd_items=1 if extraction.get("currency") == "USD" else 0,
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
