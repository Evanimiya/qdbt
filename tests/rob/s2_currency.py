# -*- coding: utf-8 -*-
"""시나리오 2: 다중 통화.

USD+KRW+JPY 혼재, 통화기호 혼입(¥·€·₩·$·元), 환율 도출 실패 외화,
원화환산(amount_krw) 열 존재/부재. → 원화 총액이 '통화배수 오류' 없이 정합되는지.
"""
import _util
from _util import build_wb, leaf_total, approx, Recorder
from extractors.extract_by_mapping import (
    extract_by_mapping, normalize_currency_code, apply_currency_fields,
)
from extractors.stitch import stitch_sheets


def run():
    rec = Recorder("S2 다중통화")

    # ── 2b(먼저). 통화기호 정규화 ──
    cases = {"¥": "CNY", "€": "EUR", "₩": "KRW", "$": "USD", "元": "CNY",
             "원": "KRW", "WON": "KRW", "달러": "USD", "JPY": "JPY", "엔": "JPY",
             "": "KRW", "USD": "USD"}
    bad = {k: normalize_currency_code(k) for k in cases
           if normalize_currency_code(k) != cases[k]}
    rec.add("2-sym", "통화기호/명칭 정규화(¥€₩$元 등)",
            "12종 기호·명칭 입력",
            "¥→CNY(도메인관례), $→USD, ₩/원/공란→KRW, 元→CNY, 엔→JPY",
            f"불일치: {bad or '없음'}",
            "PASS" if not bad else "FAIL",
            "" if not bad else "normalize_currency_code 매핑 오류")

    # ── 2a. USD+KRW+JPY 혼재 + 원화금액(amount_krw) 열 존재 ──
    p = build_wb("s2a.xlsx", [("Sheet", [
        ["품명", "통화", "금액", "원화금액"],
        ["볼트-KR", "KRW", 10000, None],
        ["칩-US", "USD", 100, 130000],
        ["코일-JP", "JPY", 1000, 9000],
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "name", 2: "currency", 3: "amount", 4: "amount_krw"}, 1)
    byname = {it["name_normalized"]: it for it in r["items"]}
    us = byname["칩-US"]
    tot = leaf_total(r["items"])
    ok = (approx(tot, 149000)                     # 10000+130000+9000
          and us["amount"] == 130000              # USD 표면값 100이 아니라 원화확정
          and us.get("amount_orig") == 100
          and us.get("unit_price_currency_in_source") == "USD")
    rec.add("2a", "USD+KRW+JPY 혼재(원화금액 열 존재)",
            "각 행 원화확정, 통화배수 오류 없이 총액 정합",
            "총액 149,000 · USD행 amount=130000(orig 100)",
            f"총액={tot} · USD amount={us['amount']} orig={us.get('amount_orig')}",
            "PASS" if ok else "FAIL",
            "" if ok else "원화환산 미적용 or 외화표면값 혼입")

    # ── 2c. 환율 도출 실패 외화 (원화열 없음) → KRW 총액에 외화표면값 혼입 금지 ──
    p = build_wb("s2c.xlsx", [("Sheet", [
        ["품명", "통화", "금액"],
        ["볼트", "KRW", 10000],
        ["칩", "USD", 100],       # 원화열·환율 도출 불가
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "name", 2: "currency", 3: "amount"}, 1)
    byname = {it["name_normalized"]: it for it in r["items"]}
    us = byname["칩"]
    tot = leaf_total(r["items"])
    ok = (approx(tot, 10000)                  # 100(USD)이 KRW로 혼입되면 10100 (버그)
          and us["amount"] is None
          and us.get("needs_fx") is True)
    rec.add("2c", "환율 도출 실패 외화(원화열 부재)",
            "외화 표면값이 KRW 총액에 혼입되면 안 됨(H3)",
            "총액 10,000 · USD행 amount=None · needs_fx=True",
            f"총액={tot} · USD amount={us['amount']} needs_fx={us.get('needs_fx')}",
            "PASS" if ok else "FAIL",
            "" if ok else "외화표면값 KRW 오합산(통화배수 오류)")

    # ── 2d. amount_krw 부재지만 원화단가(price_krw)로 환율 역산 → 원화 확정 ──
    p = build_wb("s2d.xlsx", [("Sheet", [
        ["품명", "통화", "수량", "단가", "금액", "원화단가"],
        ["칩", "USD", 10, 100, 1000, 130000],   # fx=130000/100=1300 → amount=1000*1300
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "name", 2: "currency", 3: "qty",
                                        4: "price", 5: "amount", 6: "price_krw"}, 1)
    it0 = r["items"][0]
    line_ok = approx(it0["amount"], (it0["unit_price"] or 0) * (it0["quantity"] or 0))
    ok = (it0.get("fx_rate_used") == 1300 and it0["unit_price"] == 130000
          and approx(it0["amount"], 1300000) and line_ok)
    rec.add("2d", "원화단가로 환율 역산 → 원화금액 확정",
            "fx=1300 역산, 단가·금액 원화확정, amount=단가×수량 정합",
            f"fx={it0.get('fx_rate_used')} 단가={it0['unit_price']} 금액={it0['amount']} 라인정합={line_ok}",
            f"fx={it0.get('fx_rate_used')} 단가={it0['unit_price']} 금액={it0['amount']}",
            "PASS" if ok else "FAIL",
            "" if ok else "환율 역산/원화확정 오류")

    # ── 2e. 다중시트 통화 정합(스티칭 경로도 동일 규칙) ──
    p = build_wb("s2e.xlsx", [
        ("국내", [["대분류", "품명", "통화", "금액"],
                  ["국내자재", "볼트", "KRW", 20000]], []),
        ("수입", [["대분류", "품명", "통화", "금액", "원화금액"],
                  ["수입자재", "칩", "USD", 100, 130000]], []),
    ])
    specs = [
        {"sheet": "국내", "mapping": {1: "cat1", 2: "name", 3: "currency", 4: "amount"}, "header_row": 1},
        {"sheet": "수입", "mapping": {1: "cat1", 2: "name", 3: "currency", 4: "amount", 5: "amount_krw"}, "header_row": 1},
    ]
    res = stitch_sheets(p, specs)
    tot = res["totals"]["leaf_sum"]
    us = next((it for it in res["items"] if it["name_normalized"] == "칩"), {})
    ok = approx(tot, 150000) and us.get("amount") == 130000
    rec.add("2e", "다중시트 통화 정합(스티칭)",
            "스티칭 경로도 원화 확정 → 총액 통화배수 오류 없음",
            f"총액 150,000 · 수입칩 amount={us.get('amount')}",
            f"총액={tot} · 칩 amount={us.get('amount')}",
            "PASS" if ok else "FAIL",
            "" if ok else "스티칭 통화 정합 미적용")

    return rec.flush()


if __name__ == "__main__":
    run()
