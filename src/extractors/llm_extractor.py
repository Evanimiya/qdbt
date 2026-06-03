"""
Anthropic Claude API를 사용한 견적서 자동 추출.

입력: 파싱된 텍스트 (parsers 모듈 출력)
출력: 추출 JSON (data/extractions/extraction_*.json 형식)

사용:
    from src.extractors.llm_extractor import extract_with_llm

    parsed_text = parse_xlsx("data/samples/새업체_입찰서.xlsx")
    extraction = extract_with_llm(parsed_text, vendor_name="새업체")
    # extraction은 dict (JSON)
"""
import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import ANTHROPIC_API_KEY, LLM_MODEL

# Anthropic 라이브러리 (선택적 import — 키 없을 때도 모듈 로드 가능)
try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    Anthropic = None


# 추출 프롬프트 (docs/prompt_extract_v1.md에서 로드)
def load_extraction_prompt():
    prompt_path = Path(__file__).parent.parent.parent / "docs" / "prompt_extract_v1.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    # Fallback - 기본 프롬프트
    return """당신은 한국 IT 입찰 견적서를 분석하여 구조화된 데이터로 추출하는 전문 시스템입니다.

다음 JSON 스키마로만 응답하시오. 설명, 주석, 마크다운 펜스 없이 순수 JSON만.

{
  "vendor_name": "string",
  "proposal_no": "string | null",
  "proposal_date": "YYYY-MM-DD | null",
  "currency": "KRW | USD",
  "currency_unit": "원",
  "amount_summary": {
    "subtotal_excl_vat": number | null,
    "vat": number | null,
    "grand_total": number | null
  },
  "category_totals": {
    "자재": number | null,
    "인건비": number | null,
    "출장비": number | null,
    "영업이익": number | null,
    "관리비": number | null
  },
  "headers_detected": {"표준헤더명": "원본 표기"},
  "value_normalizations": [],
  "items": [
    {
      "line_no": "string",
      "depth": 0 | 1 | 2,
      "is_category_header": true | false,
      "category": "자재 | 인건비 | 출장비 | 영업이익 | 관리비",
      "parent_path": "string",
      "name_raw": "string",
      "name_normalized": "string",
      "spec": "string | null",
      "data_doc_value": "string | null",
      "quantity": number | null,
      "unit": "string | null",
      "unit_price": number | null,
      "unit_price_currency_in_source": "원 | USD",
      "amount": number | null,
      "source_location": "string"
    }
  ],
  "validation": {
    "items_sum_matches_grand_total": true | false,
    "items_sum_value": number,
    "discrepancy_pct": number,
    "warnings": []
  }
}
"""


class LLMExtractorError(Exception):
    """LLM 추출 관련 오류"""
    pass


def is_api_available():
    """API 사용 가능 여부 확인"""
    return ANTHROPIC_AVAILABLE and ANTHROPIC_API_KEY


def extract_with_llm(parsed_text: str, vendor_name: str = "", max_retries: int = 2) -> dict:
    """
    파싱된 텍스트를 Claude API로 분석하여 추출 JSON 생성.

    Args:
        parsed_text: 파서가 생성한 LLM 입력용 텍스트
        vendor_name: 업체 이름 (프롬프트 컨텍스트용)
        max_retries: 실패 시 재시도 횟수

    Returns:
        추출 JSON (dict)

    Raises:
        LLMExtractorError: API 키 없음, 모듈 없음, JSON 파싱 실패 등
    """
    if not ANTHROPIC_AVAILABLE:
        raise LLMExtractorError(
            "anthropic 라이브러리가 설치되지 않았습니다. "
            "`pip install anthropic` 실행 후 다시 시도하세요."
        )

    if not ANTHROPIC_API_KEY:
        raise LLMExtractorError(
            "ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다. "
            "Replit Secrets 또는 .env 파일에 키를 추가하세요."
        )

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    system_prompt = load_extraction_prompt()

    user_message = f"""다음은 한 업체({vendor_name})의 견적서를 파싱한 결과입니다.
위 시스템 프롬프트의 지침에 따라 추출 JSON을 생성하세요.

=== 파싱된 견적서 ===
{parsed_text}

=== 끝 ===

출력: 순수 JSON만 (설명, 마크다운 펜스, 주석 없이)"""

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model=LLM_MODEL,
                max_tokens=8000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )

            # 응답 텍스트 추출
            response_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    response_text += block.text

            # JSON 파싱
            # 마크다운 펜스 제거 (LLM이 가끔 추가)
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                # 첫 줄(```json)과 마지막 줄(```) 제거
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines)

            try:
                result = json.loads(cleaned)
                return result
            except json.JSONDecodeError as e:
                last_error = f"JSON 파싱 실패: {e}\n응답 앞부분: {cleaned[:500]}"
                continue

        except Exception as e:
            last_error = f"API 호출 실패: {type(e).__name__}: {e}"
            continue

    raise LLMExtractorError(f"최대 재시도 횟수 초과. 마지막 오류: {last_error}")


def extract_with_validation(parsed_text: str, vendor_name: str = "") -> dict:
    """
    추출 + 자기 정합성 검증 (items 합계 vs subtotal 비교).

    LLM이 추출한 결과의 items_sum과 subtotal_excl_vat이 일치하는지 확인.
    불일치 시 warning에 기록 (자동 재시도는 하지 않음 — 사람 검수 권장).
    """
    result = extract_with_llm(parsed_text, vendor_name)

    # 자기 정합성 검증
    items_sum = sum(
        it.get("amount") or 0
        for it in result.get("items", [])
        if not it.get("is_category_header")
    )
    subtotal = result.get("amount_summary", {}).get("subtotal_excl_vat", 0)

    discrepancy = abs(items_sum - subtotal) if subtotal else 0
    discrepancy_pct = (discrepancy / subtotal * 100) if subtotal else 0

    # validation 필드 보강
    if "validation" not in result:
        result["validation"] = {}
    result["validation"]["items_sum_matches_grand_total"] = (discrepancy == 0)
    result["validation"]["items_sum_value"] = items_sum
    result["validation"]["discrepancy_pct"] = round(discrepancy_pct, 2)

    if discrepancy > 0:
        warnings = result["validation"].get("warnings", [])
        warnings.append(
            f"자기 정합성 검증 실패: items 합계({items_sum:,}) ≠ subtotal({subtotal:,}), "
            f"차이 {discrepancy:,}원 ({discrepancy_pct:.2f}%)"
        )
        result["validation"]["warnings"] = warnings

    return result
