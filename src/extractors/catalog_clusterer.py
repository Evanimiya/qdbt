"""
유사 품목 클러스터링 모듈 (Phase 3-B).

입찰별 submission_items를 분석하여 실제로 같은 품목인데 업체마다 다르게 표기된 것들을
LLM이 감지 → 클러스터 제안 → 담당자가 대표 품목명 확정.

흐름:
  1. 특정 입찰의 submission_items를 수집
  2. LLM이 유사 품목 그룹화 + 표준 품목명 제안
  3. catalog_clusters + catalog_cluster_members에 저장
     (catalog_cluster_members.catalog_item_id = submission_items.item_id)
  4. 사용자가 검토 후 확정(accept) 또는 거부(reject)

확정 시 처리:
  - cluster.status = 'accepted', representative_name 저장
  - 향후 Phase 2에서 catalog_item 자동 생성/연결 예정
"""
import json
import uuid
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))


CLUSTER_PROMPT = """당신은 입찰 견적서에서 유사 품목을 그룹화하는 전문 시스템입니다.

## 작업
아래는 여러 업체가 같은 입찰에 제출한 견적서의 품목 목록입니다.
실제로 같은 품목인데 업체마다 다르게 표기된 것들을 찾아 그룹으로 묶고,
각 그룹에 대해 가장 표준적인 품목명을 제안하세요.

## 판단 기준
1. 품목명이 같거나 유사 (영문/한글 표기 차이, 띄어쓰기, 축약어, 모델명 표기 차이 등)
2. 단위가 같거나 유사 (EA, 개, Set, 식 등)
3. 카테고리가 동일
4. 규격(spec)이 동일하거나 호환
5. 유사도 0.75 이상인 경우만 같은 그룹으로 제안

## 규칙
- 확실히 다른 품목은 절대 묶지 않는다 (잘못된 병합이 데이터 오염)
- 같은 업체의 품목끼리도 묶을 수 있음 (중복 견적)
- representative_name은 가장 명확하고 표준적인 한국어 품목명으로 작성
- representative_item_id는 그룹 중 가장 대표적인 item_id를 선택
- 그룹이 없으면 clusters를 빈 배열로 반환

## 출력 형식 (순수 JSON만, 다른 텍스트 없음)
{
  "clusters": [
    {
      "representative_name": "표준 품목명 (예: 2U 랙 서버)",
      "representative_item_id": "그룹 중 가장 대표적인 item_id",
      "duplicate_item_ids": ["나머지 item_id 목록"],
      "similarity_score": 0.0,
      "similarity_summary": "유사 판단 근거 (한 줄)"
    }
  ]
}
"""


def _build_cluster_input(submission_items: list) -> str:
    """LLM 입력 텍스트 구성 (submission_items 기반)"""
    lines = []
    for si in submission_items:
        name = si.get("name_normalized") or si.get("name_raw") or ""
        lines.append(
            f"  - id={si['item_id']}"
            f" | 업체={si.get('vendor_name', '')}"
            f" | 품목명={name}"
            f" | 카테고리={si.get('category', '')}"
            f" | 단위={si.get('unit', '')}"
            f" | 단가={si.get('unit_price', '')}"
            f" | 규격={str(si.get('spec', '') or '')[:80]}"
        )
    return f"## 견적서 품목 목록 ({len(lines)}개)\n" + "\n".join(lines)


