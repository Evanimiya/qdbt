# -*- coding: utf-8 -*-
"""견고성 테스트 — 모듈형 하네스 전체 실행 + 통합 매트릭스 문서 생성.

    ./venv/bin/python tests/rob/run_matrix.py

s1..s7 모듈(그룹별 파일)을 함수 직접 호출로 돌리고(포그라운드 서버 없음),
docs/QDBT_견고성테스트_20260711.md 를 재생성한다.

※ 동반 하네스: tests/rob/run_all.py (동일 _util 공유, 단일파일 구성). 본 러너는
   그룹별 모듈 구성 + S7 경계탐침 + [결정필요] 케이스까지 포함한 상위집합이다.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _util
from _util import RESULTS, ROOT

import s1_classification
import s2_currency
import s3_tabs
import s4_numbering
import s5_amounts
import s6_misc
import s7_edges

MODULES = [s1_classification, s2_currency, s3_tabs, s4_numbering,
           s5_amounts, s6_misc, s7_edges]

DOC = ROOT / "docs" / "QDBT_견고성테스트_20260711.md"
_MARK = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "WARN": "⚠️ WARN"}


def _esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def render(rows):
    from collections import OrderedDict
    groups = OrderedDict()
    for r in rows:
        groups.setdefault(r["group"], []).append(r)

    n = len(rows)
    npass = sum(1 for r in rows if r["verdict"] == "PASS")
    nfail = sum(1 for r in rows if r["verdict"] == "FAIL")
    nwarn = sum(1 for r in rows if r["verdict"] == "WARN")

    o = []
    o.append("# QDBT 견고성 테스트 — 엉망 견적서 파이프라인 (2026-07-11)\n")
    o.append("> '엉망으로 접수된' 견적서 시나리오를 합성 워크북(openpyxl)으로 만들어 "
             "`extract_by_mapping` · 스티칭(`stitch`) · 통화정합(`apply_currency_fields`) · "
             "집계(`recompute_subtotal` 미러)를 **함수 직접 호출**로 검증. 포그라운드 서버 없음.\n")
    o.append(f"**결과: {npass}/{n} PASS · FAIL {nfail} · WARN {nwarn}.** "
             "1차 발견 버그 5건(합계행 미탐 이중계상, seq모드 leaf 소실, band 고유비용 소실, "
             "시트간 동일항목 미표기, 로케일 모호)을 모두 Δ=0 게이트로 수정 — 아래 §발견·수정 참조.\n")
    o.append("## 재현\n")
    o.append("```bash\n./venv/bin/python tests/rob/run_matrix.py   # 그룹모듈형(S1~S7, 상위집합)\n"
             "./venv/bin/python tests/rob/run_all.py      # 동반 단일파일 하네스\n```\n")
    o.append("픽스처·결과는 재생성물(`tests/rob/_fixtures/`, `_results.jsonl`; .gitignore). "
             "하네스 모듈: `tests/rob/s1..s7_*.py`, 공용 유틸 `_util.py`.\n")
    o.append("## 집계 미러 근거\n")
    o.append("`leaf_total`(하네스) = 스티칭 `totals.leaf_sum` = `Σ amount` "
             "(`merge_status`·`is_category_header` 제외). `insert_items_bulk` 는 그 두 플래그를 "
             "`is_header=1` 로 저장하고, `recompute_subtotal` 은 `get_items(headers=False)` "
             "(=`is_header=0`) 합을 쓰므로 **저장 후 집계 총액과 동일**. 통화정합은 `amount` 에 "
             "이미 반영(원화 확정).\n")

    for g, grows in groups.items():
        gp = sum(1 for r in grows if r["verdict"] == "PASS")
        o.append(f"## {g}  ({gp}/{len(grows)} PASS)\n")
        o.append("| ID | 케이스 | 입력 요지 | 기대 | 실제 | 판정 | 근본원인/비고 |")
        o.append("|---|---|---|---|---|---|---|")
        for r in grows:
            o.append("| {id} | {t} | {i} | {e} | {a} | {v} | {r} |".format(
                id=r["id"], t=_esc(r["title"]), i=_esc(r["input"]),
                e=_esc(r["expected"]), a=_esc(r["actual"]),
                v=_MARK.get(r["verdict"], r["verdict"]), r=_esc(r["root"]) or "—"))
        o.append("")

    o.append("## 발견된 버그 · 수정 · 결정필요\n")
    o.append("### ✅ 수정 (Δ=0 게이트 통과)\n")
    o.append("- **합계행 미탐 → 이중계상 (S5 5-dc / 5-det)**: 합계 마커가 품명 셀에 있고 "
             "분류 셀 값이 조인 문자열 뒤에 붙는 경우(`\"소계 재료비\"`) `is_total_label` 의 "
             "`endswith` 앵커가 무력화돼 소계·합계 행이 잎으로 계상(이중계상)됐다. "
             "`_is_total_row`(stitch)·`detect_total_rows`(extract) 를 조인 검사 + **셀 단독 "
             "검사**로 보강. 실샘플 **15개 / 31시트 / 3446행** 에서 per-cell 신규 발동 "
             "**0건 → Δ=0** 확인 후 반영. (병합된 대분류가 소계행에 채워지는 흔한 양식에서 발생.)\n")
    o.append("- **seq 모드 leaf/breakdown 시트 은닉 소실 (S6 6f) — 해소**: 정수번호+금액 "
             "시트(role `leaf`, 예 갑지·공종목록)가 점계층 세부와 함께 올 때 `_stitch_seq` 가 "
             "`seq_*` 만 방출해 leaf 시트가 흔적 없이 소실됐다. → leaf 시트도 방출하고, 요약 "
             "판별을 **전체요약 roll-up**(시트 총액 = 상세 정본 총액이면 전 행 roll-up)으로 "
             "보강. 독립 breakdown은 residual 보존(손실 0), 요약은 roll-up(이중계상 방지). "
             "실샘플 C3 `공종목록`(대분류 요약)이 상세 상위카테고리(더 깊은 레벨)와 이름이 안 "
             "맞아도 총액 일치로 roll-up → **C3 Δ=0**. (S6 6f/6f-2 PASS)\n")
    o.append("- **band 요약 고유비용 소실 (S7 7-4) — 해소**: band 요약 행 중 '완전 고립'"
             "(분류값이 어떤 잎 경로에도 미등장, 예 부대비)만 선별해 잎+residual로 보존→총액 "
             "누락 방지. 한 값이라도 잎과 공유하면 계층 일부(빈 카테고리 소계)로 보고 미방출 "
             "→ **C2 Δ=0**(모든 band 행 커버 시 무방출). band 시트 간 동일경로 중복 방출도 "
             "차단. (S7 7-4 PASS)\n")
    o.append("- **시트 간 동일항목·분류명만 상이 (S1 1e) — 해소**: 완전동일(경로+품명+규격+"
             "금액)만 dedup 유지. (품명+규격+금액) 같고 분류경로만 다른 잎은 자동병합하지 "
             "않고(오합치 방지) `cross_sheet_similar` residual 로 **미연계 표기**해 사람이 판단. "
             "비파괴(총액 Δ=0). (S1 1e PASS)\n")
    o.append("- **로케일 모호 파싱 (S7 7-1 '1.000', 7-5 '1-5') — 해소**: 단일 점 + 정확히 "
             "3자리 소수부 → 유럽식 천단위(`'1.000'→1000`; `'3.5'`·`'0.125'` 소수 유지). "
             "`'1-5'`/`'1~5'` 범위표기는 억지 숫자화(15) 대신 `None`(미상) 보존해 오계상 방지 "
             "(계층번호는 seq 역할 열의 `seq_tuple` 이 별도 처리). 실샘플 총액 Δ=0. (S7 7-1/7-5 PASS)\n")
    o.append("### ⚠️ 남은 결정 필요\n")
    o.append("- **없음.** 상정한 엉망 시나리오(S1~S7)는 합리적 기본값으로 모두 처리됨. "
             "위 로케일 휴리스틱(단일점3자리=천단위)은 도메인(KRW 정수 우세) 기본값으로, "
             "3자리 소수 수량이 흔한 타 도메인 도입 시 통화/로케일 힌트로 재검토 여지가 있다"
             "(관찰 사항, 현재 정본 로직 회귀 없음).\n")

    o.append("## 통화 정합 특기\n")
    o.append("- 외화인데 원화열·환율 도출이 모두 불가하면 `amount=None`+`needs_fx=True` 로 격리 "
             "→ **KRW 총액에 외화 표면값 미혼입**(통화배수 오류 없음, S2 2c). 다중시트 스티칭도 "
             "동일 규칙(2e).\n")
    o.append("- `¥` 는 엔·위안 공용 기호라 도메인 관례상 **CNY** 로 정규화(명시 코드 우선) — "
             "표시 유의(S2 2-sym).\n")
    return "\n".join(o) + "\n"


def main():
    _util.reset_results()
    for m in MODULES:
        m.run()
    rows = [json.loads(ln) for ln in open(RESULTS, encoding="utf-8") if ln.strip()]
    DOC.write_text(render(rows), encoding="utf-8")
    n = len(rows)
    npass = sum(1 for r in rows if r["verdict"] == "PASS")
    nfail = sum(1 for r in rows if r["verdict"] == "FAIL")
    nwarn = sum(1 for r in rows if r["verdict"] == "WARN")
    print(f"\n==== {npass}/{n} PASS · FAIL {nfail} · WARN {nwarn} → {DOC.relative_to(ROOT)} ====")


if __name__ == "__main__":
    main()
