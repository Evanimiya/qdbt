# -*- coding: utf-8 -*-
"""시나리오 3: 탭 여러 개.

갑지(요약)+상세+설명+목록/트리/세부가 각기 다른 레벨·번호체계, 겹치는 밴드,
선택 시트만 추출·미선택 혼입 방지, 요약↔상세 중복 roll-up(이중계상 방지).
"""
import _util
from _util import build_wb, leaf_total, approx, Recorder
from extractors.stitch import stitch_sheets, stitch_workbook


def run():
    rec = Recorder("S3 다중탭")

    # ── 3a. 갑지(요약) ↔ 상세 roll-up (이중계상 방지) ──
    p = build_wb("s3a.xlsx", [
        ("갑지", [["품명", "금액"],
                  ["재료비", 3000], ["노무비", 5000]], []),
        ("상세", [["대분류", "중분류", "품명", "금액"],
                  ["재료비", "기구부", "볼트", 1000],
                  ["재료비", "전장부", "PLC", 2000],
                  ["노무비", "설치", "설치공", 5000]], []),
    ])
    specs = [
        {"sheet": "갑지", "mapping": {1: "name", 2: "amount"}, "header_row": 1},
        {"sheet": "상세", "mapping": {1: "cat1", 2: "cat2", 3: "name", 4: "amount"}, "header_row": 1},
    ]
    res = stitch_sheets(p, specs)
    tot = res["totals"]["leaf_sum"]
    rolled = len(res["reconciliation"]["rollups"])
    ok = approx(tot, 8000) and rolled == 2
    rec.add("3a", "갑지(요약)↔상세 roll-up",
            "요약 3000+5000 이 상세 카테고리합과 일치 → 요약 제외",
            "총액 8,000(상세 잎만) · roll-up 2건",
            f"총액={tot} rollups={rolled} drop={res['n_dropped']}",
            "PASS" if ok else "FAIL",
            "" if ok else "요약행 이중계상(roll-up 실패)")

    # ── 3b. 요약행 부분 불일치 → 미매칭 요약 residual 보존(이중계상도 소실도 금지) ──
    p = build_wb("s3b.xlsx", [
        ("갑지", [["품명", "금액"],
                  ["재료비", 3000], ["부대비", 9999]], []),   # 부대비는 상세에 없음
        ("상세", [["대분류", "중분류", "품명", "금액"],
                  ["재료비", "기구부", "볼트", 1000],
                  ["재료비", "전장부", "PLC", 2000]], []),
    ])
    specs = [
        {"sheet": "갑지", "mapping": {1: "name", 2: "amount"}, "header_row": 1},
        {"sheet": "상세", "mapping": {1: "cat1", 2: "cat2", 3: "name", 4: "amount"}, "header_row": 1},
    ]
    res = stitch_sheets(p, specs)
    reasons = {r["reason"] for r in res["residuals"]}
    unmatched = [r for r in res["residuals"] if r["reason"] == "summary_unmatched"]
    # 재료비는 roll-up 제외(3000=상세합), 부대비는 미매칭 → residual로 보존.
    ok = ("summary_unmatched" in reasons and len(unmatched) == 1
          and any("부대비" == u.get("name") for u in unmatched))
    rec.add("3b", "요약 부분 불일치 → 미매칭 요약 residual",
            "갑지 재료비=상세합(roll-up), 부대비=상세에 없음",
            "부대비는 summary_unmatched residual로 보존(사람 확인)",
            f"reasons={reasons} unmatched={[u.get('name') for u in unmatched]}",
            "PASS" if ok else "FAIL",
            "" if ok else "미매칭 요약행 residual 미표기")

    # ── 3c. 겹치는 밴드 파노라마 조인(대>중>소 복원) ──
    p = build_wb("s3c.xlsx", [
        ("골격1", [["대분류", "중분류"], ["컴퓨팅", "GPU컴퓨팅"]], []),
        ("골격2", [["중분류", "소분류"], ["GPU컴퓨팅", "GPU서버"]], []),
        ("명세", [["소분류", "품명", "금액"], ["GPU서버", "A100", 1000]], []),
    ])
    specs = [
        {"sheet": "골격1", "mapping": {1: "cat1", 2: "cat2"}, "header_row": 1},
        {"sheet": "골격2", "mapping": {1: "cat2", 2: "cat3"}, "header_row": 1},
        {"sheet": "명세", "mapping": {1: "cat3", 2: "name", 3: "amount"}, "header_row": 1},
    ]
    res = stitch_sheets(p, specs)
    kept = [it for it in res["items"] if not it.get("merge_status")]
    it0 = kept[0] if kept else {}
    ok = (len(kept) == 1 and it0.get("path") == "컴퓨팅 > GPU컴퓨팅 > GPU서버"
          and it0.get("name_normalized") == "A100"
          and approx(res["totals"]["leaf_sum"], 1000))
    rec.add("3c", "겹치는 밴드 파노라마 조인",
            "골격1[대>중]·골격2[중>소]·명세[소>품명] → 대>중>소 복원",
            "path=컴퓨팅>GPU컴퓨팅>GPU서버, name=A100, 총액1000",
            f"kept={len(kept)} path={it0.get('path')} tot={res['totals']['leaf_sum']}",
            "PASS" if ok else "FAIL",
            "" if ok else "밴드 파노라마 조인 실패")

    # ── 3e. 선택 시트만 추출 · 미선택/설명 시트 혼입 방지 ──
    p = build_wb("s3e.xlsx", [
        ("갑지요약", [["품명", "금액"], ["총괄", 99999]], []),
        ("상세", [["대분류", "품명", "수량", "단가", "금액"],
                  ["재료비", "볼트", 10, 100, 1000]], []),
        ("_메모", [["설명"], ["이 시트는 내부 메모"]], []),
        ("설명", [["안내"], ["본 견적은 VAT 별도입니다"]], []),
    ])
    # only_sheets 로 '상세'만 선택 → 갑지요약·설명 금액 혼입 금지
    res = stitch_workbook(p, only_sheets=["상세"])
    names = {it["name_normalized"] for it in res["items"] if not it.get("merge_status")}
    tot = res["totals"]["leaf_sum"]
    ok = names == {"볼트"} and approx(tot, 1000)
    rec.add("3e", "선택 시트만 추출(미선택·설명 혼입 방지)",
            "상세만 선택 → 갑지요약(99999)·설명 시트 혼입 금지",
            "잎={볼트}, 총액 1,000",
            f"names={names} tot={tot}",
            "PASS" if ok else "FAIL",
            "" if ok else "미선택/설명 시트 혼입")

    # ── 3f. '_' 접두 시트 자동 제외 ──
    res_all = stitch_workbook(p)   # only_sheets 없음 → '_메모' 제외, 나머지 포함
    sheets_used = {s["name"] for s in res_all["sheets"]}
    ok = "_메모" not in sheets_used
    rec.add("3f", "'_' 접두 시트 자동 제외",
            "_메모 시트는 스티칭 대상에서 제외",
            "대상 시트에 _메모 없음",
            f"used={sheets_used}",
            "PASS" if ok else "FAIL",
            "" if ok else "_ 접두 시트 미제외")

    return rec.flush()


if __name__ == "__main__":
    run()
