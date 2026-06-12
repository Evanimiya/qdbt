"""
유사 품목 클러스터링 모듈 (Phase 3-B).

전체 catalog_items를 분석하여 실제로 같은 품목인데 다르게 등록된 것들을
LLM이 감지 → 클러스터 제안 → 담당자가 대표 품목 선택 후 병합 확정.

병합 시 처리:
  1. 중복 품목의 aliases를 대표 품목에 통합
  2. price_history를 대표 품목으로 재연결
  3. submission_items.catalog_item_id를 대표 품목으로 재연결
  4. 중복 품목은 is_active = 0 (소프트 삭제)
"""
import json
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


CLUSTER_PROMPT = """당신은 표준 품목 카탈로그에서 중복/유사 품목을 감지하는 전문 시스템입니다.

## 작업
아래 카탈로그 품목 목록에서 실제로 같은 품목인데 다르게 등록된 것들을 찾아 그룹으로 묶으세요.

## 판단 기준
1. 품목명이 같거나 유사 (영문/한글 표기 차이, 띄어쓰기, 괄호 등)
2. 단위가 같거나 유사
3. 카테고리가 같은 도메인
4. aliases에 서로의 이름이 포함된 경우
5. 유사도 0.75 이상인 경우만 같은 그룹으로 제안

## 규칙
- 확실히 다른 품목은 절대 묶지 않는다 (잘못된 병합이 데이터 오염)
- 각 그룹에서 가장 표준적인 이름의 품목을 representative로 추천
- 그룹이 없으면 빈 배열 반환

## 출력 형식 (순수 JSON만)
{
  "clusters": [
    {
      "representative_item_id": "대표로 남길 품목 ID",
      "duplicate_item_ids": ["중복 품목 ID1", "중복 품목 ID2"],
      "similarity_score": 0.0~1.0,
      "similarity_summary": "유사 판단 근거 요약 (한 줄)"
    }
  ]
}
"""


def _build_cluster_input(catalog_items: list) -> str:
    """LLM 입력 텍스트 구성"""
    lines = []
    for ci in catalog_items:
        try:
            aliases = json.loads(ci.get("aliases") or "[]")[:3]
        except Exception:
            aliases = []
        lines.append(
            f"  - id={ci['catalog_item_id']}"
            f" | name={ci['name_canonical']}"
            f" | category={ci.get('category_name', '')}"
            f" | domain={ci.get('domain', '')}"
            f" | unit={ci.get('unit_std', '')}"
            f" | aliases={', '.join(aliases)}"
        )
    return f"## 카탈로그 품목 목록 ({len(lines)}개)\n" + "\n".join(lines)


