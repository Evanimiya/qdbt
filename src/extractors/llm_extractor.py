"""
LLM 추출 모듈 — Provider 추상화 + 청크 분할 지원.

항목이 많은 견적서(100개 이상)는 청크로 분할하여 여러 번 LLM 호출 후 합침.

현재 전략: B (청크 분할)
  - CHUNK_LINE_LIMIT 행 단위로 파싱 텍스트를 분할
  - 각 청크마다 LLM 호출 → items 추출
  - 첫 번째 청크에서 메타데이터(vendor, date, 총액) 추출
  - 모든 청크의 items를 합쳐서 반환

v1.0.0 이후 고려: D (파서 강화)
  - docs/CHANGELOG.md [v1.0.0] 섹션 참조
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 청크당 최대 행 수 (R001~R150 → 150행)
# 항목 1개당 파싱 텍스트 약 80자 → 150행 ≈ 12,000자 → 출력 약 6,000토큰
CHUNK_LINE_LIMIT = 150


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


CHUNK_SYSTEM_PROMPT = """당신은 한국 IT 입찰 견적서의 일부 구간을 분석하여 라인 아이템만 추출하는 시스템입니다.
순수 JSON만 응답하세요 (설명, 마크다운 펜스 없이).

★ parent_path 규칙 (매우 중요):
- 각 품목이 속한 분류 경로를 대분류부터 끝까지 전부 적으세요.
- 구분자는 반드시 " > " (공백-부등호-공백)로 통일하세요.
- 모든 단계를 포함하세요. 중간 단계를 절대 생략하지 마세요.
  예: 엑셀이 "재료비 / 기구부 / 차폐 / Maint Door"이면
      parent_path = "재료비 > 기구부 > 차폐" (품명 Maint Door 제외한 상위 전체)
  예: 엑셀이 "인건비 / 인건비(협력사site) / 설계 / 기구"이면
      parent_path = "인건비 > 인건비(협력사site) > 설계"
- 병합으로 비어 있는 상위 분류는 위 행에서 상속해 채우세요.
  (대분류·중분류가 세로 병합으로 빈 칸이면 위에서 이어받기)
- parent_path에 품명 자체는 넣지 마세요 (품명은 name_normalized).
- 대분류(재료비/인건비/출장비/영업이익/관리비 등)를 절대 빠뜨리지 마세요.

