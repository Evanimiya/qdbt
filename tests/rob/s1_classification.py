# -*- coding: utf-8 -*-
"""시나리오 1: 분류가 엉망으로 끊김.

비연속 레벨(중+세, 소 건너뜀), 대분류 없이 시작, 행마다 depth 불일치,
중간 빈 분류 셀, 분류명만 다르고 동일 항목(시트 간).
"""
import _util
from _util import build_wb, leaf_total, approx, Recorder
from extractors.extract_by_mapping import extract_by_mapping, _LEVEL_PLACEHOLDER
from extractors.stitch import stitch_sheets

PH = set(_LEVEL_PLACEHOLDER.values())


def run():
    rec = Recorder("S1 분류끊김")

    # ── 1a. 비연속 레벨 (중+세, 소 건너뜀) ──
    p = build_wb("s1a.xlsx", [("Sheet", [
        ["중분류", "세분류", "품명", "금액"],
        ["기구부", "차폐", "납 BLOCK", 100],
        ["기구부", "지지", "브래킷", 200],
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "cat2", 2: "cat4", 3: "name", 4: "amount"}, 1)
    it0 = r["items"][0]
    parts = it0["path"].split(" > ")
    ok = (r["n_items"] == 2 and it0["depth"] == 4
          and parts[0] in PH and parts[2] in PH        # 대·소 placeholder
          and parts[1] == "기구부" and parts[3] == "차폐"
          and it0.get("is_level_residual") is True
          and approx(leaf_total(r["items"]), 300))
    rec.add("1a", "비연속 레벨(중+세, 소 skip)",
            "cat2,cat4 매핑 · 세분류가 소분류 자리로 당겨지면 안 됨",
            "세분류 depth4 보존, 대·소 placeholder, residual, 총액300",
            f"depth={it0['depth']} path={it0['path']} resid={it0.get('is_level_residual')} tot={leaf_total(r['items'])}",
            "PASS" if ok else "FAIL",
            "" if ok else "레벨 절대배치/placeholder 미동작")

    # ── 1b. 대분류 없이 시작 (중분류부터) ──
    p = build_wb("s1b.xlsx", [("Sheet", [
        ["중분류", "소분류", "품명", "금액"],
        ["전장부", "제어", "PLC", 500],
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "cat2", 2: "cat3", 3: "name", 4: "amount"}, 1)
    it0 = r["items"][0]
    parts = it0["path"].split(" > ")
    ok = (it0["depth"] == 3 and parts[0] in PH
          and parts[1] == "전장부" and parts[2] == "제어"
          and it0.get("is_level_residual") is True)
    rec.add("1b", "대분류 없이 중분류부터 시작",
            "중분류가 대분류로 승격되면 안 됨(placeholder로 자리 보존)",
            "depth3, 대분류 placeholder, 중=전장부, residual",
            f"depth={it0['depth']} path={it0['path']} resid={it0.get('is_level_residual')}",
            "PASS" if ok else "FAIL",
            "" if ok else "상위 레벨 placeholder 미삽입")

    # ── 1c. 행마다 depth 불일치 ──
    p = build_wb("s1c.xlsx", [("Sheet", [
        ["대분류", "중분류", "소분류", "품명", "금액"],
        ["재료비", None, None, "일반자재", 100],
        ["재료비", "기구부", None, "납블록", 200],
        ["재료비", "기구부", "차폐", "순납", 300],
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "cat1", 2: "cat2", 3: "cat3", 4: "name", 5: "amount"}, 1)
    names = {it["name_normalized"] for it in r["items"]}
    ok = (r["n_items"] == 3 and names == {"일반자재", "납블록", "순납"}
          and approx(leaf_total(r["items"]), 600)
          and all(it["path"].startswith("재료비") for it in r["items"]))
    rec.add("1c", "행마다 depth 불일치",
            "각 행 depth 달라도 잎 3건·총액600 보존, 재료비 하위 유지",
            "3건, 총액600, 모두 재료비 루트",
            f"n={r['n_items']} names={names} tot={leaf_total(r['items'])}",
            "PASS" if ok else "FAIL",
            "" if ok else "depth 불일치 처리 오류")

    # ── 1d. 중간 빈 분류 셀 (fill-down 상속) ──
    p = build_wb("s1d.xlsx", [("Sheet", [
        ["대분류", "중분류", "품명", "금액"],
        ["재료비", "기구부", "납블록", 100],
        [None, None, "브래킷", 200],       # 대·중 빈칸 → 상속 기대
        [None, "전장부", "PLC", 300],       # 중만 바뀜, 대 상속
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "cat1", 2: "cat2", 3: "name", 4: "amount"}, 1)
    by = {it["name_normalized"]: it["path"] for it in r["items"]}
    ok = (r["n_items"] == 3
          and by.get("브래킷") == "재료비 > 기구부"
          and by.get("PLC") == "재료비 > 전장부"
          and approx(leaf_total(r["items"]), 600))
    rec.add("1d", "중간 빈 분류 셀 fill-down",
            "빈 분류 셀이 위 행에서 상속되어야",
            "브래킷=재료비>기구부, PLC=재료비>전장부, 총액600",
            f"paths={by} tot={leaf_total(r['items'])}",
            "PASS" if ok else "FAIL",
            "" if ok else "fill-down 상속 실패")

    # ── 1e. 항목 연결 3단계 원칙 [Fix3 재작업] ──
    #  1단계 완전합치(코드): 경로+품명+규격+금액 정확일치만 자동 dedup.
    #  유사(품명·규격·금액 같고 분류경로만 다름)는 스티칭(무LLM)이 병합/미연계 종결하지
    #  않고 별도 정본 잎으로 보존(총액 Δ=0) → 2단계 LLM 유사도(clusterer/matcher)로 위임.
    mp = {1: "cat1", 2: "name", 3: "spec", 4: "qty", 5: "price", 6: "amount"}
    # (i) 유사(다른 경로) → 별도 잎 보존, 코드-종결 표기 없음
    p = build_wb("s1e.xlsx", [
        ("자재A", [["대분류", "품명", "규격", "수량", "단가", "금액"],
                   ["구매자재", "베어링", "6203ZZ", 1, 5000, 5000]], []),
        ("자재B", [["대분류", "품명", "규격", "수량", "단가", "금액"],
                   ["수입자재", "베어링", "6203ZZ", 1, 5000, 5000]], []),
    ])
    res = stitch_sheets(p, [{"sheet": "자재A", "mapping": mp, "header_row": 1},
                            {"sheet": "자재B", "mapping": mp, "header_row": 1}])
    kept = [it for it in res["items"] if not it.get("merge_status")]
    tot = res["totals"]["leaf_sum"]
    terminal = [r for r in res["residuals"] if r["reason"] == "cross_sheet_similar"]
    code_flag = any(it.get("_cross_sheet_similar") for it in kept)
    sim_ok = (len(kept) == 2 and approx(tot, 10000)
              and not terminal and not code_flag)   # 코드 자동병합·종결표기 없음
    # (ii) 완전동일(경로까지 동일) → 1단계 코드 dedup
    p2 = build_wb("s1e2.xlsx", [
        ("자재A", [["대분류", "품명", "규격", "수량", "단가", "금액"],
                   ["구매자재", "베어링", "6203ZZ", 1, 5000, 5000]], []),
        ("자재B", [["대분류", "품명", "규격", "수량", "단가", "금액"],
                   ["구매자재", "베어링", "6203ZZ", 1, 5000, 5000]], []),
    ])
    res2 = stitch_sheets(p2, [{"sheet": "자재A", "mapping": mp, "header_row": 1},
                              {"sheet": "자재B", "mapping": mp, "header_row": 1}])
    exact_ok = (res2["reconciliation"]["duplicates"] == 1
                and approx(res2["totals"]["leaf_sum"], 5000))
    ok = sim_ok and exact_ok
    rec.add("1e", "항목 연결 3단계 원칙(완전합치=코드, 유사도=LLM 위임) [Fix3]",
            "시트 간 동일 품명·규격·금액 — 유사(다른경로) vs 완전동일(같은경로)",
            "완전동일만 코드 dedup, 유사는 별도 잎 보존(총액 불변)·코드종결 없이 LLM에 위임",
            f"유사:kept={len(kept)} 총액={tot} 종결표기={len(terminal)}/{code_flag} | 완전동일:dedup={res2['reconciliation']['duplicates']} 총액={res2['totals']['leaf_sum']}",
            "PASS" if ok else "FAIL",
            "" if ok else "3단계 원칙 위반(코드 자동병합/종결 or exact dedup 실패)")

    return rec.flush()


if __name__ == "__main__":
    run()
