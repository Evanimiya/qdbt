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


CLUSTER_PROMPT = """당신은 입찰 견적서에서 비교 가능한 품목 그룹을 찾는 전문 시스템입니다.

## 핵심 임무 (가장 중요)
**서로 다른 업체가 제출한 품목 중, 실제로 같은 품목을 나타내는 것들을 묶으세요.**
이름 표기가 달라도 같은 물건이면 반드시 하나의 그룹으로 묶습니다.
적극적으로 묶으세요. 같은 품목인데 이름만 다른 것을 놓치는 것이 가장 큰 실수입니다.

## 당신의 언어 능력을 적극 활용하세요
당신은 영어와 한국어를 모두 이해합니다. 입찰 품목은 업체마다
영어로 적기도, 한국어로 적기도 합니다 — **같은 물건을 다른 언어로 적은 것뿐입니다.**
- "Spine Switch"와 "Spine 스위치"는 같은 품목입니다 (Switch=스위치).
- "GPU Server"와 "GPU 서버"는 같은 품목입니다 (Server=서버).
- "Firewall Appliance"와 "방화벽 장비"는 같은 품목입니다.
당신은 이 번역을 이미 알고 있습니다. 사전을 주지 않아도, 영문 품목을 한국어로
머릿속에서 번역해보고, 다른 업체의 한글 품목과 같은 의미인지 판단하세요.
표기 차이(띄어쓰기, 대소문자, 괄호 수식어, 접미사 "장비/본체")도 무시하고 의미로 묶으세요.

## ★ 누락 방지 — 반드시 지킬 절차
1. 먼저 모든 업체의 모든 품목을 머릿속에 나열하세요.
2. **각 영문 품목**에 대해: 다른 업체에 그것의 한글 번역에 해당하는
   품목이 있는지 반드시 확인하세요. (영문은 흔히 한글 항목과 짝이 됩니다.)
3. **각 한글 품목**에 대해: 다른 업체에 영문/다른 표기 짝이 없는지 확인하세요.
4. 빠뜨린 품목이 없는지 마지막에 다시 한 번 전체를 훑으세요.
같은 품목인데 한 업체는 영문, 다른 업체는 한글로 적어서 따로 떨어지는 일이
없도록 하세요. 이것이 가장 흔한 실수입니다.

## 기존 카탈로그 활용
입력에 "## 기존 카탈로그 품목" 섹션이 있으면:
- representative_name은 **카탈로그의 name_canonical을 그대로** 사용하세요.
- 카탈로그에 없는 새 품목만 새 이름(가장 표준적인 한국어 품목명)을 제안합니다.
- 대표명은 가능하면 한국어로 (영문/한글이 섞이면 한글을 대표명으로).

## 비교 단위(묶음)가 있는 경우에만 적용 (해당 시)
입력에 "비교단위(묶음)=..."로 표시된 항목이 있을 때만 아래를 적용하세요.
없으면 무시하고 위의 핵심 임무(품목명 매칭)에 집중하세요.
- 묶음명이 비교 기준. A의 "기구부"와 B의 "기구부"를 묶음명으로 매칭.
- "[참조] 하위 N개"는 보조 정보. 묶음명이 애매할 때 참조.
- 업체마다 구성이 다를 수 있음(A는 "차폐", B는 "기구부"). 의미가 같으면 묶기.
- 묶음과 단일 항목이 명백히 같으면 묶을 수 있음.


1. **완전히 동일한 품목명** — 두 업체 모두 "Daily Allowance"처럼 같은 이름
2. **영문 ↔ 한글 번역** — "Firewall" ↔ "방화벽", "Server" ↔ "서버"
3. **괄호 수식어 차이** — "방화벽"과 "방화벽 (NGFW)"은 같은 품목
4. **접미사 차이** — "방화벽", "방화벽 장비", "방화벽 본체"는 같은 품목
5. **약어/전체 표기 차이** — "NW HW 보증연장"과 "네트워크 HW 보증연장"
6. **띄어쓰기/맞춤법 차이** — "L2원격지원"과 "L2 원격 지원"

## 제외 규칙
- 명백히 다른 품목은 묶지 않는다 (예: "방화벽 본체"와 "방화벽 라이선스"는 다른 품목)
- 같은 업체의 품목끼리만 있는 그룹은 만들지 않는다 (최소 2개 업체 필요)
- all_item_ids에는 그룹의 모든 item_id 포함 (2개 이상)

## 판단 예시
- "GPU Server" + "GPU 서버" + "GPU서버" = 같은 그룹 ✅ (영문/한글/띄어쓰기)
- "Firewall Appliance" + "방화벽 본체" = 같은 그룹 ✅ (영문/한글)
- "방화벽" + "방화벽 (NGFW)" + "방화벽 장비" = 같은 그룹 ✅
- "방화벽 본체" + "방화벽 라이선스/구독" = 다른 그룹 ❌
- "Operating Margin" + "영업이익" = 같은 그룹 ✅
- "서버 HW 보증연장" + "서버 유지보수" = 다를 수 있음, 맥락 판단

## 출력 형식 (순수 JSON만, 설명 텍스트 없음)
{
  "clusters": [
    {
      "representative_name": "카탈로그에 있으면 카탈로그 이름, 없으면 가장 표준적인 한국어 품목명",
      "all_item_ids": ["모든 item_id — 대표 포함, 2개 이상"],
      "similarity_score": 0.0~1.0,
      "similarity_summary": "유사 근거 한 줄"
    }
  ]
}
"""


def _pick_rep_name(names: list) -> str:
    """클러스터 대표명 선택 — 한글 이름 우선, 없으면 첫 번째.

    한/영이 섞인 경우 한글이 더 표준적이므로 한글명을 대표로.
    """
    import re as _re
    # 한글이 포함된 이름 우선
    korean = [n for n in names if _re.search(r"[가-힣]", n or "")]
    if korean:
        # 한글 이름 중 가장 짧은 것 (간결)
        return min(korean, key=len)
    return names[0] if names else "미명명"


