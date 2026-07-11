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

    # ── 7-1. 유럽식 단일점 천단위 '1.000' (로케일 모호) ──
    got = parse_amount("1.000")
    # 도메인(KRW 정수 우세)에선 '.' 단독 1개는 소수점 → 1.0. 유럽식이면 1000.
    ok = got == 1  # 현 정책상 소수 → 1 (관찰: 로케일 힌트 없으면 모호)
    rec.add("7-1", "유럽식 단일점 천단위 '1.000'",
            "'1.000' (로케일 미상)",
            "정책: 단일점 1개=소수(1.0). 유럽식(1000) 여부는 로케일 힌트 필요",
            f"parse_amount('1.000')={got}",
            "WARN",
            "[결정필요] 통화/로케일 힌트 없이는 '1.000'=1 vs 1000 모호. 콤마 단독만 천단위.")

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
    rec.add("7-4", "band 요약 '상세에 없는 비용'(부대비) 보존",
            "갑지(band, 대분류별 금액)에 상세에 없는 부대비 500",
            "부대비가 items/residual 로 흔적 보존(은닉 소실 금지)",
            f"총액={res['totals']['leaf_sum']}(상세3000만) · 부대비흔적={'있음' if budae_seen else '없음(소실)'}",
            "PASS" if budae_seen else "WARN",
            "" if budae_seen else ("[결정필요] band 아키타입은 요약시트를 골격으로만 써 요약 "
                                   "고유 비용(부대비 등)이 은닉 소실. band 금액 무조건 방출 시 "
                                   "이중/삼중 계상(C2 회귀) → 별도 설계결정 필요."))

    # ── 7-5. 범위표기 '1-5' 금액열 오계상 위험(비금액 문자열) ──
    got = parse_amount("1-5")
    # '1-5'는 범위표기지만 파서는 비숫자 구분자를 제거해 15로 해석.
    ok = got == 15
    rec.add("7-5", "범위표기 '1-5' 금액 파싱(관찰)",
            "금액열에 '1-5' 같은 범위/비금액 문자열",
            "예측 불가 표기 — 파서가 15로 해석(오계상 위험, 참고)",
            f"parse_amount('1-5')={got}",
            "WARN",
            "[관찰] 금액열엔 범위표기가 드묾. 힌트 없이 '1-5'=15 vs 범위 판별 불가.")

    return rec.flush()


if __name__ == "__main__":
    run()