def run_clustering(
    catalog_items: list,
    api_key: str,
    provider_id: str = "claude",
    model: str = None,
    domain: str = None,
) -> list:
    """
    LLM으로 유사 품목 클러스터 감지.

    Args:
        catalog_items:  catalog_items + category_name 포함 dict 리스트
        api_key:        사용자 API 키
        provider_id:    LLM provider
        model:          모델 지정
        domain:         특정 도메인만 분석 (None이면 전체)

    Returns:
        클러스터 목록 [{cluster_id, representative_item_id, members, ...}]
    """
    if not api_key:
        raise ValueError("API 키가 설정되지 않았습니다.")

    # 도메인 필터
    items = catalog_items
    if domain:
        items = [ci for ci in catalog_items
                 if ci.get("domain") == domain or ci.get("category_name") == domain]

    if len(items) < 2:
        return []  # 품목이 2개 미만이면 클러스터링 불필요

    from extractors.providers import get_provider
    provider = get_provider(provider_id)
    input_text = _build_cluster_input(items)

    last_error = None
    for attempt in range(3):
        try:
            response_text = provider.extract(
                parsed_text=input_text,
                system_prompt=CLUSTER_PROMPT,
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
            raw_clusters = result.get("clusters", [])

            # cluster_id 부여 + 검증
            clusters = []
            existing_ids = {ci["catalog_item_id"] for ci in items}
            for rc in raw_clusters:
                rep_id = rc.get("representative_item_id")
                dup_ids = rc.get("duplicate_item_ids", [])
                if not rep_id or rep_id not in existing_ids:
                    continue
                # 유효한 중복 ID만 필터
                valid_dups = [d for d in dup_ids
                              if d in existing_ids and d != rep_id]
                if not valid_dups:
                    continue
                clusters.append({
                    "cluster_id":             str(uuid.uuid4()),
                    "representative_item_id": rep_id,
                    "duplicate_item_ids":     valid_dups,
                    "similarity_score":       rc.get("similarity_score", 0),
                    "similarity_summary":     rc.get("similarity_summary", ""),
                })
            return clusters

        except json.JSONDecodeError as e:
            last_error = f"JSON 파싱 실패: {e}"
            continue
        except Exception as e:
            last_error = f"API 오류: {e}"
            break

    raise RuntimeError(f"클러스터링 실패: {last_error}")


def save_clusters(conn, clusters: list) -> int:
    """클러스터 제안을 DB에 저장"""
    if not clusters:
        return 0

    for cl in clusters:
        conn.execute("""
            INSERT OR REPLACE INTO catalog_clusters
                (cluster_id, representative_item_id,
                 status, similarity_summary, created_at)
            VALUES (?, ?, 'pending', ?, datetime('now'))
        """, (cl["cluster_id"], cl["representative_item_id"],
              cl["similarity_summary"]))

        # 대표 품목
        conn.execute("""
            INSERT OR REPLACE INTO catalog_cluster_members
                (cluster_id, catalog_item_id, role, similarity_score)
            VALUES (?, ?, 'representative', 1.0)
        """, (cl["cluster_id"], cl["representative_item_id"]))

        # 중복 품목들
        for dup_id in cl["duplicate_item_ids"]:
            conn.execute("""
                INSERT OR REPLACE INTO catalog_cluster_members
                    (cluster_id, catalog_item_id, role, similarity_score)
                VALUES (?, ?, 'duplicate', ?)
            """, (cl["cluster_id"], dup_id, cl["similarity_score"]))

    conn.commit()
    return len(clusters)


def accept_cluster(conn, cluster_id: str, user_id: str,
                   representative_id: str = None) -> dict:
    """
    클러스터 병합 확정.

    처리:
      1. 중복 품목의 aliases → 대표 품목에 통합
      2. price_history → 대표 품목으로 재연결
      3. submission_items → 대표 품목으로 재연결
      4. 중복 품목 비활성화 (is_active = 0)
      5. cluster.status = 'accepted'
    """
    from datetime import datetime
    now = datetime.now().isoformat()

    cluster = conn.execute(
        "SELECT * FROM catalog_clusters WHERE cluster_id = ?",
        (cluster_id,)
    ).fetchone()
    if not cluster:
        raise ValueError(f"클러스터를 찾을 수 없습니다: {cluster_id}")

    # representative_id 오버라이드 가능 (담당자가 직접 선택)
    rep_id = representative_id or cluster["representative_item_id"]

    # 모든 멤버 조회
    members = conn.execute(
        "SELECT catalog_item_id FROM catalog_cluster_members WHERE cluster_id = ?",
        (cluster_id,)
    ).fetchall()
    all_ids = [m["catalog_item_id"] for m in members]
    dup_ids = [i for i in all_ids if i != rep_id]

    # 1. 대표 품목의 현재 aliases 조회
    rep_row = conn.execute(
        "SELECT name_canonical, aliases FROM catalog_items WHERE catalog_item_id = ?",
        (rep_id,)
    ).fetchone()
    try:
        rep_aliases = json.loads(rep_row["aliases"] or "[]")
    except Exception:
        rep_aliases = []

    # 2. 중복 품목의 name + aliases 통합
    for dup_id in dup_ids:
        dup_row = conn.execute(
            "SELECT name_canonical, aliases FROM catalog_items WHERE catalog_item_id = ?",
            (dup_id,)
        ).fetchone()
        if not dup_row:
            continue
        # name_canonical을 alias로 추가
        if dup_row["name_canonical"] not in rep_aliases:
            rep_aliases.append(dup_row["name_canonical"])
        # 기존 aliases도 통합
        try:
            dup_aliases = json.loads(dup_row["aliases"] or "[]")
            for a in dup_aliases:
                if a not in rep_aliases and a != rep_row["name_canonical"]:
                    rep_aliases.append(a)
        except Exception:
            pass

    # 3. 대표 품목 aliases 업데이트
    conn.execute("""
        UPDATE catalog_items
        SET aliases = ?, updated_at = ?
        WHERE catalog_item_id = ?
    """, (json.dumps(rep_aliases, ensure_ascii=False), now, rep_id))

    # 4. price_history 재연결
    for dup_id in dup_ids:
        conn.execute("""
            UPDATE price_history
            SET catalog_item_id = ?
            WHERE catalog_item_id = ?
        """, (rep_id, dup_id))

    # 5. submission_items 재연결
    for dup_id in dup_ids:
        conn.execute("""
            UPDATE submission_items
            SET catalog_item_id = ?
            WHERE catalog_item_id = ?
        """, (rep_id, dup_id))

    # 6. 중복 품목 비활성화
    for dup_id in dup_ids:
        conn.execute("""
            UPDATE catalog_items
            SET is_active = 0, updated_at = ?
            WHERE catalog_item_id = ?
        """, (now, dup_id))

    # 7. cluster 상태 업데이트
    conn.execute("""
        UPDATE catalog_clusters
        SET status = 'accepted', reviewed_by = ?, reviewed_at = ?,
            representative_item_id = ?
        WHERE cluster_id = ?
    """, (user_id, now, rep_id, cluster_id))

    conn.commit()
    return {
        "representative_id": rep_id,
        "merged_count":      len(dup_ids),
        "aliases_added":     len(rep_aliases),
    }


def reject_cluster(conn, cluster_id: str, user_id: str):
    """클러스터 제안 거부"""
    from datetime import datetime
    conn.execute("""
        UPDATE catalog_clusters
        SET status = 'rejected', reviewed_by = ?, reviewed_at = ?
        WHERE cluster_id = ?
    """, (user_id, datetime.now().isoformat(), cluster_id))
    conn.commit()