def _build_cluster_input(submission_items: list, catalog_items: list = None) -> str:
    """LLM 입력 텍스트 구성 — 카탈로그 컨텍스트 + 업체별 품목"""
    from collections import defaultdict
    by_vendor = defaultdict(list)
    for si in submission_items:
        by_vendor[si.get("vendor_name", "미상")].append(si)

    lines = []

    # 기존 카탈로그 품목 섹션 (있을 때만)
    if catalog_items:
        lines.append("## 기존 카탈로그 품목 (representative_name 작성 시 이 이름을 우선 사용)")
        for ci in catalog_items:
            raw_aliases = ci.get("aliases") or "[]"
            if isinstance(raw_aliases, str):
                try:
                    alias_list = json.loads(raw_aliases)
                except Exception:
                    alias_list = []
            else:
                alias_list = list(raw_aliases)
            alias_str = ", ".join(alias_list) if alias_list else ""
            entry = f"  - {ci['name_canonical']}"
            if alias_str:
                entry += f" (별칭: {alias_str})"
            lines.append(entry)
        lines.append("")

    lines.append(f"## 신규 입찰 품목 ({len(submission_items)}개 / {len(by_vendor)}개 업체)\n")

    for vendor, items in by_vendor.items():
        lines.append(f"### {vendor}")
        for si in items:
            name = si.get("name_normalized") or si.get("name_raw") or ""
            if si.get("is_group"):
                # 비교 단위가 분류 묶음 → 묶음명이 비교 기준(주),
                # 하위 세부는 참조(보조)임을 LLM에 명시
                ref = si.get("ref_members") or []
                ref_str = ", ".join(ref[:12])
                lines.append(
                    f"  - id={si['item_id']}"
                    f" | 비교단위(묶음)={name}"
                    f" | 카테고리={si.get('category', '')}"
                    f" | 묶음합계금액={si.get('amount', '')}"
                    f" | [참조] 하위 {si.get('n_items','')}개: {ref_str[:200]}"
                )
            else:
                lines.append(
                    f"  - id={si['item_id']}"
                    f" | 품목명={name}"
                    f" | 카테고리={si.get('category', '')}"
                    f" | 단위={si.get('unit', '')}"
                    f" | 단가={si.get('unit_price', '')}"
                    f" | 규격={str(si.get('spec', '') or '')[:60]}"
                )
        lines.append("")

    # ── 전체 품목 통합 목록 (영한 짝을 한눈에 — 누락 방지) ──
    # 업체별로 흩어진 같은 품목(영문/한글)을 LLM이 가로질러 찾기 쉽도록,
    # 모든 품목을 이름 가나다/알파벳 순으로 한 번 더 나열한다.
    all_names = []
    for vendor, items in by_vendor.items():
        for si in items:
            nm = si.get("name_normalized") or si.get("name_raw") or ""
            if nm:
                all_names.append((nm, vendor, si["item_id"]))
    all_names.sort(key=lambda x: x[0].lower())
    lines.append("## 전체 품목 (이름순 — 영문/한글 짝을 빠짐없이 확인하세요)")
    lines.append("아래는 모든 업체의 품목을 이름순으로 모은 것입니다.")
    lines.append("영문 품목 바로 옆/근처에 같은 의미의 한글 품목이 있는지 반드시 확인하세요.")
    for nm, vendor, iid in all_names:
        lines.append(f"  - {nm}  [{vendor}] (id={iid})")
    lines.append("")

    return "\n".join(lines)


# 단일 LLM 호출로 클러스터링할 최대 품목 수.
# 이 값을 초과하면 청크 + carry-forward(미매칭 이월) 증분 클러스터링으로 전환.
CLUSTER_CHUNK_SIZE = 90


def run_clustering(
    submission_items: list,
    api_key: str,
    provider_id: str = "claude",
    model: str = None,
    base_url: str = None,
    verify_ssl: bool = True,
    catalog_items: list = None,
) -> list:
    """
    LLM으로 submission_items에서 유사 품목 클러스터 감지.

    catalog_items: 기존 카탈로그 품목 목록 (name_canonical + aliases).
                   제공 시 LLM이 카탈로그 이름을 representative_name으로 우선 채택.

    품목 수가 CLUSTER_CHUNK_SIZE를 초과하면 자동으로 청크 분할 후
    carry-forward 증분 클러스터링을 수행한다(견적서 추출의 청크 처리와 동일한 발상이나,
    클러스터링은 단순 append가 아니라 청크 간 유사 품목을 놓치지 않도록 미매칭 항목을
    다음 청크로 이월하며 누적 클러스터에 편입시키는 방식).

    반환: [{cluster_id, representative_name, representative_item_id,
             duplicate_item_ids, similarity_score, similarity_summary}]
    """
    if not api_key:
        raise ValueError("API 키가 설정되지 않았습니다.")
    if len(submission_items) < 2:
        return []

    if len(submission_items) <= CLUSTER_CHUNK_SIZE:
        return _cluster_single(submission_items, api_key, provider_id, model, base_url, verify_ssl, catalog_items)
    return _cluster_chunked(submission_items, api_key, provider_id, model, base_url, verify_ssl, catalog_items)


