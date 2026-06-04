"""
LLM 추출 모듈 — Provider 추상화 기반.

provider 선택 → 공통 인터페이스로 호출.
새 모델 추가 시 providers/ 에 파일만 추가하면 됨.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import LLM_MODEL


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent.parent.parent / "docs" / "prompt_extract_v1.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return """당신은 한국 IT 입찰 견적서를 분석하여 구조화된 JSON으로 추출하는 시스템입니다.
순수 JSON만 응답하세요 (설명, 마크다운 펜스 없이).

필수 필드:
{
  "vendor_name": "string",
  "proposal_no": "string|null",
  "proposal_date": "YYYY-MM-DD|null",
  "currency": "KRW|USD",
  "currency_unit": "원",
  "amount_summary": {"subtotal_excl_vat": number, "vat": number, "grand_total": number},
  "category_totals": {"자재": number, "인건비": number, "출장비": number, "영업이익": number, "관리비": number},
  "headers_detected": {},
  "value_normalizations": [],
  "items": [
    {
      "line_no": "string",
      "depth": 0,
      "is_category_header": false,
      "category": "자재|인건비|출장비|영업이익|관리비",
      "parent_path": "string",
      "name_raw": "string",
      "name_normalized": "string",
      "spec": "string|null",
      "data_doc_value": "string|null",
      "quantity": number|null,
      "unit": "string|null",
      "unit_price": number|null,
      "unit_price_currency_in_source": "원|USD",
      "amount": number|null,
      "source_location": "string"
    }
  ],
  "validation": {"items_sum_matches_grand_total": bool, "items_sum_value": number, "discrepancy_pct": number, "warnings": []}
}"""


class LLMExtractorError(Exception):
    pass


def is_api_available(api_key: str = "") -> bool:
    """API 키 설정 여부 확인"""
    return bool(api_key)


def extract_with_llm(parsed_text: str, vendor_name: str = "",
                     max_retries: int = 2,
                     api_key: str = "",
                     provider_id: str = "claude",
                     model: str = None) -> dict:
    """
    파싱된 텍스트를 LLM으로 분석하여 추출 JSON 반환.

    Args:
        parsed_text:  파서가 생성한 입력 텍스트
        vendor_name:  업체명 (프롬프트 컨텍스트용)
        max_retries:  실패 시 재시도 횟수
        api_key:      사용자 API 키
        provider_id:  LLM provider ('claude' | 'gpt' | ...)
        model:        모델 지정 (None이면 provider 기본값)

    Returns:
        추출 JSON (dict)
    """
    if not api_key:
        raise LLMExtractorError(
            "API 키가 설정되지 않았습니다. "
            "프로필(⚙ 내 프로필)에서 API 키를 입력하세요."
        )

    try:
        from extractors.providers import get_provider
        provider = get_provider(provider_id)
    except ValueError as e:
        raise LLMExtractorError(str(e))

    system_prompt = _load_system_prompt()
    user_message = (
        f"다음은 '{vendor_name}' 업체의 견적서 파싱 결과입니다.\n"
        f"지시사항에 따라 추출 JSON을 생성하세요.\n\n"
        f"=== 파싱된 견적서 ===\n{parsed_text}\n=== 끝 ==="
    )

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            from extractors.llm_provider import LLMProviderError
            response_text = provider.extract(
                parsed_text=user_message,
                system_prompt=system_prompt,
                api_key=api_key,
                model=model,
            )

            # 마크다운 펜스 제거
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = lines[1:] if lines[0].startswith("```") else lines
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines)

            return json.loads(cleaned)

        except json.JSONDecodeError as e:
            last_error = f"JSON 파싱 실패 (시도 {attempt+1}): {e}"
            continue
        except Exception as e:
            last_error = f"API 호출 실패 (시도 {attempt+1}): {type(e).__name__}: {e}"
            continue

    raise LLMExtractorError(f"최대 재시도 초과. 마지막 오류: {last_error}")


def extract_with_validation(parsed_text: str, vendor_name: str = "",
                             api_key: str = "",
                             provider_id: str = "claude",
                             model: str = None) -> dict:
    """추출 + 자기 정합성 검증"""
    result = extract_with_llm(
        parsed_text, vendor_name,
        api_key=api_key, provider_id=provider_id, model=model,
    )

    items_sum = sum(
        it.get("amount") or 0
        for it in result.get("items", [])
        if not it.get("is_category_header")
    )
    subtotal = result.get("amount_summary", {}).get("subtotal_excl_vat", 0)
    discrepancy_pct = (abs(items_sum - subtotal) / subtotal * 100) if subtotal else 0

    if "validation" not in result:
        result["validation"] = {}
    result["validation"].update({
        "items_sum_matches_grand_total": (items_sum == subtotal),
        "items_sum_value": items_sum,
        "discrepancy_pct": round(discrepancy_pct, 2),
    })

    if discrepancy_pct > 0:
        warnings = result["validation"].get("warnings", [])
        warnings.append(
            f"정합성 경고: items 합계({items_sum:,}) ≠ subtotal({subtotal:,}), "
            f"차이 {discrepancy_pct:.2f}%"
        )
        result["validation"]["warnings"] = warnings

    return result
