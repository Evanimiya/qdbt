# -*- coding: utf-8 -*-
"""시나리오 6: 기타.

품명 미지정, 완전중복 항목, seq 다중 세부시트, 병합셀, 헤더 위치 이상.
"""
import _util
from _util import build_wb, leaf_total, approx, Recorder
from extractors.extract_by_mapping import extract_by_mapping, suggest_column_mapping
from extractors.stitch import stitch_sheets


def run():
    rec = Recorder("S6 기타")

    # ── 6a. 품명 미지정(분류만) → 최말단 분류를 잎으로 ──
    p = build_wb("s6a.xlsx", [("Sheet", [
        ["대분류", "중분류", "금액"],
        ["재료비", "볼트류", 1000],
        ["재료비", "너트류", 2000],
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "cat1", 2: "cat2", 3: "amount"}, 1)
    names = {it["name_normalized"] for it in r["items"]}
    ok = (r["n_items"] == 2 and names == {"볼트류", "너트류"}
          and approx(leaf_total(r["items"]), 3000))
    rec.add("6a", "품명 미지정(분류만) 추출",
            "품명 열 부재 → 최말단 분류값을 잎으로(추출 0 방지)",
            "볼트류·너트류 2건, 총액3000",
            f"names={names} n={r['n_items']} tot={leaf_total(r['items'])}",
            "PASS" if ok else "FAIL",
            "" if ok else "품명 미지정 폴백 실패(추출 0)")

    # ── 6b. 완전중복 항목(시트 간 동일 분류경로+품명+규격+금액) → dedup ──
    row = ["구매자재", "볼트", "M6", 1, 1000, 1000]
    hdr = ["대분류", "품명", "규격", "수량", "단가", "금액"]
    p = build_wb("s6b.xlsx", [
        ("자재1", [hdr, row], []),
        ("자재2", [hdr, row], []),
    ])
    mp = {1: "cat1", 2: "name", 3: "spec", 4: "qty", 5: "price", 6: "amount"}
    specs = [{"sheet": "자재1", "mapping": mp, "header_row": 1},
             {"sheet": "자재2", "mapping": mp, "header_row": 1}]
    res = stitch_sheets(p, specs)
    tot = res["totals"]["leaf_sum"]
    dup = res["reconciliation"]["duplicates"]
    ok = approx(tot, 1000) and dup == 1 and res["n_items"] == 1
    rec.add("6b", "완전중복 항목(시트 간) dedup",
            "동일 분류경로+품명+규격+금액 → 1건 정본, 나머지 제외(총액 불변)",
            "총액 1,000 · duplicates 1 · 잎 1건",
            f"총액={tot} dup={dup} n={res['n_items']}",
            "PASS" if ok else "FAIL",
            "" if ok else "완전중복 dedup 실패(이중계상 or 과잉삭제)")

    # ── 6c. seq 다중 세부시트(서로 다른 번호계열) 모두 보존 ──
    p = build_wb("s6c.xlsx", [
        ("목록", [["No", "품명"], [1, "자재"], [2, "인력"]], []),
        ("세부A", [["No", "품명", "수량", "단가", "금액"], ["1.1", "볼트", 1, 100, 100]], []),
        ("세부B", [["No", "품명", "수량", "단가", "금액"], ["2.1", "설치공", 1, 200, 200]], []),
    ])
    specs = [
        {"sheet": "목록", "mapping": {1: "seq", 2: "name"}, "header_row": 1},
        {"sheet": "세부A", "mapping": {1: "seq", 2: "name", 3: "qty", 4: "price", 5: "amount"}, "header_row": 1},
        {"sheet": "세부B", "mapping": {1: "seq", 2: "name", 3: "qty", 4: "price", 5: "amount"}, "header_row": 1},
    ]
    res = stitch_sheets(p, specs)
    names = {it["name_normalized"] for it in res["items"] if not it.get("merge_status")}
    tot = res["totals"]["leaf_sum"]
    ok = {"볼트", "설치공"} <= names and approx(tot, 300)
    rec.add("6c", "seq 다중 세부시트 모두 보존",
            "세부A·세부B 서로 다른 세부시트 → 통째 소실 없이 모두 처리",
            "볼트·설치공 보존, 총액300",
            f"names={names} tot={tot}",
            "PASS" if ok else "FAIL",
            "" if ok else "다중 세부시트 일부 소실")

    # ── 6d. 병합셀(대분류 세로병합) 복원 ──
    p = build_wb("s6d.xlsx", [("Sheet", [
        ["대분류", "품명", "금액"],
        ["재료비", "볼트", 100],
        [None, "너트", 200],
        [None, "와셔", 300],
    ], ["A2:A4"])])
    r = extract_by_mapping(p, "Sheet", {1: "cat1", 2: "name", 3: "amount"}, 1)
    ok = (r["n_items"] == 3
          and all(it["path"] == "재료비" for it in r["items"])
          and approx(leaf_total(r["items"]), 600))
    rec.add("6d", "병합셀(대분류 세로병합) 복원",
            "A2:A4 병합 → 너트·와셔도 재료비 상속",
            "3건 모두 재료비, 총액600",
            f"paths={[it['path'] for it in r['items']]} tot={leaf_total(r['items'])}",
            "PASS" if ok else "FAIL",
            "" if ok else "병합 복원 실패")

    # ── 6e. 헤더 위치 이상(제목행이 위에) ──
    p = build_wb("s6e.xlsx", [("Sheet", [
        ["○○사업 견적서", None, None],
        [None, None, None],
        ["대분류", "품명", "금액"],
        ["재료비", "볼트", 100],
        ["재료비", "너트", 200],
    ], [])])
    r = extract_by_mapping(p, "Sheet", {1: "cat1", 2: "name", 3: "amount"}, 3)
    sug = suggest_column_mapping(p, "Sheet")
    ok = (r["n_items"] == 2 and approx(leaf_total(r["items"]), 300)
          and sug["header_row"] == 3)
    rec.add("6e", "헤더 위치 이상(제목행 위)",
            "제목·빈행 위 → header_row=3, 자동탐지도 3",
            "2건 총액300, suggest header_row=3",
            f"n={r['n_items']} tot={leaf_total(r['items'])} suggest_hr={sug['header_row']}",
            "PASS" if ok else "FAIL",
            "" if ok else "헤더 위치 이상 처리 실패")

    # ── 6f. seq 모드 leaf/breakdown 시트 보존(은닉 소실 금지) [Fix1 반영] ──
    #  갑지(정수 No + 금액 → role 'leaf')가 산출(1.1 계층 → seq_leaf)과 함께 와도 방출된다.
    #  독립 breakdown(갑지 8000 ≠ 산출 300)이므로 잎+residual로 보존(총액에 포함, 손실 0).
    #  요약(총액 일치)이면 전체요약 roll-up으로 제외(별도 6f-2에서 검증).
    p = build_wb("s6f.xlsx", [
        ("갑지", [["No", "품명", "금액"], [1, "자재총괄", 5000], [2, "인력총괄", 3000]], []),
        ("산출", [["No", "품명", "수량", "단가", "금액"],
                  ["1.1", "볼트", 1, 100, 100], ["1.2", "너트", 1, 200, 200]], []),
    ])
    specs = [
        {"sheet": "갑지", "mapping": {1: "seq", 2: "name", 3: "amount"}, "header_row": 1},
        {"sheet": "산출", "mapping": {1: "seq", 2: "name", 3: "qty", 4: "price", 5: "amount"}, "header_row": 1},
    ]
    res = stitch_sheets(p, specs)
    all_names = {it["name_normalized"] for it in res["items"] if not it.get("merge_status")}
    resid_names = {r.get("name") for r in res["residuals"]}
    gapji_seen = {"자재총괄", "인력총괄"} <= (all_names | resid_names)
    tot = res["totals"]["leaf_sum"]
    ok = gapji_seen and approx(tot, 8300)   # 독립 갑지 8000 보존 + 산출 300
    rec.add("6f", "seq모드 leaf 독립 breakdown 보존",
            "갑지(정수No+금액 role=leaf) + 산출(1.1 seq_leaf), 갑지는 독립(총액 불일치)",
            "갑지 잎+residual 보존, 총액 8,300(은닉 소실 0)",
            f"총액={tot} · 갑지흔적={'보존' if gapji_seen else '소실'}",
            "PASS" if ok else "FAIL",
            "" if ok else "seq모드 leaf 시트 소실")

    # ── 6f-2. seq 모드 요약 leaf 시트 → 전체요약 roll-up(이중계상 방지, Δ=0) ──
    #  갑지 총액(300)이 산출 정본 총액과 일치하면 요약으로 보고 roll-up(제외).
    p = build_wb("s6f2.xlsx", [
        ("갑지", [["No", "품명", "금액"], [1, "볼트류", 100], [2, "너트류", 200]], []),
        ("산출", [["No", "품명", "수량", "단가", "금액"],
                  ["1.1", "볼트", 1, 100, 100], ["1.2", "너트", 1, 200, 200]], []),
    ])
    specs = [
        {"sheet": "갑지", "mapping": {1: "seq", 2: "name", 3: "amount"}, "header_row": 1},
        {"sheet": "산출", "mapping": {1: "seq", 2: "name", 3: "qty", 4: "price", 5: "amount"}, "header_row": 1},
    ]
    res = stitch_sheets(p, specs)
    tot = res["totals"]["leaf_sum"]
    rolled = len(res["reconciliation"]["rollups"])
    ok = approx(tot, 300) and rolled >= 2   # 산출 300만, 갑지 roll-up
    rec.add("6f-2", "seq모드 요약 leaf → 전체요약 roll-up",
            "갑지 총액(300)=산출 정본 총액 → 요약 판별",
            "총액 300(갑지 roll-up 제외, 이중계상 없음)",
            f"총액={tot} rollups={rolled}",
            "PASS" if ok else "FAIL",
            "" if ok else "요약 leaf roll-up 실패(이중계상 or 소실)")

    return rec.flush()


if __name__ == "__main__":
    run()