def _cluster_single(
    submission_items: list,
    api_key: str,
    provider_id: str = "claude",
    model: str = None,
    base_url: str = None,
    verify_ssl: bool = True,
    catalog_items: list = None,
) -> list:
    """단일 LLM 호출로 클러스터 감지(소규모 또는 청크 단위 처리용)."""
    if len(submission_items) < 2:
        return []

    from extractors.providers import get_provider
    provider  = get_provider(provider_id)
    input_text = _build_cluster_input(submission_items, catalog_items)

    # 디버그: LLM 입력을 파일로 남김 (클러스터링 진단용)
    import os as _os
    _dbg = _os.environ.get("QDBT_CLUSTER_DEBUG")
    if _dbg:
        try:
            with open(_dbg, "a", encoding="utf-8") as _f:
                _f.write("\n\n===== CLUSTER INPUT =====\n")
                _f.write(input_text)
        except Exception:
            pass

    last_error = None
    for attempt in range(3):
        try:
            response_text = provider.extract(
                parsed_text=input_text,
                system_prompt=CLUSTER_PROMPT,
                api_key=api_key,
                model=model,
                base_url=base_url, verify_ssl=verify_ssl,            )
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines)

            result     = json.loads(cleaned)
            raw_clusters = result.get("clusters", [])

            # 디버그: LLM 응답을 파일로 남김
            if _dbg:
                try:
                    with open(_dbg, "a", encoding="utf-8") as _f:
                        _f.write("\n\n===== CLUSTER OUTPUT =====\n")
                        _f.write(response_text)
                        _f.write(f"\n\n(파싱된 클러스터 수: {len(raw_clusters)})\n")
                except Exception:
                    pass

            existing_ids = {si["item_id"]: si for si in submission_items}
            clusters = []

            for rc in raw_clusters:
                rep_name    = (rc.get("representative_name") or "").strip()
                all_ids     = rc.get("all_item_ids", [])

                # all_item_ids 검증 — 존재하는 id만, 최소 2개 업체
                valid_ids = [i for i in all_ids if i in existing_ids]
                if len(valid_ids) < 2:
                    continue

                # 최소 2개 업체가 포함되어야 함
                vendors_in_group = {existing_ids[i]["vendor_name"] for i in valid_ids}
                if len(vendors_in_group) < 2:
                    continue

                # 대표 품목: 첫 번째 id (없으면 첫 번째 valid_id)
                rep_id   = valid_ids[0]
                dup_ids  = valid_ids[1:]

                if not rep_name:
                    # 대표명이 없으면 대표 품목의 name_normalized 사용
                    si = existing_ids[rep_id]
                    rep_name = (si.get("name_normalized") or si.get("name_raw") or "미명명")

                # 대표명이 영문뿐이면, 멤버 중 한글 이름을 우선 (한글이 더 표준)
                import re as _re_k
                if not _re_k.search(r"[가-힣]", rep_name):
                    member_names = [
                        existing_ids[i].get("name_normalized") or existing_ids[i].get("name_raw") or ""
                        for i in valid_ids
                    ]
                    rep_name = _pick_rep_name(member_names + [rep_name])

                clusters.append({
                    "cluster_id":             str(uuid.uuid4()),
                    "representative_item_id": rep_id,
                    "representative_name":    rep_name,
                    "duplicate_item_ids":     dup_ids,
                    "similarity_score":       float(rc.get("similarity_score", 0.8)),
                    "similarity_summary":     rc.get("similarity_summary", ""),
                })

            return clusters
            return clusters
            last_error = f"JSON 파싱 실패: {e}"
            continue
        except Exception as e:
            last_error = f"API 오류: {e}"
            break

    raise RuntimeError(f"클러스터링 실패: {last_error}")


def _cluster_chunked(
    submission_items: list,
    api_key: str,
    provider_id: str = "claude",
    model: str = None,
    base_url: str = None,
    verify_ssl: bool = True,
    catalog_items: list = None,
) -> list:
    """
    대규모 품목을 청크 + carry-forward 증분 클러스터링.

    각 청크마다:
      A) 이전 청크에서 누적된 클러스터에 현재 풀(이월 미매칭 + 신규 청크)을 편입 시도.
      B) 남은 항목들끼리 새 클러스터 형성.
      C) 어디에도 속하지 못한 항목은 다음 청크로 이월(carry-forward).
    → 청크 경계를 넘어선 유사 품목도 이월을 통해 같은 풀에서 만나 매칭된다.
    """
    # 청킹 전 정규화 이름순으로 정렬(blocking): 유사·동일 품목이 인접 → 같은
    # 청크/윈도우에 모여 단일 호출 내에서 매칭될 확률을 높인다. 청크 경계를
    # 넘어가는 그룹은 carry-forward로 보완.
    submission_items = sorted(
        submission_items,
        key=lambda si: (si.get("name_normalized") or si.get("name_raw") or "").lower(),
    )
    id_map = {si["item_id"]: si for si in submission_items}

    def _member_names(cluster: dict) -> list:
        ids = [cluster["representative_item_id"]] + cluster.get("duplicate_item_ids", [])
        names = []
        for iid in ids:
            si = id_map.get(iid)
            if si:
                names.append(si.get("name_normalized") or si.get("name_raw") or "")
        return names

    chunks = [
        submission_items[i:i + CLUSTER_CHUNK_SIZE]
        for i in range(0, len(submission_items), CLUSTER_CHUNK_SIZE)
    ]

    all_clusters: list = []
    carry: list = []  # 아직 어떤 클러스터에도 속하지 못한 항목 dict 목록

    for chunk in chunks:
        # 이월분 + 신규 청크를 합치되, 모든 LLM 호출 입력이 CLUSTER_CHUNK_SIZE를
        # 넘지 않도록 윈도우(<= SIZE) 단위로 나눠 처리한다.
        combined = carry + chunk
        carry = []  # 이번 청크 처리 중 미매칭분으로 다시 채워짐(다음 청크로 이월)

        for w in range(0, len(combined), CLUSTER_CHUNK_SIZE):
            pool = combined[w:w + CLUSTER_CHUNK_SIZE]
            assigned_ids: set = set()

            # ── A) 누적 클러스터에 풀 항목 편입 시도 (입력 <= SIZE) ──
            if all_clusters:
                existing_for_llm = [
                    {
                        "cluster_id":          c["cluster_id"],
                        "representative_name":  c["representative_name"],
                        "member_names":         _member_names(c),
                    }
                    for c in all_clusters
                ]
                try:
                    additions = run_unmatched_verification(
                        pool, existing_for_llm, api_key, provider_id, model,
                        base_url, verify_ssl
                    )
                except Exception:
                    additions = []

                cluster_by_id = {c["cluster_id"]: c for c in all_clusters}
                for a in additions:
                    c = cluster_by_id.get(a.get("cluster_id"))
                    iid = a.get("item_id")
                    if not c or not iid:
                        continue
                    if iid == c["representative_item_id"] or iid in c["duplicate_item_ids"]:
                        continue
                    c["duplicate_item_ids"].append(iid)
                    assigned_ids.add(iid)

            # ── B) 남은 항목끼리 새 클러스터 형성 (입력 <= SIZE) ──
            remaining = [si for si in pool if si["item_id"] not in assigned_ids]
            newly_clustered: set = set()
            if len(remaining) >= 2:
                new_clusters = _cluster_single(remaining, api_key, provider_id, model, base_url, verify_ssl, catalog_items)
                all_clusters.extend(new_clusters)
                for c in new_clusters:
                    newly_clustered.add(c["representative_item_id"])
                    newly_clustered.update(c["duplicate_item_ids"])

            # ── C) 미매칭 항목 이월 ──
            carry.extend(
                si for si in remaining
                if si["item_id"] not in newly_clustered
            )

    return all_clusters


