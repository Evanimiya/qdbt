# -*- coding: utf-8 -*-
"""시나리오 4: 번호체계.

점/하이픈/공백/가운뎃점/혼합, 정수 행번호(No) vs 계층, 중복 번호, 시트 간 동일 No.
"""
import _util
from _util import build_wb, leaf_total, approx, Recorder
from extractors.extract_by_mapping import extract_by_mapping, seq_tuple
from extractors.stitch import stitch_sheets


def run():
    rec = Recorder("S4 번호체계")

    # ── 4a. 구분자 무관 seq 파싱 ──
    SEQ = {
        "1.1": (1, 1), "1-1": (1, 1), "1 1": (1, 1), "1·1": (1, 1),
        "1)1": (1, 1), "1.1.2": (1, 1, 2), "1-1.2": (1, 1, 2),
        "1": (1,), "10": (10,), "No": None, "": None,
        "1-1/2인치": None, "항번": None,
    }
    bad = {k: (seq_tuple(k), v) for k, v in SEQ.items() if seq_tuple(k) != v}
    rec.add("4a", "번호 구분자 무관 파싱(점·하이픈·공백·가운뎃점·혼합)",
            f"{len(SEQ)}종 번호 표기",
            "숫자그룹 튜플화, 치수(1-1/2인치)·비번호는 None",
            f"불일치: {bad or '없음'}",
            "PASS" if not bad else "FAIL",
            "" if not bad else "seq_tuple 파싱 오류")

    # ── 4b. 정수 행번호(No) + 분류열 → seq 모드 발동 금지(BUG A 회귀 방지) ──
    p = build_wb("s4b.xlsx", [("Sheet", [
        ["No", "대분류", "중분류", "품명", "금액"],
        [1, "재료비", "기구부", "볼트", 100],
        [2, "재료비", "전장부", "PLC", 200],
        [3, "노무비", "설치", "설치공", 300],
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "seq", 2: "cat1", 3: "cat2", 4: "name", 5: "amount"}, 1)
    by = {it["name_normalized"]: it["path"] for it in r["items"]}
    ok = (r["n_items"] == 3 and by.get("볼트") == "재료비 > 기구부"
          and by.get("PLC") == "재료비 > 전장부"
          and approx(leaf_total(r["items"]), 600))
    rec.add("4b", "정수 No + 분류열 → 계층 보존(seq 모드 미발동)",
            "No가 seq로 매핑돼도 분류 계층을 버리면 안 됨(BUG A)",
            "볼트=재료비>기구부, PLC=재료비>전장부, 총액600",
            f"paths={by} tot={leaf_total(r['items'])}",
            "PASS" if ok else "FAIL",
            "" if ok else "정수 No가 seq계층으로 오인→분류 소실")

    # ── 4c. 중복 번호(같은 시트에 1.1 두 번) → 둘 다 보존 ──
    p = build_wb("s4c.xlsx", [("Sheet", [
        ["No", "품명", "금액"],
        ["1", "재료비", None],       # 대분류 헤더(금액 없음)
        ["1.1", "볼트", 100],
        ["1.1", "너트", 200],         # 중복 번호, 다른 품명
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "seq", 2: "name", 3: "amount"}, 1)
    names = [it["name_normalized"] for it in r["items"]]
    ok = (names.count("볼트") == 1 and names.count("너트") == 1
          and approx(leaf_total(r["items"]), 300)
          and all(it["path"].startswith("재료비") for it in r["items"]))
    rec.add("4c", "중복 번호(같은 1.1 두 행)",
            "중복 번호라도 다른 품명이면 둘 다 보존(손실·병합 없음)",
            "볼트·너트 각 1건, 재료비 하위, 총액300",
            f"names={names} tot={leaf_total(r['items'])}",
            "PASS" if ok else "FAIL",
            "" if ok else "중복 번호 처리 오류(항목 소실/병합)")

    # ── 4d. 시트 간 동일 No(세부시트 2개 각각 1.1) → 둘 다 보존 ──
    p = build_wb("s4d.xlsx", [
        ("목록", [["No", "품명"], [1, "자재"], [2, "인력"]], []),
        ("세부A", [["No", "품명", "수량", "단가", "금액"], ["1.1", "볼트", 1, 100, 100]], []),
        ("세부B", [["No", "품명", "수량", "단가", "금액"], ["1.1", "케이블", 1, 200, 200]], []),
    ])
    specs = [
        {"sheet": "목록", "mapping": {1: "seq", 2: "name"}, "header_row": 1},
        {"sheet": "세부A", "mapping": {1: "seq", 2: "name", 3: "qty", 4: "price", 5: "amount"}, "header_row": 1},
        {"sheet": "세부B", "mapping": {1: "seq", 2: "name", 3: "qty", 4: "price", 5: "amount"}, "header_row": 1},
    ]
    res = stitch_sheets(p, specs)
    kept = [it for it in res["items"] if not it.get("merge_status")]
    names = {it["name_normalized"] for it in kept}
    tot = res["totals"]["leaf_sum"]
    ok = ({"볼트", "케이블"} <= names and approx(tot, 300))
    rec.add("4d", "시트 간 동일 No(세부시트 2개)",
            "세부시트가 같은 번호를 써도 각각 보존(다중 seq_leaf)",
            "볼트·케이블 둘 다, 총액300",
            f"kept={len(kept)} names={names} tot={tot}",
            "PASS" if ok else "FAIL",
            "" if ok else "동일 No 세부시트 소실/병합")

    return rec.flush()


if __name__ == "__main__":
    run()
