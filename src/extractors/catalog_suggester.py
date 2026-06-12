"""
카탈로그 자동 제안 모듈 (Phase 3-A).

추출 완료 직후 실행:
  1. submission_items 중 카탈로그 미연결 항목 수집
  2. 기존 catalog_items와 LLM으로 비교
  3. 두 가지 제안 생성:
     - 'new_item'  : 카탈로그에 없는 신규 품목 → 추가 제안
     - 'similar'   : 기존 품목과 유사 → 연결 제안
  4. catalog_suggestions 테이블에 저장
  5. 담당자가 제출서 화면에서 검토 후 확정

설계 원칙:
  - 도메인 무관 (IT/설비/용역 모두 동일 로직)
  - LLM이 제안, 사람이 확정
  - 카탈로그가 쌓일수록 'similar' 제안 비율 증가
"""
import json
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


SUGGEST_PROMPT = """당신은 입찰 견적서의 라인 아이템을 분석하여 표준 품목 카탈로그 관리를 돕는 시스템입니다.

## 작업
아래 "분석 대상 라인 아이템" 목록을 "기존 카탈로그 품목" 목록과 비교하여:
1. 기존 카탈로그에 유사한 품목이 있으면 → 'similar' 제안
2. 유사한 품목이 없으면 → 'new_item' 제안 (표준 품목명 생성)

## 규칙
- 품목명, 사양, 단위를 종합적으로 비교
- 같은 품목을 업체마다 다르게 표기한 경우 'similar'로 분류
- 도메인(IT/설비/용역)이 달라도 동일 규칙 적용
- 카테고리 헤더(is_header=true)는 건너뜀
- 유사도 0.7 이상이면 'similar', 미만이면 'new_item' 권장

## 출력 형식 (순수 JSON만, 설명 없이)
{
  "suggestions": [
    {
      "item_id": "라인 아이템 ID",
      "suggestion_type": "new_item 또는 similar",
      "suggested_name": "표준 품목명 (new_item일 때 생성, similar일 때 기존 name_canonical)",
      "suggested_aliases": ["현재 name_raw를 별칭으로 추가"],
      "suggested_spec": "주요 스펙 항목 (쉼표 구분)",
      "suggested_category": "자재|인건비|출장비|영업이익|관리비|기타",
      "matched_catalog_item_id": "유사 품목 ID (similar일 때만, 없으면 null)",
      "similarity_score": 0.0,
      "similarity_reason": "유사 판단 근거 한 줄"
    }
  ]
}
"""


def _build_suggest_input(items: list, catalog_items: list) -> str:
    """LLM 입력 텍스트 구성"""
    item_lines = []
    for it in items:
        if it.get("is_header"):
            continue
        item_lines.append(
            f"  - item_id={it['item_id']}"
            f" | name={it.get('name_normalized') or it.get('name_raw', '')}"
            f" | spec={it.get('spec', '')}"
            f" | category={it.get('category', '')}"
            f" | unit={it.get('unit', '')}"
        )

    cat_lines = []
    for ci in catalog_items:
        try:
            aliases = json.loads(ci.get("aliases") or "[]")[:2]
        except Exception:
            aliases = []
        cat_lines.append(
            f"  - id={ci['catalog_item_id']}"
            f" | name={ci['name_canonical']}"
            f" | category={ci.get('category_name', '')}"
            f" | unit={ci.get('unit_std', '')}"
            f" | aliases={', '.join(aliases)}"
        )

    cat_section = (
        "\n".join(cat_lines)
        if cat_lines
        else "  (아직 등록된 카탈로그 품목 없음 — 모두 new_item으로 제안)"
    )

    return (
        f"## 분석 대상 라인 아이템 ({len(item_lines)}개)\n"
        + "\n".join(item_lines)
        + f"\n\n## 기존 카탈로그 품목 ({len(cat_lines)}개)\n"
        + cat_section
    )


def run_catalog_suggestion(
    submission_id: str,
    items: list,
    catalog_items: list,
    api_key: str,
    provider_id: str = "claude",
    model: str = None,
) -> list:
    """
    LLM으로 신규/유사 품목 감지 후 제안 목록 반환.

    Args:
        submission_id:  제출서 ID
        items:          submission_items (dict 리스트)
        catalog_items:  catalog_items (dict 리스트)
        api_key:        사용자 API 키
        provider_id:    LLM provider
        model:          모델 지정 (None이면 기본값)

    Returns:
        제안 목록 [{suggestion_id, item_id, suggestion_type, ...}]
    """
    if not api_key:
        raise ValueError("API 키가 설정되지 않았습니다.")

    # 카테고리 헤더 제외
    target_items = [it for it in items if not it.get("is_header")]
    if not target_items:
        return []

    from extractors.providers import get_provider
    provider = get_provider(provider_id)

    input_text = _build_suggest_input(target_items, catalog_items)

    last_error = None
    for attempt in range(3):
        try:
            response_text = provider.extract(
                parsed_text=input_text,
                system_prompt=SUGGEST_PROMPT,
                api_key=api_key,
                model=model,
            )
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines)

            result = json.loads(cleaned)
            raw_suggestions = result.get("suggestions", [])

            # suggestion_id 부여
            suggestions = []
            for s in raw_suggestions:
                if not s.get("item_id"):
                    continue
                suggestions.append({
                    "suggestion_id":            str(uuid.uuid4()),
                    "submission_id":            submission_id,
                    "item_id":                  s["item_id"],
                    "suggestion_type":          s.get("suggestion_type", "new_item"),
                    "suggested_name":           s.get("suggested_name"),
                    "suggested_category_id":    None,  # 카테고리명으로 나중에 매핑
                    "suggested_category_name":  s.get("suggested_category"),
                    "suggested_aliases":        json.dumps(
                                                    s.get("suggested_aliases", []),
                                                    ensure_ascii=False
                                                ),
                    "suggested_spec":           s.get("suggested_spec"),
                    "matched_catalog_item_id":  s.get("matched_catalog_item_id"),
                    "similarity_score":         s.get("similarity_score", 0),
                    "similarity_reason":        s.get("similarity_reason", ""),
                    "status":                   "pending",
                })
            return suggestions

        except json.JSONDecodeError as e:
            last_error = f"JSON 파싱 실패: {e}"
            continue
        except Exception as e:
            last_error = f"API 오류: {e}"
            break

    raise RuntimeError(f"카탈로그 제안 실패: {last_error}")