# ─────────────────────────────────────────────────────────────────
# PHASE 2 — 미분류 항목 재검증
# ─────────────────────────────────────────────────────────────────

UNMATCHED_PROMPT = """당신은 입찰 견적서 품목 분류 전문 시스템입니다.

## 작업
이미 식별된 클러스터(유사 품목 그룹) 목록과, 아직 어느 클러스터에도 포함되지 않은 품목 목록이 주어집니다.
미분류 품목이 기존 클러스터에 속하는지 찾으세요.
이 단계는 1차에서 놓친 품목을 회수하는 단계입니다. 특히 한/영 표기 차이로
따로 떨어진 품목을 적극적으로 찾아 편입하세요.

## 당신의 언어 능력을 활용하세요
미분류 품목이 영문이면, 머릿속에서 한국어로 번역해보고 클러스터 대표명과
같은 의미인지 판단하세요. 당신은 영한 번역을 이미 압니다.
- 미분류 "Spine Switch" → 클러스터 "Spine 스위치"에 편입 (Switch=스위치)
- 미분류 "GPU Server" → 클러스터 "GPU 서버"에 편입
- 미분류 "Firewall Appliance" → 클러스터 "방화벽 장비"에 편입

## 판단 기준 (적극 편입)
- 영문↔한글 번역으로 같은 의미 → 편입 (확신을 가지세요, 당신은 번역을 압니다)
- 띄어쓰기/대소문자/괄호 수식어/접미사 차이 → 편입
- 명백히 다른 품목만 제외 (예: "방화벽 본체" vs "방화벽 라이선스")

## 제약
- 한 품목을 여러 클러스터에 할당하지 마세요 (가장 맞는 하나만)
- 의미가 명백히 다르면 편입하지 마세요

## 출력 형식 (순수 JSON만, 설명 없음)
{
  "additions": [
    {
      "item_id": "추가할 품목의 item_id",
      "cluster_id": "기존 클러스터 id",
      "confidence": 0.0~1.0,
      "reason": "추가 근거 한 줄"
    }
  ]
}
추가할 항목이 없으면: {"additions": []}
"""


def _build_unmatched_input(unmatched_items: list, existing_clusters: list) -> str:
    """Phase 2 LLM 입력 텍스트 구성"""
    from collections import defaultdict
    lines = []

    lines.append(f"## 기존 클러스터 ({len(existing_clusters)}개)")
    for cl in existing_clusters:
        members_text = " | ".join(cl.get("member_names", [])[:8])
        lines.append(
            f"  - cluster_id={cl['cluster_id']}"
            f" | 대표명={cl['representative_name']}"
            f" | 멤버: {members_text}"
        )

    lines.append("")
    lines.append(f"## 미분류 품목 ({len(unmatched_items)}개)")
    by_vendor = defaultdict(list)
    for si in unmatched_items:
        by_vendor[si.get("vendor_name", "미상")].append(si)
    for vendor, items in by_vendor.items():
        lines.append(f"### {vendor}")
        for si in items:
            name = si.get("name_normalized") or si.get("name_raw") or ""
            lines.append(
                f"  - id={si['item_id']}"
                f" | 품목명={name}"
                f" | 카테고리={si.get('category', '')}"
            )
        lines.append("")

    return "\n".join(lines)


def run_unmatched_verification(
    unmatched_items: list,
    existing_clusters: list,
    api_key: str,
    provider_id: str = "claude",
    model: str = None,
    base_url: str = None,
    verify_ssl: bool = True,
) -> list:
    """
    Phase 2: 미분류 품목이 기존 클러스터에 속하는지 LLM으로 검증.

    Returns:
        [{item_id, cluster_id, confidence, reason}]
    """
    if not unmatched_items or not existing_clusters:
        return []

    from extractors.providers import get_provider
    provider  = get_provider(provider_id)
    input_text = _build_unmatched_input(unmatched_items, existing_clusters)

    valid_item_ids    = {si["item_id"] for si in unmatched_items}
    valid_cluster_ids = {cl["cluster_id"] for cl in existing_clusters}

    last_error = None
    for attempt in range(3):
        try:
            response_text = provider.extract(
                parsed_text=input_text,
                system_prompt=UNMATCHED_PROMPT,
                api_key=api_key,
                model=model,
                base_url=base_url, verify_ssl=verify_ssl,            )
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines)

            result   = json.loads(cleaned)
            additions = result.get("additions", [])

            # 유효성 검증: 실제 존재하는 id만, 중복 item 제거
            valid = []
            seen_items = set()
            for a in additions:
                iid = a.get("item_id", "")
                cid = a.get("cluster_id", "")
                if iid in valid_item_ids and cid in valid_cluster_ids and iid not in seen_items:
                    valid.append(a)
                    seen_items.add(iid)
            return valid

        except json.JSONDecodeError as e:
            last_error = f"JSON 파싱 실패: {e}"
            continue
        except Exception as e:
            last_error = f"API 오류: {e}"
            break

    raise RuntimeError(f"미분류 검증 실패: {last_error}")