출력 형식 (items 배열만):
{
  "items": [
    {
      "line_no": "string",
      "depth": 0,
      "is_category_header": false,
      "category": "자재|인건비|출장비|영업이익|관리비",
      "parent_path": "대분류 > 중분류 > 소분류 (전체 경로, 구분자 ' > ')",
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
  ]
}"""


class LLMExtractorError(Exception):
    pass


def is_api_available(api_key: str = "") -> bool:
    return bool(api_key)


def _clean_json(response_text: str) -> str:
    """LLM 응답에서 마크다운 펜스 제거"""
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    return cleaned


def _call_llm(provider, system_prompt: str, user_message: str,
              api_key: str, model: str, max_retries: int = 2,
              base_url: str = None, verify_ssl: bool = True) -> dict:
    """LLM 단일 호출 + 재시도"""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response_text = provider.extract(
                parsed_text=user_message,
                system_prompt=system_prompt,
                api_key=api_key,
                model=model,
                base_url=base_url or None,
                verify_ssl=verify_ssl,
            )
            return json.loads(_clean_json(response_text))
        except json.JSONDecodeError as e:
            last_error = f"JSON 파싱 실패 — {e}"
            continue
        except Exception as e:
            last_error = f"API 호출 실패 — {type(e).__name__}: {e}"
            continue
    raise LLMExtractorError(f"최대 재시도 초과. 마지막 오류: {last_error}")


def _count_lines(parsed_text: str) -> int:
    """파싱 텍스트의 R행 수 카운트"""
    return sum(1 for line in parsed_text.splitlines()
               if line.strip().startswith("R") and line[1:4].isdigit())


def _split_into_chunks(parsed_text: str, chunk_size: int) -> list[str]:
    """
    파싱 텍스트를 chunk_size 행씩 분할.

    헤더(### Sheet, Dimensions 등)는 모든 청크에 포함시켜
    LLM이 컨텍스트를 잃지 않도록 함.
    """
    lines = parsed_text.splitlines()

    # 헤더 행 수집 (R로 시작하지 않는 메타 정보)
    header_lines = []
    data_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("R") and len(stripped) > 3 and stripped[1:4].isdigit():
            data_lines.append(line)
        else:
            header_lines.append(line)

    header_text = "\n".join(header_lines)

    # 청크 분할
    chunks = []
    for i in range(0, len(data_lines), chunk_size):
        chunk_data = data_lines[i:i + chunk_size]
        chunk_text = header_text + "\n" + "\n".join(chunk_data)
        chunks.append(chunk_text)

    return chunks if chunks else [parsed_text]


def extract_with_llm(parsed_text: str, vendor_name: str = "",
                     max_retries: int = 2,
                     api_key: str = "",
                     provider_id: str = "claude",
                     model: str = None,
                     base_url: str = None,
                     verify_ssl: bool = True) -> dict:
    """
    파싱된 텍스트를 LLM으로 분석하여 추출 JSON 반환.

    항목이 CHUNK_LINE_LIMIT(150)을 초과하면 자동으로 청크 분할 처리.
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

    n_lines = _count_lines(parsed_text)

    # ── 단일 호출 (150행 이하) ──────────────────────
    if n_lines <= CHUNK_LINE_LIMIT:
        system_prompt = _load_system_prompt()
        user_message = (
            f"다음은 '{vendor_name}' 업체의 견적서 파싱 결과입니다.\n"
            f"지시사항에 따라 추출 JSON을 생성하세요.\n\n"
            f"=== 파싱된 견적서 ===\n{parsed_text}\n=== 끝 ==="
        )
        return _call_llm(provider, system_prompt, user_message,
                         api_key, model, max_retries, base_url=base_url,
                         verify_ssl=verify_ssl)

    # ── 청크 분할 (150행 초과) ──────────────────────
    chunks = _split_into_chunks(parsed_text, CHUNK_LINE_LIMIT)
    n_chunks = len(chunks)

    # 1단계: 첫 번째 청크로 메타데이터 + 아이템 추출
    system_prompt = _load_system_prompt()
    first_msg = (
        f"다음은 '{vendor_name}' 업체 견적서의 1/{n_chunks} 구간입니다.\n"
        f"메타데이터(vendor, date, 총액 등)와 이 구간의 items를 추출하세요.\n"
        f"items_sum은 전체 합계가 아닌 이 구간의 합계입니다.\n\n"
        f"=== 파싱된 견적서 (청크 1/{n_chunks}) ===\n{chunks[0]}\n=== 끝 ==="
    )
    result = _call_llm(provider, system_prompt, first_msg,
                       api_key, model, max_retries, base_url=base_url,
                       verify_ssl=verify_ssl)

    all_items = result.get("items", [])

    # 2단계: 나머지 청크에서 items만 추출
    for i, chunk in enumerate(chunks[1:], start=2):
        chunk_msg = (
            f"다음은 '{vendor_name}' 업체 견적서의 {i}/{n_chunks} 구간입니다.\n"
            f"이 구간에 있는 라인 아이템만 추출하세요.\n\n"
            f"=== 파싱된 견적서 (청크 {i}/{n_chunks}) ===\n{chunk}\n=== 끝 ==="
        )
        try:
            chunk_result = _call_llm(provider, CHUNK_SYSTEM_PROMPT,
                                     chunk_msg, api_key, model, max_retries,
                                     base_url=base_url, verify_ssl=verify_ssl)
            all_items.extend(chunk_result.get("items", []))
        except LLMExtractorError as e:
            # 일부 청크 실패 시 경고만 남기고 계속
            warnings = result.setdefault("validation", {}).setdefault("warnings", [])
            warnings.append(f"청크 {i}/{n_chunks} 추출 실패: {e}")

    result["items"] = all_items
    result.setdefault("validation", {}).setdefault("warnings", []).append(
        f"청크 분할 처리: {n_chunks}개 청크, 총 {len(all_items)}개 항목"
    )
    return result


def extract_with_validation(parsed_text: str, vendor_name: str = "",
                             api_key: str = "",
                             provider_id: str = "claude",
                             model: str = None,
                             base_url: str = None,
                             verify_ssl: bool = True) -> dict:
    """추출 + 자기 정합성 검증"""
    result = extract_with_llm(
        parsed_text, vendor_name,
        api_key=api_key, provider_id=provider_id, model=model,
        base_url=base_url, verify_ssl=verify_ssl,
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