def save_suggestions(conn, suggestions: list):
    """제안 목록을 DB에 저장"""
    if not suggestions:
        return 0

    # 기존 pending 제안 삭제 (재실행 시)
    if suggestions:
        conn.execute(
            "DELETE FROM catalog_suggestions WHERE submission_id = ? AND status = 'pending'",
            (suggestions[0]["submission_id"],)
        )

    for s in suggestions:
        conn.execute("""
            INSERT INTO catalog_suggestions (
                suggestion_id, submission_id, item_id,
                suggestion_type, suggested_name, suggested_category_id,
                suggested_aliases, suggested_spec,
                matched_catalog_item_id, similarity_score, similarity_reason,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (
            s["suggestion_id"], s["submission_id"], s["item_id"],
            s["suggestion_type"], s.get("suggested_name"),
            s.get("suggested_category_id"),
            s.get("suggested_aliases"), s.get("suggested_spec"),
            s.get("matched_catalog_item_id"),
            s.get("similarity_score", 0),
            s.get("similarity_reason", ""),
        ))
    conn.commit()
    return len(suggestions)


def accept_suggestion(conn, suggestion_id: str, user_id: str,
                      override_name: str = None) -> str:
    """
    제안 수락 처리.
    - new_item: catalog_items에 새 품목 생성
    - similar:  submission_items.catalog_item_id 연결

    Returns: 생성/연결된 catalog_item_id
    """
    from datetime import datetime

    row = conn.execute(
        "SELECT * FROM catalog_suggestions WHERE suggestion_id = ?",
        (suggestion_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"제안을 찾을 수 없습니다: {suggestion_id}")

    now = datetime.now().isoformat()
    catalog_item_id = None

    if row["suggestion_type"] == "new_item":
        # 새 품목 생성
        catalog_item_id = str(uuid.uuid4())
        name = override_name or row["suggested_name"] or "미명명 품목"
        conn.execute("""
            INSERT INTO catalog_items (
                catalog_item_id, category_id, name_canonical,
                aliases, spec_template, is_active, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (
            catalog_item_id,
            row["suggested_category_id"],
            name,
            row["suggested_aliases"] or "[]",
            row["suggested_spec"],
            user_id, now, now,
        ))

        # submission_item 연결
        conn.execute("""
            UPDATE submission_items
            SET catalog_item_id = ?, match_status = 'confirmed', match_confidence = 1.0
            WHERE item_id = ?
        """, (catalog_item_id, row["item_id"]))

    elif row["suggestion_type"] == "similar":
        catalog_item_id = row["matched_catalog_item_id"]

        # 현재 name_raw를 기존 품목의 aliases에 추가
        existing = conn.execute(
            "SELECT name_canonical, aliases FROM catalog_items WHERE catalog_item_id = ?",
            (catalog_item_id,)
        ).fetchone()
        if existing:
            try:
                aliases = json.loads(existing["aliases"] or "[]")
            except Exception:
                aliases = []
            # item의 name_raw 가져오기
            item_row = conn.execute(
                "SELECT name_raw FROM submission_items WHERE item_id = ?",
                (row["item_id"],)
            ).fetchone()
            if item_row and item_row["name_raw"] and item_row["name_raw"] not in aliases:
                aliases.append(item_row["name_raw"])
                conn.execute(
                    "UPDATE catalog_items SET aliases = ?, updated_at = ? WHERE catalog_item_id = ?",
                    (json.dumps(aliases, ensure_ascii=False), now, catalog_item_id)
                )

        # submission_item 연결
        conn.execute("""
            UPDATE submission_items
            SET catalog_item_id = ?, match_status = 'confirmed',
                match_confidence = ?
            WHERE item_id = ?
        """, (catalog_item_id, row["similarity_score"], row["item_id"]))

    # 제안 상태 업데이트
    status = "modified" if override_name else "accepted"
    conn.execute("""
        UPDATE catalog_suggestions
        SET status = ?, reviewed_by = ?, reviewed_at = ?
        WHERE suggestion_id = ?
    """, (status, user_id, now, suggestion_id))

    conn.commit()
    return catalog_item_id


def reject_suggestion(conn, suggestion_id: str, user_id: str, note: str = ""):
    """제안 거부 처리"""
    from datetime import datetime
    conn.execute("""
        UPDATE catalog_suggestions
        SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, review_note = ?
        WHERE suggestion_id = ?
    """, (user_id, datetime.now().isoformat(), note, suggestion_id))
    conn.commit()