def apply_verification_results(conn, additions: list) -> int:
    """Phase 2 결과를 DB에 반영 — 미분류 품목을 기존 클러스터에 추가."""
    if not additions:
        return 0
    n = 0
    for a in additions:
        item_id    = a["item_id"]
        cluster_id = a["cluster_id"]
        confidence = float(a.get("confidence", 0.7))
        # 이미 다른 클러스터에 속해있으면 건너뜀
        if conn.execute(
            "SELECT 1 FROM catalog_cluster_members WHERE catalog_item_id = ?", (item_id,)
        ).fetchone():
            continue
        conn.execute("""
            INSERT OR IGNORE INTO catalog_cluster_members
                (cluster_id, catalog_item_id, role, similarity_score)
            VALUES (?, ?, 'duplicate', ?)
        """, (cluster_id, item_id, confidence))
        n += 1
    conn.commit()
    return n


# ─────────────────────────────────────────────────────────────────
# PHASE 3 — 클러스터 적정성 재검토
# ─────────────────────────────────────────────────────────────────

VALIDATION_PROMPT = """당신은 입찰 견적서 품목 분류 품질 검토 전문 시스템입니다.

## 작업
각 클러스터 구성원들이 실제로 같은 품목을 나타내는지 검토하세요.

## 제거 기준 (이 경우에만 제거 제안)
1. 클러스터 대표명과 명백히 다른 품목 (예: "방화벽" 클러스터에 "방화벽 라이선스/구독")
2. 부속품/소모품이 본체 클러스터에 잘못 포함된 경우

## 보존 기준 (제거하지 마세요)
- 영문↔한글 번역 관계 (GPU Server ↔ GPU 서버)
- 약어/전체 표기 차이
- 괄호 수식어 차이 (방화벽 ↔ 방화벽(NGFW))

## 출력 형식 (순수 JSON만)
{
  "validations": [
    {
      "cluster_id": "클러스터 id",
      "is_valid": true/false,
      "items_to_remove": [
        {"item_id": "제거할 item_id", "reason": "제거 근거 한 줄"}
      ],
      "note": "검토 의견 한 줄 (선택)"
    }
  ]
}
제거할 항목이 없는 클러스터는 생략하세요.
모든 클러스터가 정상이면: {"validations": []}
"""


def _build_validation_input(clusters_with_members: list) -> str:
    """Phase 3 LLM 입력 텍스트 구성"""
    lines = [f"## 검토 대상 클러스터 ({len(clusters_with_members)}개)"]
    for cl in clusters_with_members:
        lines.append(
            f"\n### cluster_id={cl['cluster_id']} | 대표명={cl['representative_name']}"
        )
        for m in cl.get("members", []):
            name = m.get("name_normalized") or m.get("name_raw") or ""
            lines.append(
                f"  - item_id={m['item_id']}"
                f" | 품목명={name}"
                f" | 업체={m.get('vendor_name', '')}"
            )
    return "\n".join(lines)


def run_cluster_validation(
    clusters_with_members: list,
    api_key: str,
    provider_id: str = "claude",
    model: str = None,
    base_url: str = None,
    verify_ssl: bool = True,
) -> list:
    """
    Phase 3: 각 클러스터 구성원 적정성 검토.

    Returns:
        [{cluster_id, is_valid, items_to_remove: [{item_id, reason}], note}]
    """
    if not clusters_with_members:
        return []

    from extractors.providers import get_provider
    provider  = get_provider(provider_id)
    input_text = _build_validation_input(clusters_with_members)

    valid_cluster_ids = {cl["cluster_id"] for cl in clusters_with_members}
    valid_item_ids    = {
        m["item_id"]
        for cl in clusters_with_members
        for m in cl.get("members", [])
    }

    last_error = None
    for attempt in range(3):
        try:
            response_text = provider.extract(
                parsed_text=input_text,
                system_prompt=VALIDATION_PROMPT,
                api_key=api_key,
                model=model,
                base_url=base_url, verify_ssl=verify_ssl,            )
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines)

            result      = json.loads(cleaned)
            validations = result.get("validations", [])

            valid_results = []
            for v in validations:
                cid = v.get("cluster_id", "")
                if cid not in valid_cluster_ids:
                    continue
                items_to_remove = [
                    r for r in v.get("items_to_remove", [])
                    if r.get("item_id", "") in valid_item_ids
                ]
                valid_results.append({
                    "cluster_id":     cid,
                    "is_valid":       v.get("is_valid", True),
                    "items_to_remove": items_to_remove,
                    "note":           v.get("note", ""),
                })
            return valid_results

        except json.JSONDecodeError as e:
            last_error = f"JSON 파싱 실패: {e}"
            continue
        except Exception as e:
            last_error = f"API 오류: {e}"
            break

    raise RuntimeError(f"클러스터 검증 실패: {last_error}")