def run_clustering(
    submission_items: list,
    api_key: str,
    provider_id: str = "claude",
    model: str = None,
) -> list:
    """
    LLM으로 submission_items에서 유사 품목 클러스터 감지.

    Args:
        submission_items: item_id, name_normalized, name_raw, vendor_name,
                          category, unit, unit_price, spec 포함 dict 리스트
        api_key:          사용자 API 키
        provider_id:      LLM provider
        model:            모델 지정 (None이면 provider 기본값)

    Returns:
        클러스터 목록 [{cluster_id, representative_name, representative_item_id, ...}]
    """
    if not api_key:
        raise ValueError("API 키가 설정되지 않았습니다.")

    if len(submission_items) < 2:
        return []

    from extractors.providers import get_provider
    provider = get_provider(provider_id)
    input_text = _build_cluster_input(submission_items)

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
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines)

            result = json.loads(cleaned)
            raw_clusters = result.get("clusters", [])

            existing_ids = {si["item_id"] for si in submission_items}
            clusters = []
            for rc in raw_clusters:
                rep_id   = rc.get("representative_item_id")
                rep_name = rc.get("representative_name", "").strip()
                dup_ids  = rc.get("duplicate_item_ids", [])
                if not rep_id or rep_id not in existing_ids:
                    continue
                valid_dups = [d for d in dup_ids
                              if d in existing_ids and d != rep_id]
                if not valid_dups:
                    continue
                clusters.append({
                    "cluster_id":             str(uuid.uuid4()),
                    "representative_item_id": rep_id,
                    "representative_name":    rep_name,
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


def save_clusters(conn, clusters: list, bid_id: str = None) -> int:
    """클러스터 제안을 DB에 저장.
    catalog_cluster_members.catalog_item_id = submission_items.item_id
    """
    if not clusters:
        return 0

    for cl in clusters:
        conn.execute("""
            INSERT OR REPLACE INTO catalog_clusters
                (cluster_id, bid_id, representative_item_id, representative_name,
                 status, similarity_summary, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?, datetime('now'))
        """, (cl["cluster_id"], bid_id,
              cl["representative_item_id"],
              cl["representative_name"],
              cl["similarity_summary"]))

        # 대표 품목 (submission item_id 저장)
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
                   representative_name: str = None) -> dict:
    """
    클러스터 확정.

    사용자가 대표 품목명을 최종 확인(또는 수정)하여 확정.
    현재는 status만 변경 (Phase 2에서 catalog_item 자동 생성 예정).
    """
    cluster = conn.execute(
        "SELECT * FROM catalog_clusters WHERE cluster_id = ?",
        (cluster_id,)
    ).fetchone()
    if not cluster:
        raise ValueError(f"클러스터를 찾을 수 없습니다: {cluster_id}")

    now = datetime.now().isoformat()
    final_name = (representative_name or "").strip() or cluster["representative_name"]

    conn.execute("""
        UPDATE catalog_clusters
        SET status = 'accepted',
            representative_name = ?,
            reviewed_by = ?,
            reviewed_at = ?
        WHERE cluster_id = ?
    """, (final_name, user_id, now, cluster_id))
    conn.commit()

    return {
        "representative_name": final_name,
        "merged_count": conn.execute(
            "SELECT COUNT(*) FROM catalog_cluster_members WHERE cluster_id=? AND role='duplicate'",
            (cluster_id,)
        ).fetchone()[0],
        "aliases_added": 0,
    }


def reject_cluster(conn, cluster_id: str, user_id: str):
    """클러스터 제안 거부"""
    conn.execute("""
        UPDATE catalog_clusters
        SET status = 'rejected', reviewed_by = ?, reviewed_at = ?
        WHERE cluster_id = ?
    """, (user_id, datetime.now().isoformat(), cluster_id))
    conn.commit()


def hold_cluster(conn, cluster_id: str, user_id: str):
    """클러스터 보류 (클러스터로 만들고 싶지 않음, 나중에 재검토)"""
    from datetime import datetime
    conn.execute("""
        UPDATE catalog_clusters
        SET status = 'held', reviewed_by = ?, reviewed_at = ?
        WHERE cluster_id = ?
    """, (user_id, datetime.now().isoformat(), cluster_id))
    conn.commit()


def remove_member(conn, cluster_id: str, item_id: str):
    """클러스터에서 특정 아이템 제외 (5-1 요청)"""
    conn.execute("""
        DELETE FROM catalog_cluster_members
        WHERE cluster_id = ? AND catalog_item_id = ?
    """, (cluster_id, item_id))
    conn.commit()


def add_member(conn, cluster_id: str, item_id: str):
    """클러스터에 아이템 수동 추가 (5-2 요청)"""
    conn.execute("""
        INSERT OR IGNORE INTO catalog_cluster_members
            (cluster_id, catalog_item_id, role, similarity_score)
        VALUES (?, ?, 'duplicate', 1.0)
    """, (cluster_id, item_id))
    conn.commit()


def is_high_confidence(conn, cluster_id: str, threshold: float = 0.9) -> bool:
    """모든 멤버의 유사도가 threshold 이상인지 확인 (5-3 확정 버튼 활성화 조건)"""
    rows = conn.execute("""
        SELECT similarity_score FROM catalog_cluster_members
        WHERE cluster_id = ? AND role = 'duplicate'
    """, (cluster_id,)).fetchall()
    if not rows:
        return False
    return all(r["similarity_score"] >= threshold for r in rows)
