# -*- coding: utf-8 -*-
"""시나리오 5: 금액 이상치.

괄호 음수·△·▲·−, 천단위 콤마, 통화기호 붙은 금액, 빈 금액/단가,
합계/소계/Subtotal/合計/Total 행 혼입(오탐·미탐), 요약행 이중계상.
"""
import _util
from _util import build_wb, leaf_total, approx, Recorder
from extractors.extract_by_mapping import (
    extract_by_mapping, is_total_label, detect_total_rows,
)
from extractors.stitch import stitch_sheets
from core.numparse import parse_amount


def run():
    rec = Recorder("S5 금액이상치")

    # ── 5-num. 숫자·부호 파싱 ──
    NUM = {
        "(1,234)": -1234, "△1,000": -1000, "▲1000": -1000,
        "−1000": -1000, "－1000": -1000,
        "1,234,000": 1234000, "₩1,000": 1000, "$1,234.56": 1234.56,
        "1.234.567": 1234567, "1.234,56": 1234.56,
        "3.5": 3.5, "": None, "  ": None, "합계": None,
        "1,000원": 1000, "￦2,500": 2500,
    }
    bad = {}
    for s, exp in NUM.items():
        got = parse_amount(s)
        if exp is None:
            if got is not None:
                bad[repr(s)] = got
        elif got is None or abs(got - exp) > 1e-6:
            bad[repr(s)] = got
    rec.add("5-num", "금액 파싱(괄호·△▲−·콤마·통화기호·유럽식)",
            f"{len(NUM)}종 표기",
            "부호 보존·천단위 제거·통화기호 제거·빈값 None",
            f"불일치: {bad or '없음'}",
            "PASS" if not bad else "FAIL",
            "" if not bad else "core.numparse.parse_amount 파싱 오류")

    # ── 5-fp. 합계 오탐 방지(품명 우연 포함) ──
    FALSE = ["소계장치", "합계금액표", "Total Station", "Grandstand",
             "Summary", "consumables", "회계 프로그램", "설계변경", "통계 모듈"]
    fp = [t for t in FALSE if is_total_label(t)]
    rec.add("5-fp", "합계 오탐 방지",
            "품명에 우연히 '계/합계/total' 포함",
            "모두 합계행 아님(False)",
            f"오탐: {fp or '없음'}",
            "PASS" if not fp else "FAIL",
            "" if not fp else "is_total_label 부분문자열 오탐")

    # ── 5-fn. 합계 미탐 방지(실제 합계행) ──
    TRUE = ["재료비 합계", "小計", "合計", "Subtotal", "총계", "합계 : 1,200,000",
            "계", "합 계", "sub-total", "累計", "총 합계"]
    fn = [t for t in TRUE if not is_total_label(t)]
    rec.add("5-fn", "합계 미탐 방지",
            "실제 합계/소계/CJK/영문 합계행",
            "모두 합계행(True)",
            f"미탐: {fn or '없음'}",
            "PASS" if not fn else "FAIL",
            "" if not fn else "is_total_label 실제 합계행 미검출")

    # ── 5-dc. 합계행 혼입 → 이중계상 방지(스티칭 passthrough 자동 제외) ──
    p = build_wb("s5dc.xlsx", [("Sheet", [
        ["대분류", "품명", "수량", "단가", "금액"],
        ["재료비", "볼트", 10, 100, 1000],
        ["재료비", "너트", 20, 100, 2000],
        ["재료비", "소계", None, None, 3000],       # 소계행(혼입)
        ["재료비", "합계", None, None, 3000],       # 합계행(혼입)
    ], [])])
    specs = [{"sheet": "Sheet", "mapping": {1: "cat1", 2: "name", 3: "qty",
             4: "price", 5: "amount"}, "header_row": 1}]
    res = stitch_sheets(p, specs)
    tot = res["totals"]["leaf_sum"]
    names = {it["name_normalized"] for it in res["items"] if not it.get("merge_status")}
    ok = approx(tot, 3000) and "소계" not in names and "합계" not in names
    rec.add("5-dc", "합계/소계행 혼입 이중계상 방지(스티칭)",
            "잎(볼트1000+너트2000) + 소계3000 + 합계3000 혼입",
            "총액 3,000(합계·소계 제외)",
            f"총액={tot} · 잎={names}",
            "PASS" if ok else "FAIL",
            "" if ok else "스티칭이 합계행을 잎으로 계상(이중계상)")

    # ── 5-det. detect_total_rows 가 합계행 후보로 제안 ──
    det = detect_total_rows(p, "Sheet", {1: "cat1", 2: "name", 3: "qty", 4: "price", 5: "amount"}, 1)
    det_names = {d["name"] for d in det}
    ok = "소계" in det_names and "합계" in det_names
    rec.add("5-det", "detect_total_rows 합계행 제안",
            "소계·합계 행 존재",
            "둘 다 제외 후보로 제안",
            f"제안={det_names}",
            "PASS" if ok else "FAIL",
            "" if ok else "detect_total_rows 미제안")

    # ── 5-neg. 괄호/△ 음수가 금액으로 파싱되어 차감 반영 ──
    p = build_wb("s5neg.xlsx", [("Sheet", [
        ["대분류", "품명", "금액"],
        ["조정", "기본공급", 10000],
        ["조정", "할인", "(2,000)"],       # 괄호 음수
        ["조정", "에누리", "△1,000"],       # 삼각 음수
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "cat1", 2: "name", 3: "amount"}, 1)
    byname = {it["name_normalized"]: it["amount"] for it in r["items"]}
    tot = leaf_total(r["items"])
    ok = (byname.get("할인") == -2000 and byname.get("에누리") == -1000
          and approx(tot, 7000))
    rec.add("5-neg", "괄호·삼각 음수 차감 반영",
            "10000 + (2,000) + △1,000",
            "할인=-2000, 에누리=-1000, 총액 7,000",
            f"amounts={byname} 총액={tot}",
            "PASS" if ok else "FAIL",
            "" if ok else "음수 부호 미보존(차감 누락)")

    # ── 5-empty. 빈 금액/단가 행 처리 ──
    p = build_wb("s5empty.xlsx", [("Sheet", [
        ["대분류", "품명", "수량", "단가", "금액"],
        ["재료비", "볼트", 10, 100, 1000],
        ["재료비", "미정품목", None, None, None],   # 금액·단가 없음
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "cat1", 2: "name", 3: "qty", 4: "price", 5: "amount"}, 1)
    tot = leaf_total(r["items"])
    # 금액 없는 행은 총액에 0 기여(파싱 크래시 없이). 추출 자체는 될 수도/스킵될 수도.
    ok = approx(tot, 1000)
    rec.add("5-empty", "빈 금액/단가 행",
            "정상행 1000 + 금액·단가 빈 행",
            "총액 1,000(빈 행 0 기여, 크래시 없음)",
            f"총액={tot} n={r['n_items']}",
            "PASS" if ok else "FAIL",
            "" if ok else "빈 금액 처리 오류")

    return rec.flush()


if __name__ == "__main__":
    run()