def apply_validation_results(conn, validations: list) -> dict:
    """Phase 3 결과를 DB에 반영 — 부적절한 멤버 제거, 멤버 1개 이하 클러스터 해소."""
    n_removed = 0
    affected  = set()

    for v in validations:
        for item in v.get("items_to_remove", []):
            conn.execute("""
                DELETE FROM catalog_cluster_members
                WHERE cluster_id = ? AND catalog_item_id = ?
            """, (v["cluster_id"], item["item_id"]))
            n_removed += 1
            affected.add(v["cluster_id"])

    # 멤버가 1명 이하 남은 클러스터 해소
    dissolved = 0
    for cluster_id in affected:
        count = conn.execute(
            "SELECT COUNT(*) FROM catalog_cluster_members WHERE cluster_id = ?",
            (cluster_id,)
        ).fetchone()[0]
        if count < 2:
            conn.execute(
                "DELETE FROM catalog_cluster_members WHERE cluster_id = ?", (cluster_id,)
            )
            conn.execute(
                "DELETE FROM catalog_clusters WHERE cluster_id = ?", (cluster_id,)
            )
            dissolved += 1

    conn.commit()
    return {"removed": n_removed, "dissolved": dissolved}


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
    클러스터 확정 + 카탈로그 자동 생성.

    처리 순서:
    ① catalog_clusters.status = 'accepted'
    ② catalog_items 생성 (표준 품목)
       - name_canonical = representative_name
       - aliases = 멤버 품목들의 name_raw 자동 수집
       - category_id = 멤버 중 가장 많은 카테고리
    ③ submission_items.catalog_item_id 연결
    ④ price_history 자동 생성 (업체별 단가 이력)
    """
    cluster = conn.execute(
        "SELECT * FROM catalog_clusters WHERE cluster_id = ?",
        (cluster_id,)
    ).fetchone()
    if not cluster:
        raise ValueError(f"클러스터를 찾을 수 없습니다: {cluster_id}")

    now = datetime.now().isoformat()
    final_name = (representative_name or "").strip() or cluster["representative_name"] or "미명명 품목"

    # ── ① 클러스터 상태 업데이트 ──────────────────
    conn.execute("""
        UPDATE catalog_clusters
        SET status = 'accepted', representative_name = ?,
            reviewed_by = ?, reviewed_at = ?
        WHERE cluster_id = ?
    """, (final_name, user_id, now, cluster_id))

    # ── ② 멤버 품목 조회 ──────────────────────────
    members = conn.execute("""
        SELECT cm.catalog_item_id as item_id, cm.role,
               si.name_raw, si.name_normalized, si.category,
               si.unit_price, si.quantity, si.amount,
               s.vendor_name, s.submission_id,
               b.bid_id, b.due_date
        FROM catalog_cluster_members cm
        JOIN submission_items si ON cm.catalog_item_id = si.item_id
        JOIN submissions s ON si.submission_id = s.submission_id
        JOIN bids b ON s.bid_id = b.bid_id
        WHERE cm.cluster_id = ?
    """, (cluster_id,)).fetchall()

    if not members:
        conn.commit()
        return {"representative_name": final_name, "catalog_item_id": None,
                "aliases_added": 0, "price_history_count": 0}

    # ── ③ 가장 많은 카테고리 찾기 ─────────────────
    from collections import Counter
    cat_counter = Counter(m["category"] for m in members if m["category"])
    most_common_cat = cat_counter.most_common(1)[0][0] if cat_counter else None

    # 카테고리 ID 조회
    category_id = None
    if most_common_cat:
        cat_row = conn.execute(
            "SELECT category_id FROM catalog_categories WHERE name = ? AND is_active = 1 LIMIT 1",
            (most_common_cat,)
        ).fetchone()
        if cat_row:
            category_id = cat_row["category_id"]

    # ── ④ aliases 수집 (중복 제거) ────────────────
    new_aliases_set = {
        (m["name_normalized"] or m["name_raw"] or "").strip()
        for m in members
        if (m["name_normalized"] or m["name_raw"] or "").strip()
        and (m["name_normalized"] or m["name_raw"] or "").strip() != final_name
    }

    # ── ⑤ catalog_items: 동일 이름 기존 항목 재사용 / 없으면 신규 생성 ──
    # 1) name_canonical 정확 매칭 (대소문자 무시)
    existing_ci = conn.execute("""
        SELECT catalog_item_id, name_canonical, aliases
        FROM catalog_items
        WHERE LOWER(name_canonical) = LOWER(?) AND is_active = 1
        LIMIT 1
    """, (final_name,)).fetchone()

    # 2) aliases 역방향 매칭 (final_name이 기존 catalog_item의 별칭인 경우)
    if not existing_ci:
        for ci_row in conn.execute(
            "SELECT catalog_item_id, name_canonical, aliases FROM catalog_items WHERE is_active=1"
        ).fetchall():
            try:
                al = json.loads(ci_row["aliases"] or "[]")
            except Exception:
                al = []
            if any(a.lower() == final_name.lower() for a in al):
                existing_ci = ci_row
                break

    if existing_ci:
        # 기존 catalog_item 재사용 — aliases만 병합
        catalog_item_id = existing_ci["catalog_item_id"]
        try:
            prev_aliases = json.loads(existing_ci["aliases"] or "[]")
        except Exception:
            prev_aliases = []
        merged_aliases = list(set(prev_aliases) | new_aliases_set)
        # final_name이 기존 name_canonical과 다르면 별칭으로도 추가
        if existing_ci["name_canonical"].lower() != final_name.lower():
            merged_aliases = list(set(merged_aliases) | {final_name})
        conn.execute("""
            UPDATE catalog_items SET aliases=?, updated_at=? WHERE catalog_item_id=?
        """, (json.dumps(merged_aliases, ensure_ascii=False), now, catalog_item_id))
    else:
        # 신규 catalog_item 생성
        catalog_item_id = str(uuid.uuid4())
        aliases = list(new_aliases_set)
        conn.execute("""
            INSERT INTO catalog_items
                (catalog_item_id, category_id, name_canonical,
                 aliases, is_active, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
        """, (catalog_item_id, category_id, final_name,
              json.dumps(aliases, ensure_ascii=False),
              user_id, now, now))

    # ── ⑥ submission_items.catalog_item_id 연결 ──
    for m in members:
        conn.execute("""
            UPDATE submission_items
            SET catalog_item_id = ?,
                match_status = 'confirmed',
                match_confidence = 1.0
            WHERE item_id = ?
        """, (catalog_item_id, m["item_id"]))

    # ── ⑦ price_history 자동 생성 ────────────────
    ph_count = 0
    for m in members:
        if not m["unit_price"]:
            continue
        bid_date = (m["due_date"] or now)[:10]
        # 중복 방지: 같은 item_id는 한 번만
        existing = conn.execute("""
            SELECT record_id FROM price_history
            WHERE catalog_item_id = ? AND item_id = ?
        """, (catalog_item_id, m["item_id"])).fetchone()
        if existing:
            continue
        conn.execute("""
            INSERT INTO price_history
                (record_id, catalog_item_id, submission_id, item_id,
                 vendor_name, unit_price, quantity, amount, bid_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), catalog_item_id, m["submission_id"],
              m["item_id"], m["vendor_name"], m["unit_price"], m["quantity"],
              m["amount"], bid_date))
        ph_count += 1

    # ── ⑧ 클러스터에 catalog_item_id 저장 ────────
    conn.execute("""
        UPDATE catalog_clusters
        SET representative_item_id = ?
        WHERE cluster_id = ?
    """, (catalog_item_id, cluster_id))

    conn.commit()
    if existing_ci:
        aliases_count = len(merged_aliases)
    else:
        aliases_count = len(aliases)
    return {
        "representative_name":  final_name,
        "catalog_item_id":      catalog_item_id,
        "aliases_added":        aliases_count,
        "price_history_count":  ph_count,
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


def create_cluster_from_items(conn, bid_id: str, item_ids: list,
                               representative_name: str, user_id: str,
                               category: str = None) -> str:
    """
    선택한 아이템들로 새 클러스터 생성.
    category: 유저가 지정한 카테고리. None이면 멤버 다수결로 자동 결정.
    """
    import uuid
    from datetime import datetime
    from collections import Counter

    # 카테고리 자동 결정 (유저 지정 없을 때)
    if not category:
        rows = conn.execute(
            f"SELECT category FROM submission_items WHERE item_id IN ({','.join('?'*len(item_ids))})",
            item_ids
        ).fetchall()
        cat_cnt = Counter(r["category"] for r in rows if r["category"])
        category = cat_cnt.most_common(1)[0][0] if cat_cnt else "기타"

    cluster_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO catalog_clusters
            (cluster_id, bid_id, representative_item_id, representative_name,
             status, similarity_summary, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?, datetime('now'))
    """, (cluster_id, bid_id, item_ids[0], representative_name,
          f'수동 생성 ({category})'))

    for i, item_id in enumerate(item_ids):
        # 기존 클러스터에서 해당 아이템 제거 (move_items_to_cluster와 동일)
        conn.execute("""
            DELETE FROM catalog_cluster_members
            WHERE catalog_item_id = ? AND cluster_id != ?
        """, (item_id, cluster_id))
        role = 'representative' if i == 0 else 'duplicate'
        conn.execute("""
            INSERT OR IGNORE INTO catalog_cluster_members
                (cluster_id, catalog_item_id, role, similarity_score)
            VALUES (?, ?, ?, 1.0)
        """, (cluster_id, item_id, role))

    conn.commit()
    return cluster_id


def move_items_to_cluster(conn, cluster_id: str, item_ids: list) -> int:
    """
    선택한 아이템들을 기존 클러스터에 추가.
    이미 다른 클러스터에 속한 아이템은 기존 클러스터에서 제거 후 이동.
    """
    moved = 0
    for item_id in item_ids:
        conn.execute("""
            DELETE FROM catalog_cluster_members
            WHERE catalog_item_id = ? AND cluster_id != ?
        """, (item_id, cluster_id))
        conn.execute("""
            INSERT OR IGNORE INTO catalog_cluster_members
                (cluster_id, catalog_item_id, role, similarity_score)
            VALUES (?, ?, 'duplicate', 1.0)
        """, (cluster_id, item_id))
        moved += 1
    conn.commit()
    return moved


def rename_cluster(conn, cluster_id: str, new_name: str, user_id: str):
    """클러스터 대표 품목명 수정 (상태 무관)"""
    conn.execute("""
        UPDATE catalog_clusters
        SET representative_name = ?, reviewed_by = ?
        WHERE cluster_id = ?
    """, (new_name.strip(), user_id, cluster_id))
    conn.commit()


def merge_clusters(conn, target_id: str, src_ids: list, user_id: str) -> dict:
    """
    src_ids 클러스터들을 target 클러스터로 병합.
    - 멤버 이전 후 src 클러스터는 rejected 처리.
    """
    moved_items = 0
    now = datetime.now().isoformat()
    for src_id in src_ids:
        members = conn.execute(
            "SELECT catalog_item_id FROM catalog_cluster_members WHERE cluster_id = ?",
            (src_id,)
        ).fetchall()
        for m in members:
            conn.execute("""
                INSERT OR IGNORE INTO catalog_cluster_members
                    (cluster_id, catalog_item_id, role, similarity_score)
                VALUES (?, ?, 'duplicate', 1.0)
            """, (target_id, m["catalog_item_id"]))
            moved_items += 1
        conn.execute("DELETE FROM catalog_cluster_members WHERE cluster_id = ?", (src_id,))
        conn.execute("""
            UPDATE catalog_clusters
            SET status = 'rejected', reviewed_by = ?, reviewed_at = ?
            WHERE cluster_id = ?
        """, (user_id, now, src_id))
    conn.commit()
    return {"merged_cluster_count": len(src_ids), "merged_item_count": moved_items}


def _release_cluster_catalog(conn, cluster_id: str, cat_item_id: str):
    """확정 클러스터를 해제/삭제할 때 연결된 카탈로그를 안전하게 정리.

    핵심: 같은 카탈로그(cat_item_id)를 가리키는 '다른 확정 클러스터'가 있으면
          카탈로그를 삭제하지 않고, 이 클러스터 몫(멤버 품목)의 연결/이력만 제거한다.
          아무도 안 쓰는 경우에만 카탈로그 전체를 삭제한다.

    이것이 공유 카탈로그 삭제 버그(입찰2 삭제 시 입찰1 카탈로그까지 삭제)의 수정.
    """
    if not cat_item_id:
        return
    is_catalog = conn.execute(
        "SELECT catalog_item_id FROM catalog_items WHERE catalog_item_id = ?",
        (cat_item_id,)
    ).fetchone()
    if not is_catalog:
        return

    # 이 카탈로그를 가리키는 '다른' 확정 클러스터 수
    others = conn.execute("""
        SELECT COUNT(*) FROM catalog_clusters
        WHERE representative_item_id = ?
          AND cluster_id != ?
          AND status = 'accepted'
    """, (cat_item_id, cluster_id)).fetchone()[0]

    # 이 클러스터의 멤버 품목 id 목록
    member_rows = conn.execute(
        "SELECT catalog_item_id FROM catalog_cluster_members WHERE cluster_id = ?",
        (cluster_id,)
    ).fetchall()
    item_ids = [r["catalog_item_id"] for r in member_rows]

    if others > 0:
        # ── 공유 중 → 카탈로그 보존, 이 클러스터 몫만 정리 ──
        if item_ids:
            ph = ",".join("?" * len(item_ids))
            # 이 클러스터 멤버 품목의 가격이력만 제거 (item_id로 정확히 식별)
            conn.execute(
                f"DELETE FROM price_history WHERE catalog_item_id = ? AND item_id IN ({ph})",
                [cat_item_id] + item_ids
            )
            # 이 클러스터 멤버 품목의 연결만 해제
            conn.execute(
                f"""UPDATE submission_items
                    SET catalog_item_id = NULL, match_status = 'pending',
                        match_confidence = NULL
                    WHERE item_id IN ({ph})""",
                item_ids
            )
        # catalog_items 자체는 보존 (다른 확정 클러스터가 사용 중)
    else:
        # ── 아무도 안 쓰면 → 기존처럼 전체 삭제 ──
        conn.execute("DELETE FROM price_history WHERE catalog_item_id = ?", (cat_item_id,))
        conn.execute("""
            UPDATE submission_items
            SET catalog_item_id = NULL, match_status = 'pending', match_confidence = NULL
            WHERE catalog_item_id = ?
        """, (cat_item_id,))
        conn.execute("DELETE FROM catalog_items WHERE catalog_item_id = ?", (cat_item_id,))


def reopen_cluster(conn, cluster_id: str, user_id: str):
    """확정/거부/보류 클러스터를 검토 대기로 되돌림.
    확정 상태였던 경우 자동 생성된 catalog_item 및 price_history도 삭제."""
    cluster = conn.execute(
        "SELECT * FROM catalog_clusters WHERE cluster_id = ?", (cluster_id,)
    ).fetchone()
    if not cluster:
        raise ValueError(f"클러스터를 찾을 수 없습니다: {cluster_id}")

    if cluster["status"] == "accepted":
        cat_item_id = cluster["representative_item_id"]
        _release_cluster_catalog(conn, cluster_id, cat_item_id)

    conn.execute("""
        UPDATE catalog_clusters
        SET status = 'pending', reviewed_by = NULL, reviewed_at = NULL
        WHERE cluster_id = ?
    """, (cluster_id,))
    conn.commit()


def delete_cluster(conn, cluster_id: str):
    """클러스터 삭제. 확정 상태면 catalog_item/price_history도 함께 삭제."""
    cluster = conn.execute(
        "SELECT * FROM catalog_clusters WHERE cluster_id = ?", (cluster_id,)
    ).fetchone()
    if not cluster:
        return

    if cluster["status"] == "accepted":
        cat_item_id = cluster["representative_item_id"]
        _release_cluster_catalog(conn, cluster_id, cat_item_id)

    conn.execute("DELETE FROM catalog_cluster_members WHERE cluster_id = ?", (cluster_id,))
    conn.execute("DELETE FROM catalog_clusters WHERE cluster_id = ?", (cluster_id,))
    conn.commit()


def reset_bid_clusters(conn, bid_id: str) -> int:
    """입찰의 모든 클러스터 초기화. 확정 클러스터의 catalog_item도 함께 제거."""
    clusters = conn.execute(
        "SELECT * FROM catalog_clusters WHERE bid_id = ?", (bid_id,)
    ).fetchall()

    for cl in clusters:
        if cl["status"] == "accepted":
            cat_item_id = cl["representative_item_id"]
            _release_cluster_catalog(conn, cl["cluster_id"], cat_item_id)

    cluster_ids = [cl["cluster_id"] for cl in clusters]
    if cluster_ids:
        ph = ",".join("?" * len(cluster_ids))
        conn.execute(f"DELETE FROM catalog_cluster_members WHERE cluster_id IN ({ph})", cluster_ids)
        conn.execute(f"DELETE FROM catalog_clusters WHERE cluster_id IN ({ph})", cluster_ids)

    conn.commit()
    return len(clusters)
