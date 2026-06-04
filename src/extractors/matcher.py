"""
카탈로그 매칭 LLM 모듈.

submission_items의 라인 아이템을 catalog_items와 매칭.

흐름:
  1. submission의 라인 아이템 목록 준비
  2. 카탈로그 품목 목록 준비
  3. LLM에 매칭 요청 → 각 아이템에 가장 적합한 catalog_item 추천
  4. 결과를 submission_items.catalog_item_id + match_status='suggested'로 저장
  5. 담당자가 검수 화면에서 confirmed/unmatched 결정
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


MATCH_PROMPT = """당신은 입찰 견적서의 라인 아이템을 표준 품목 카탈로그와 매칭하는 전문 시스템입니다.

## 작업
아래 "라인 아이템 목록"의 각 항목을 "카탈로그 품목 목록"과 매칭하세요.

## 매칭 규칙
1. 품목명(name_normalized/name_raw)과 카탈로그의 name_canonical, aliases를 비교
2. spec(규격)도 참고하여 더 정확한 매칭
3. 동일하거나 실질적으로 같은 품목이면 매칭
4. 적합한 카탈로그 품목이 없으면 null 반환
5. 카테고리 헤더(is_header=true)는 건너뜀

## 출력 형식 (순수 JSON만, 설명 없이)
{
  "matches": [
    {
      "item_id": "submission_item의 item_id",
      "catalog_item_id": "매칭된 카탈로그 ID 또는 null",
      "confidence": 0.0~1.0,
      "reason": "매칭 근거 한 줄"
    }
  ]
}
"""


def build_match_input(items: list, catalog_items: list) -> str:
    """LLM 입력 텍스트 구성"""
    # 라인 아이템
    item_lines = []
    for it in items:
        if it.get("is_header"):
            continue
        item_lines.append(
            f"  - item_id={it['item_id']}"
            f" | category={it.get('category','')}"
            f" | name={it.get('name_normalized') or it.get('name_raw','')}"
            f" | spec={it.get('spec','')}"
            f" | qty={it.get('quantity','')} {it.get('unit','')}"
        )

    # 카탈로그 품목
    cat_lines = []
    for ci in catalog_items:
        try:
            aliases = json.loads(ci.get("aliases") or "[]")
        except Exception:
            aliases = []
        cat_lines.append(
            f"  - catalog_item_id={ci['catalog_item_id']}"
            f" | name={ci['name_canonical']}"
            f" | category={ci.get('category_name','')}"
            f" | unit={ci.get('unit_std','')}"
            f" | aliases={', '.join(aliases[:3])}"
        )

    return (
        f"## 라인 아이템 목록 ({len(item_lines)}개)\n"
        + "\n".join(item_lines)
        + f"\n\n## 카탈로그 품목 목록 ({len(cat_lines)}개)\n"
        + "\n".join(cat_lines)
    )


def run_llm_matching(items: list, catalog_items: list,
                     api_key: str, provider_id: str = "claude",
                     model: str = None) -> list:
    """
    LLM으로 라인 아이템 ↔ 카탈로그 매칭 실행.

    Returns:
        list of {item_id, catalog_item_id, confidence, reason}
    """
    if not api_key:
        raise ValueError("API 키가 설정되지 않았습니다.")

    # 카테고리 헤더 제외
    target_items = [it for it in items if not it.get("is_header")]
    if not target_items:
        return []

    if not catalog_items:
        # 카탈로그가 비어있으면 전부 null
        return [
            {"item_id": it["item_id"], "catalog_item_id": None,
             "confidence": 0.0, "reason": "카탈로그 품목 없음"}
            for it in target_items
        ]

    from extractors.providers import get_provider
    provider = get_provider(provider_id)

    input_text = build_match_input(target_items, catalog_items)

    last_error = None
    for attempt in range(3):
        try:
            response_text = provider.extract(
                parsed_text=input_text,
                system_prompt=MATCH_PROMPT,
                api_key=api_key,
                model=model,
            )

            # 마크다운 펜스 제거
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines)

            result = json.loads(cleaned)
            return result.get("matches", [])

        except json.JSONDecodeError as e:
            last_error = f"JSON 파싱 실패: {e}"
            continue
        except Exception as e:
            last_error = f"API 오류: {e}"
            break

    raise RuntimeError(f"매칭 LLM 실패: {last_error}")


def save_match_suggestions(conn, matches: list):
    """
    LLM 매칭 결과를 submission_items에 저장.
    match_status = 'suggested'
    """
    from datetime import datetime
    for m in matches:
        conn.execute("""
            UPDATE submission_items
            SET catalog_item_id = ?,
                match_confidence = ?,
                match_status = ?,
                match_note = ?
            WHERE item_id = ?
        """, (
            m.get("catalog_item_id"),
            m.get("confidence", 0),
            "suggested" if m.get("catalog_item_id") else "unmatched",
            m.get("reason", ""),
            m["item_id"],
        ))
    conn.commit()


def confirm_match(conn, item_id: str, catalog_item_id: str | None,
                  submission_id: str):
    """
    담당자가 매칭을 확정.
    catalog_item_id가 None이면 'unmatched'로 확정.
    확정 시 price_history 자동 생성.
    """
    status = "confirmed" if catalog_item_id else "unmatched"
    conn.execute("""
        UPDATE submission_items
        SET catalog_item_id = ?, match_status = ?
        WHERE item_id = ?
    """, (catalog_item_id, status, item_id))

    # price_history 생성 (매칭 확정 시만)
    if catalog_item_id:
        row = conn.execute("""
            SELECT si.*, s.vendor_name, b.due_date, p.name as project_name
            FROM submission_items si
            JOIN submissions s USING (submission_id)
            JOIN bids b USING (bid_id)
            JOIN projects p USING (project_id)
            WHERE si.item_id = ?
        """, (item_id,)).fetchone()

        if row:
            import uuid
            conn.execute("""
                INSERT OR IGNORE INTO price_history
                    (record_id, catalog_item_id, submission_id, item_id,
                     vendor_name, bid_date, project_name,
                     quantity, unit, unit_price, amount, spec_snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(uuid.uuid4()),
                catalog_item_id, submission_id, item_id,
                row["vendor_name"], row["due_date"], row["project_name"],
                row["quantity"], row["unit"],
                row["unit_price"], row["amount"],
                row["spec"],
            ))

    conn.commit()
