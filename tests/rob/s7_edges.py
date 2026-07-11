# -*- coding: utf-8 -*-
"""시나리오 7: 경계 탐침(결정 필요/관찰).

로케일·도메인 힌트 없이는 모호하거나, 안전한 자동수정이 어려운 경계 케이스.
(동반 하네스 run_all.py 의 S7 경계탐침과 정합.)
"""
import _util
from _util import build_wb, leaf_total, approx, Recorder
from extractors.stitch import stitch_sheets
from core.numparse import parse_amount


def run():
    rec = Recorder("S7 경계탐침")

    # ── 7-1. 단일점 천단위 휴리스틱 '1.000'→1000 [Fix4 반영] ──
    got = parse_amount("1.000")
    dec = parse_amount("3.5")   # 소수는 유지되어야
    z = parse_amount("0.125")   # 정수부 0 → 소수 유지
    ok = got == 1000 and dec == 3.5 and z == 0.125
    rec.add("7-1", "단일점 천단위 휴리스틱 '1.000'",
            "'1.000'(3자리 소수부) · '3.5' · '0.125'",
            "'1.000'→1000(천단위), '3.5'→3.5·'0.125'→0.125(소수 유지)",
            f"1.000={got} 3.5={dec} 0.125={z}",
            "PASS" if ok else "FAIL",
            "" if ok else "단일점3자리 천단위 휴리스틱 오동작")

    # ── 7-4. band 요약시트의 '상세에 없는 비용'(부대비) 은닉 소실 ──
    p = build_wb("s7_4.xlsx", [
        ("갑지", [["대분류", "금액"], ["재료비", 3000], ["부대비", 500]], []),
        ("상세", [["대분류", "중분류", "품명", "금액"],
                  ["재료비", "기구부", "볼트", 1000],
                  ["재료비", "전장부", "PLC", 2000]], []),
    ])
    specs = [
        {"sheet": "갑지", "mapping": {1: "cat1", 2: "amount"}, "header_row": 1},
        {"sheet": "상세", "mapping": {1: "cat1", 2: "cat2", 3: "name", 4: "amount"}, "header_row": 1},
    ]
    res = stitch_sheets(p, specs)
    all_txt = " ".join(str(it.get("name_normalized")) for it in res["items"]) + \
        " ".join(str(r) for r in res["residuals"])
    budae_seen = "부대비" in all_txt
    tot = res["totals"]["leaf_sum"]
    ok = budae_seen and approx(tot, 3500)   # 상세3000 + 부대비500(고유비용 보존)
    rec.add("7-4", "band 요약 고유비용(부대비) 보존 [Fix2]",
            "갑지(band, 대분류별 금액)에 상세에 없는 부대비 500",
            "부대비 잎+residual 보존, 총액 3,500(누락 없음)",
            f"총액={tot} · 부대비={'보존' if budae_seen else '소실'}",
            "PASS" if ok else "FAIL",
            "" if ok else "band 고유비용 소실")

    # ── 7-5. 범위표기 '1-5' → 미상(None) 보존 [Fix4 반영] ──
    got = parse_amount("1-5")
    got2 = parse_amount("1~5")
    ok = got is None and got2 is None
    rec.add("7-5", "범위표기 '1-5' 미상 보존",
            "금액열에 '1-5'/'1~5' 범위 문자열",
            "억지 숫자화(15) 대신 None(미상) 보존 → 오계상 방지",
            f"'1-5'={got} '1~5'={got2}",
            "PASS" if ok else "FAIL",
            "" if ok else "범위표기 오계상(숫자화)")

    return rec.flush()


if __name__ == "__main__":
    run()
