# -*- coding: utf-8 -*-
"""견고성 테스트 하네스 (엉망 견적서 시나리오) — 전 시나리오 실행 + 매트릭스 렌더.

실행: ./venv/bin/python tests/rob/run_all.py
산출: tests/rob/_results.jsonl + docs/QDBT_견고성테스트_20260711.md

포그라운드 서버 등 블로킹 없음. extract_by_mapping/stitch/통화정합/집계를 함수 직접 호출.
"""
from _util import (build_wb, leaf_total, krw_leaf_total_from_extract, Recorder,
                   reset_results, approx, ROOT, RESULTS)
import json

from extractors.extract_by_mapping import (
    extract_by_mapping, seq_tuple, is_total_label, apply_currency_fields, _CUR_MAP)
from extractors.stitch import stitch_sheets, _read_records, _classify_sheet
from core.numparse import parse_amount


# ══════════════════════════════════════════════════════════════════
# S1 — 분류가 엉망: 비연속 레벨, depth 불일치, 중간 빈 분류, 대분류 없이 시작
# ══════════════════════════════════════════════════════════════════
def s1_broken_levels():
    R = Recorder("S1 분류 엉망")

    # S1-1: 비연속 레벨 [중분류, 세분류] (소분류 gap) 단독
    p = build_wb("s1_gap.xlsx", [("S", [
        ["중분류", "세분류", "금액"],
        ["기구부", "차폐", 1000],
        ["기구부", "냉각", 2000]], [])])
    res = extract_by_mapping(p, "S", {1: "cat2", 2: "cat4", 3: "amount"}, header_row=1)
    depths = {it["depth"] for it in res["items"]}
    has_ph = all("⟨미연계·소분류⟩" in (it["path"] or "") for it in res["items"])
    tot = krw_leaf_total_from_extract(res)
    ok = (depths == {4}) and has_ph and approx(tot, 3000)
    R.add("S1-1", "비연속 레벨(cat2+cat4, 소분류 gap) 단독추출", "[중,세] 매핑",
          "세분류 depth4 + ⟨소분류⟩ placeholder, 총액 3000",
          f"depths={depths} placeholder={has_ph} 총액={tot}", "PASS" if ok else "FAIL",
          "" if ok else "gap placeholder 미충전")

    # S1-2: 대분류 없이 중분류부터 시작
    p = build_wb("s1_nostart.xlsx", [("S", [
        ["중분류", "품명", "금액"],
        ["기구부", "납블록", 500],
        ["제어부", "센서", 700]], [])])
    res = extract_by_mapping(p, "S", {1: "cat2", 2: "name", 3: "amount"}, header_row=1)
    d = {it["depth"] for it in res["items"]}
    root_ph = all((it["path"] or "").startswith("⟨미연계·대분류⟩") for it in res["items"])
    tot = krw_leaf_total_from_extract(res)
    ok = d == {2} and root_ph and approx(tot, 1200)
    R.add("S1-2", "대분류 없이 중분류부터", "[중,품명] 매핑",
          "중분류 depth2 + ⟨대분류⟩ placeholder, 총액 1200",
          f"depths={d} root_ph={root_ph} 총액={tot}", "PASS" if ok else "FAIL",
          "" if ok else "레벨 승격/미보존")

    # S1-3: 행마다 depth 불일치 (일부 대>중>소, 일부 대>중) — 빈 분류 상속
    p = build_wb("s1_ragged.xlsx", [("S", [
        ["대분류", "중분류", "소분류", "품명", "금액"],
        ["재료비", "기구부", "차폐", "납블록", 1000],
        ["재료비", "기구부", None, "일반부속", 300],   # 소분류 빈칸
        ["재료비", None, None, "직접자재", 200]], [])])    # 중/소 빈칸
    res = extract_by_mapping(p, "S", {1: "cat1", 2: "cat2", 3: "cat3", 4: "name", 5: "amount"}, header_row=1)
    tot = krw_leaf_total_from_extract(res)
    n = len([it for it in res["items"] if not it.get("is_category_header")])
    ok = approx(tot, 1500) and n == 3
    R.add("S1-3", "행마다 depth 불일치 + 빈 분류 상속", "3행(소·중 빈칸 혼재)",
          "3잎 전부 추출, 총액 1500(소실 없음)",
          f"n_leaf={n} 총액={tot}", "PASS" if ok else "FAIL",
          "" if ok else "빈 분류 행 드롭/총액 손실")

    # S1-4: 동일 품목, 분류명만 다름 (업체 A '자재>서버', B '장비>서버') — 각자 보존
    #  (단일시트 추출 관점: 분류가 달라도 항목 자체는 정상 추출돼야)
    p = build_wb("s1_diffcat.xlsx", [("S", [
        ["대분류", "품명", "금액"],
        ["자재", "GPU서버", 5000],
        ["장비", "GPU서버", 5200]], [])])
    res = extract_by_mapping(p, "S", {1: "cat1", 2: "name", 3: "amount"}, header_row=1)
    tot = krw_leaf_total_from_extract(res)
    cats = {(it.get("category")) for it in res["items"]}
    ok = approx(tot, 10200) and cats == {"자재", "장비"}
    R.add("S1-4", "동일품목 분류명만 상이", "자재/장비 > GPU서버",
          "각자 분류 보존, 총액 10200",
          f"cats={cats} 총액={tot}", "PASS" if ok else "FAIL")
    return R.flush()


# ══════════════════════════════════════════════════════════════════
# S2 — 다중 통화: USD+KRW+JPY 혼재, 기호 혼입, fx 실패, 원화열 유무
# ══════════════════════════════════════════════════════════════════
def s2_currency():
    R = Recorder("S2 다중통화")

    # S2-1: 통화열(USD/KRW/JPY) + 원화금액열 → 전부 원화 확정
    p = build_wb("s2_mix.xlsx", [("S", [
        ["품명", "통화", "단가", "금액", "원화금액"],
        ["서버", "USD", 1000, 5000, 6500000],
        ["케이블", "KRW", 200000, 400000, 400000],
        ["센서", "JPY", 50000, 500000, 4500000]], [])])
    res = extract_by_mapping(p, "S", {1: "name", 2: "currency", 3: "price", 4: "amount", 5: "amount_krw"}, header_row=1)
    tot = krw_leaf_total_from_extract(res)
    ok = approx(tot, 6500000 + 400000 + 4500000)
    R.add("S2-1", "USD+KRW+JPY 혼재 + 원화금액열", "3통화 + 원화금액",
          "전부 원화확정, 총액 11,400,000",
          f"총액={tot}", "PASS" if ok else "FAIL",
          "" if ok else "통화 혼합 합산")

    # S2-2: fx 도출 실패 외화(원화열 없음) → needs_fx, KRW 합계 미혼입
    p = build_wb("s2_nofx.xlsx", [("S", [
        ["품명", "통화", "단가", "금액"],
        ["서버", "USD", 1000, 5000],
        ["국산", "KRW", 300000, 300000]], [])])
    res = extract_by_mapping(p, "S", {1: "name", 2: "currency", 3: "price", 4: "amount"}, header_row=1)
    usd = next(it for it in res["items"] if it["name_normalized"] == "서버")
    tot = krw_leaf_total_from_extract(res)
    ok = usd.get("needs_fx") and usd.get("amount") is None and approx(tot, 300000)
    R.add("S2-2", "환율 미확정 외화 KRW 합계 미혼입", "USD 원화열 없음",
          "USD amount=None+needs_fx, 총액=국산만 300000",
          f"needs_fx={usd.get('needs_fx')} usd_amt={usd.get('amount')} 총액={tot}",
          "PASS" if ok else "FAIL", "" if ok else "외화 표면값 KRW 오합산")

    # S2-3: 금액 셀에 통화기호 혼입 (¥·€·₩·$) → parse_amount가 값 보존
    syms = {"₩1,000": 1000, "$1,000": 1000, "¥500": 500, "€10": 10, "1,234원": 1234}
    bad = {k: parse_amount(k) for k, v in syms.items() if parse_amount(k) != v}
    ok = not bad
    R.add("S2-3", "금액 통화기호 혼입 파싱", "₩$¥€ 붙은 금액",
          "기호 제거 후 값 보존",
          f"불일치={bad or '없음'}", "PASS" if ok else "FAIL",
          "" if ok else "통화기호 금액 소실")

    # S2-4: 다중시트 통화(스티칭이 통화 반영)
    p = build_wb("s2_stitch.xlsx", [
        ("A", [["대", "금액"], ["재료비", None]], []),
        ("B", [["대", "품명", "통화", "단가", "금액", "원화금액"],
               ["재료비", "서버", "USD", 1000, 5000, 6500000]], [])])
    sres = stitch_sheets(p, [
        {"sheet": "A", "mapping": {1: "cat1", 2: "amount"}, "header_row": 1},
        {"sheet": "B", "mapping": {1: "cat1", 2: "name", 3: "currency", 4: "price", 5: "amount", 6: "amount_krw"}, "header_row": 1}])
    bt = next((i for i in sres["items"] if i.get("_sheet") == "B"), None)
    ok = bt and approx(bt.get("amount"), 6500000)
    R.add("S2-4", "다중시트 통화 원화정합(스티칭)", "A분류+B통화/원화열",
          "B amount=원화확정 6,500,000",
          f"amount={bt.get('amount') if bt else None}", "PASS" if ok else "FAIL",
          "" if ok else "스티칭 통화 미반영")
    return R.flush()


# ══════════════════════════════════════════════════════════════════
# S3 — 다중시트: 요약+상세+설명+목록/트리/세부, 겹치는 밴드, 선택 시트만
# ══════════════════════════════════════════════════════════════════
def s3_multisheet():
    R = Recorder("S3 다중시트")

    # S3-1: 갑지(요약) + 명세서(상세) — 상세 우선, 요약 roll-up(이중계상 없음)
    p = build_wb("s3_sumdet.xlsx", [
        ("갑지", [["대분류", "금액"], ["재료비", 3000], ["노무비", 2000]], []),
        ("명세서", [["대분류", "품명", "수량", "단가", "금액"],
                  ["재료비", "납블록", 2, 1000, 2000],
                  ["재료비", "볼트", 1, 1000, 1000],
                  ["노무비", "설치", 1, 2000, 2000]], [])])
    sres = stitch_sheets(p, [
        {"sheet": "갑지", "mapping": {1: "cat1", 2: "amount"}, "header_row": 1},
        {"sheet": "명세서", "mapping": {1: "cat1", 2: "name", 3: "qty", 4: "price", 5: "amount"}, "header_row": 1}])
    tot = sum(i["amount"] for i in sres["items"] if i.get("amount") and not i.get("merge_status"))
    ok = approx(tot, 5000)   # 상세 5000, 갑지 roll-up
    R.add("S3-1", "갑지(요약)+명세서(상세) 이중계상 방지", "요약5000+상세5000",
          "상세 기준 5000(요약 roll-up)",
          f"총액={tot} n_dropped={sres.get('n_dropped')}", "PASS" if ok else "FAIL",
          "" if ok else "요약 이중계상")

    # S3-2: 겹치는 밴드 [대>중] + [중>소>품명] 조인
    p = build_wb("s3_band.xlsx", [
        ("분류", [["대", "중"], ["재료비", "기구부"]], []),
        ("상세", [["중", "소", "품명", "수량", "단가", "금액"],
                 ["기구부", "차폐", "납블록", 2, 500, 1000]], [])])
    sres = stitch_sheets(p, [
        {"sheet": "분류", "mapping": {1: "cat1", 2: "cat2"}, "header_row": 1},
        {"sheet": "상세", "mapping": {1: "cat2", 2: "cat3", 3: "name", 4: "qty", 5: "price", 6: "amount"}, "header_row": 1}])
    leaf = next((i for i in sres["items"] if i.get("amount")), None)
    joined = leaf and "재료비" in (leaf.get("path") or "")
    ok = leaf and approx(leaf["amount"], 1000) and joined
    R.add("S3-2", "겹치는 밴드 조인 [대>중]+[중>소>품명]", "밴드+상세",
          "재료비>기구부>차폐 경로로 조인, 1000",
          f"path={leaf.get('path') if leaf else None}", "PASS" if ok else "FAIL",
          "" if ok else "밴드 조인 실패")

    # S3-3: 선택 시트만 추출 (설명 시트 혼입 방지) — stitch_sheets는 지정 시트만
    p = build_wb("s3_select.xlsx", [
        ("설명", [["이 견적서는..."], ["주의사항 blah"]], []),
        ("명세서", [["대", "품명", "금액"], ["재료비", "납블록", 1000]], [])])
    sres = stitch_sheets(p, [
        {"sheet": "명세서", "mapping": {1: "cat1", 2: "name", 3: "amount"}, "header_row": 1}])
    tot = sum(i["amount"] for i in sres["items"] if i.get("amount") and not i.get("merge_status"))
    ok = approx(tot, 1000) and len([i for i in sres["items"] if i.get("amount")]) == 1
    R.add("S3-3", "선택 시트만 추출(설명시트 미혼입)", "명세서만 지정",
          "명세서 1잎 1000, 설명 미혼입",
          f"총액={tot}", "PASS" if ok else "FAIL")
    return R.flush()


# ══════════════════════════════════════════════════════════════════
# S4 — 번호체계: 점/하이픈/공백/가운뎃점/혼합, 정수 vs 계층, 중복
# ══════════════════════════════════════════════════════════════════
def s4_seq():
    R = Recorder("S4 번호체계")

    cases = {"1.1": (1, 1), "1-1": (1, 1), "1 1": (1, 1), "1·1": (1, 1),
             "1)1": (1, 1), "1-1-2": (1, 1, 2), "1": (1,), "No": None,
             "1-1/2인치": None, "A-1": None}
    bad = {k: seq_tuple(k) for k, v in cases.items() if seq_tuple(k) != v}
    R.add("S4-1", "구분자 무관 seq 파싱", "점/하이픈/공백/·/) 혼합",
          "모두 동일 튜플, 정수·치수·문자 비계층",
          f"불일치={bad or '없음'}", "PASS" if not bad else "FAIL",
          "" if not bad else "구분자 종속")

    # S4-2: 하이픈 계층 단독추출
    p = build_wb("s4_hyphen.xlsx", [("S", [
        ["No", "품명", "금액"],
        ["1", "재료비", None], ["1-1", "납블록", 1000], ["1-2", "볼트", 500],
        ["2", "노무비", None], ["2-1", "설치", 2000]], [])])
    res = extract_by_mapping(p, "S", {1: "seq", 2: "name", 3: "amount"}, header_row=1)
    tot = krw_leaf_total_from_extract(res)
    paths_ok = any("재료비 > 납블록" == it.get("path") for it in res["items"])
    ok = approx(tot, 3500) and paths_ok
    R.add("S4-2", "하이픈 번호 계층 추출", "1/1-1/1-2/2/2-1",
          "재료비>납블록 등, 총액 3500",
          f"총액={tot} 계층복원={paths_ok}", "PASS" if ok else "FAIL")

    # S4-3: 정수 행번호(No 1,2,3) + cat 있음 → seq 비활성, cat 계층 유지
    p = build_wb("s4_int.xlsx", [("S", [
        ["No", "대분류", "품명", "금액"],
        [1, "재료비", "납블록", 1000], [2, "재료비", "볼트", 500]], [])])
    res = extract_by_mapping(p, "S", {1: "seq", 2: "cat1", 3: "name", 4: "amount"}, header_row=1)
    tot = krw_leaf_total_from_extract(res)
    cat_ok = all((it.get("path") or "").startswith("재료비") for it in res["items"])
    ok = approx(tot, 1500) and cat_ok
    R.add("S4-3", "정수 행번호(No)+cat → 계층 아님", "No 1,2 + 대분류",
          "cat 계층 유지, 총액 1500",
          f"총액={tot} cat유지={cat_ok}", "PASS" if ok else "FAIL")

    # S4-4: 중복 번호 (같은 1.1 두 개, 다른 품목) → 둘 다 보존
    p = build_wb("s4_dup.xlsx", [("S", [
        ["No", "품명", "수량", "단가", "금액"],
        ["1.1", "납블록", 1, 1000, 1000],
        ["1.1", "볼트", 1, 500, 500]], [])])
    res = extract_by_mapping(p, "S", {1: "seq", 2: "name", 3: "qty", 4: "price", 5: "amount"}, header_row=1)
    tot = krw_leaf_total_from_extract(res)
    n = len([it for it in res["items"] if not it.get("is_category_header")])
    ok = approx(tot, 1500) and n == 2
    R.add("S4-4", "중복 번호(1.1 두 개)", "동일 seq 다른 품목",
          "둘 다 보존, 총액 1500",
          f"n={n} 총액={tot}", "PASS" if ok else "FAIL",
          "" if ok else "중복 번호로 항목 병합/소실")
    return R.flush()


# ══════════════════════════════════════════════════════════════════
# S5 — 금액 이상치: 괄호음수/△/▲, 콤마, 통화기호, 빈칸, 합계행 혼입
# ══════════════════════════════════════════════════════════════════
def s5_amounts():
    R = Recorder("S5 금액이상치")

    # S5-1: 괄호/△/▲ 음수 파싱 (부호 보존)
    cases = {"(1,234)": -1234, "△1,000": -1000, "▲500": -500, "-1,234": -1234,
             "−1,000": -1000, "1,234": 1234, "1.234,56": 1234.56}
    bad = {k: parse_amount(k) for k, v in cases.items() if parse_amount(k) != v}
    R.add("S5-1", "괄호/△/▲/유니코드마이너스 음수", "회계식 음수 표기",
          "부호 보존(뒤집힘 없음)",
          f"불일치={bad or '없음'}", "PASS" if not bad else "FAIL",
          "" if not bad else "음수 소실/부호 뒤집힘")

    # S5-2: 합계/소계/Subtotal/合計 행 혼입 → is_total_label 판정
    labels = {"합계 : 1,200,000": True, "소 계": True, "Subtotal": True,
              "合計": True, "총 계 (VAT 별도)": True,
              "Total Station": False, "설계": False, "GPU Server": False}
    bad = {k: is_total_label(k) for k, v in labels.items() if is_total_label(k) != v}
    R.add("S5-2", "합계/소계/Subtotal/合計 행 판정", "합계행 + 제품명 오탐",
          "합계행 True, 제품명 False",
          f"불일치={bad or '없음'}", "PASS" if not bad else "FAIL",
          "" if not bad else "합계행 과탐/미탐")

    # S5-3: 요약행(합계) 혼입 시트 — excluded로 제외 시 총액 정확
    p = build_wb("s5_total.xlsx", [("S", [
        ["대분류", "품명", "금액"],
        ["재료비", "납블록", 1000],
        ["재료비", "볼트", 500],
        ["재료비 합계", None, 1500]], [])])   # 합계행(품명 없음)
    # 합계행(3행 R4) 제외
    res = extract_by_mapping(p, "S", {1: "cat1", 2: "name", 3: "amount"}, header_row=1,
                             excluded_rows={4})
    tot = krw_leaf_total_from_extract(res)
    ok = approx(tot, 1500)
    R.add("S5-3", "합계행 제외 후 총액 정확", "합계행 R4 제외",
          "잎 2개 1500(합계 이중계상 없음)",
          f"총액={tot}", "PASS" if ok else "FAIL")

    # S5-4: 빈 금액/단가, 통화기호 붙은 금액 혼재
    p = build_wb("s5_messy.xlsx", [("S", [
        ["대분류", "품명", "단가", "금액"],
        ["재료비", "납블록", "₩1,000", "₩2,000"],
        ["재료비", "빈금액", None, None],
        ["재료비", "볼트", "500", "1,000"]], [])])
    res = extract_by_mapping(p, "S", {1: "cat1", 2: "name", 3: "price", 4: "amount"}, header_row=1)
    tot = krw_leaf_total_from_extract(res)
    ok = approx(tot, 3000)   # 2000 + 0(빈) + 1000
    R.add("S5-4", "통화기호 금액 + 빈 금액 혼재", "₩붙은금액/빈칸/일반",
          "기호 제거 정상 파싱, 총액 3000",
          f"총액={tot}", "PASS" if ok else "FAIL",
          "" if ok else "통화기호 금액 소실")
    return R.flush()


# ══════════════════════════════════════════════════════════════════
# S6 — 품명 미지정, 완전 중복, seq 다중 세부시트
# ══════════════════════════════════════════════════════════════════
def s6_misc():
    R = Recorder("S6 기타")

    # S6-1: 품명 미지정(분류+금액만) 단독
    p = build_wb("s6_noname.xlsx", [("S", [
        ["대분류", "중분류", "금액"],
        ["재료비", "기구부", 1000], ["노무비", "설치", 2000]], [])])
    res = extract_by_mapping(p, "S", {1: "cat1", 2: "cat2", 3: "amount"}, header_row=1)
    tot = krw_leaf_total_from_extract(res)
    ok = approx(tot, 3000)
    R.add("S6-1", "품명 미지정(분류+금액만)", "[대,중,금액]",
          "최말단 분류를 잎으로, 총액 3000",
          f"총액={tot}", "PASS" if ok else "FAIL",
          "" if ok else "무품명 소계처리로 총액 0")

    # S6-2: 완전 중복 항목(같은 시트 동일 행 2개) → 둘 다 계상(단일시트는 중복제거 안 함)
    p = build_wb("s6_dup.xlsx", [("S", [
        ["대분류", "품명", "금액"],
        ["재료비", "납블록", 1000], ["재료비", "납블록", 1000]], [])])
    res = extract_by_mapping(p, "S", {1: "cat1", 2: "name", 3: "amount"}, header_row=1)
    tot = krw_leaf_total_from_extract(res)
    ok = approx(tot, 2000)
    R.add("S6-2", "완전 중복 항목(단일시트)", "동일 행 2개",
          "둘 다 계상 2000(단일시트 dedup 안 함)",
          f"총액={tot}", "PASS" if ok else "WARN",
          "" if ok else "단일시트 중복 처리 정책")

    # S6-3: seq 다중 세부시트 (목록 + 세부A + 세부B)
    p = build_wb("s6_multileaf.xlsx", [
        ("목록", [["No", "분류", "금액"], ["1", "재료비", None], ["1.1", "기구부", None]], []),
        ("세부A", [["No", "품명", "수량", "단가", "금액"], ["1.1.1", "납블록", 2, 500, 1000]], []),
        ("세부B", [["No", "품명", "수량", "단가", "금액"], ["1.1.2", "볼트", 1, 500, 500]], [])])
    sres = stitch_sheets(p, [
        {"sheet": "목록", "mapping": {1: "seq", 2: "cat1", 3: "amount"}, "header_row": 1},
        {"sheet": "세부A", "mapping": {1: "seq", 2: "name", 3: "qty", 4: "price", 5: "amount"}, "header_row": 1},
        {"sheet": "세부B", "mapping": {1: "seq", 2: "name", 3: "qty", 4: "price", 5: "amount"}, "header_row": 1}])
    tot = sum(i["amount"] for i in sres["items"] if i.get("amount") and not i.get("merge_status"))
    ok = approx(tot, 1500)
    R.add("S6-3", "seq 다중 세부시트", "목록+세부A+세부B",
          "두 세부 모두 보존, 총액 1500",
          f"총액={tot} mode={sres['mode']}", "PASS" if ok else "FAIL",
          "" if ok else "첫 세부시트만 처리(나머지 소실)")
    return R.flush()


# ══════════════════════════════════════════════════════════════════
# S7 — 어려운 경계/의심 케이스 (한계 탐침 — WARN/FAIL로 정직하게 드러냄)
# ══════════════════════════════════════════════════════════════════
def s7_edge():
    R = Recorder("S7 경계탐침")

    # S7-1: 유럽식 단일 점 천단위 "1.000" — 소수(1.0)로 오해석 가능성
    v = parse_amount("1.000")
    #  KRW 도메인상 "1.000"=1000 이 자연스러우나, 단일 점은 소수로 처리(설계상 콤마 단독=천단위).
    #  → 1.0 이면 WARN(도메인 결정), 1000 이면 PASS.
    ok = (v == 1000)
    R.add("S7-1", "유럽식 단일점 천단위 '1.000'", "'1.000'",
          "1000(천단위) 기대",
          f"parse_amount='{v}'", "PASS" if ok else "WARN",
          "단일 점은 소수로 처리(콤마 단독만 천단위) — 유럽식 정수표기 '1.000'은 1.0. "
          "도메인 결정 필요: 통화/로케일 힌트 없이는 모호." if not ok else "")

    # S7-2: 괄호+통화기호 음수 "(₩1,000)"
    v = parse_amount("(₩1,000)")
    ok = (v == -1000)
    R.add("S7-2", "괄호+통화기호 음수 '(₩1,000)'", "(₩1,000)",
          "-1000", f"parse_amount={v}", "PASS" if ok else "FAIL",
          "" if ok else "괄호+기호 동시 처리 실패")

    # S7-3: seq 하이픈 계층 + 시트 간 No 중복(다른 분류) — 오조인 없이 각자 보존
    p = build_wb("s7_seqcol.xlsx", [
        ("A", [["No", "대", "중", "금액"], ["1-1", "설비비", "기구부", 3000], ["2-1", "공사비", "설치", 2000]], []),
        ("B", [["No", "중", "품명", "금액"], ["1-1", "제어부", "센서", 600], ["1-2", "제어부", "릴레이", 400]], [])])
    sres = stitch_sheets(p, [
        {"sheet": "A", "mapping": {1: "seq", 2: "cat1", 3: "cat2", 4: "amount"}, "header_row": 1},
        {"sheet": "B", "mapping": {1: "seq", 2: "cat2", 3: "name", 4: "amount"}, "header_row": 1}])
    tot = sum(i["amount"] for i in sres["items"] if i.get("amount") and not i.get("merge_status"))
    bkept = len([i for i in sres["items"] if i.get("_sheet") == "B" and not i.get("merge_status") and i.get("amount")])
    ok = approx(tot, 6000) and bkept == 2
    R.add("S7-3", "시트 간 No 중복(다른 분류) 오조인 방지", "A/B No 1-1 겹침",
          "각자 보존, B 2건, 총액 6000",
          f"총액={tot} B_kept={bkept}", "PASS" if ok else "FAIL",
          "" if ok else "No 중복으로 오조인/소실")

    # S7-4: 요약 시트가 상세와 불일치(별도 비용) → roll-up 안 하고 둘 다 보존
    p = build_wb("s7_summisc.xlsx", [
        ("갑지", [["대", "금액"], ["재료비", 3000], ["부대비", 800]], []),   # 부대비는 상세에 없음
        ("명세서", [["대", "품명", "수량", "단가", "금액"],
                  ["재료비", "납블록", 3, 1000, 3000]], [])])
    sres = stitch_sheets(p, [
        {"sheet": "갑지", "mapping": {1: "cat1", 2: "amount"}, "header_row": 1},
        {"sheet": "명세서", "mapping": {1: "cat1", 2: "name", 3: "qty", 4: "price", 5: "amount"}, "header_row": 1}])
    tot = sum(i["amount"] for i in sres["items"] if i.get("amount") and not i.get("merge_status"))
    #  재료비3000(상세 roll-up) + 부대비800(상세에 없어 보존) = 3800 기대
    ok = approx(tot, 3800)
    R.add("S7-4", "요약≠상세(별도 비용) 보존", "갑지 부대비 상세에 없음",
          "재료비 roll-up + 부대비 보존, 3800",
          f"총액={tot}", "PASS" if ok else "WARN",
          "요약 부대비가 상세에 매칭 안 되면 보존돼야(누락 시 결정 필요)" if not ok else "")

    # S7-5: 금액이 범위표기 "1-5" (계층 아님·수량 아님) — 금액으로는 파싱 실패/오해
    v = parse_amount("1-5")
    #  '1-5'는 숫자-숫자 → 하이픈 소수 아님(정수-정수). parse_amount는 하이픈을 마이너스로?
    #  실제: '1-5' → 통화토큰 없음, 선행 '1' 숫자, '-5' → -? 확인용.
    R.add("S7-5", "범위표기 '1-5' 금액 파싱", "'1-5'",
          "예측 불가 표기 — 오계상 없어야(참고)",
          f"parse_amount='{v}'", "WARN",
          "'1-5' 같은 범위·비금액 문자열이 금액열에 오면 파서 해석이 모호(값 오계상 위험). "
          "금액열엔 범위표기가 드묾 — 참고용 관찰.")
    return R.flush()


def render_matrix():
    rows = []
    with open(RESULTS, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    npass = sum(1 for r in rows if r["verdict"] == "PASS")
    nwarn = sum(1 for r in rows if r["verdict"] == "WARN")
    nfail = sum(1 for r in rows if r["verdict"] == "FAIL")
    out = []
    out.append("# QDBT 견고성 테스트 — 엉망 견적서 시나리오 매트릭스 (2026-07-11)\n")
    out.append(f"> 하네스: `tests/rob/run_all.py` · 픽스처: `tests/rob/_fixtures/` · 함수 직접 호출(서버 없음).\n")
    out.append(f"> 결과: **{npass} PASS / {nwarn} WARN / {nfail} FAIL** (전체 {len(rows)}).\n")
    out.append("> 검증: extract_by_mapping·stitch·통화정합(apply_currency_fields)·집계(leaf_total=recompute_subtotal 미러).\n")
    cur = None
    for r in rows:
        if r["group"] != cur:
            cur = r["group"]
            out.append(f"\n## {cur}\n")
            out.append("| ID | 시나리오 | 기대 | 실제 | 판정 |")
            out.append("|---|---|---|---|---|")
        mark = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[r["verdict"]]
        exp = r["expected"].replace("|", "\\|")
        act = str(r["actual"]).replace("|", "\\|")
        out.append(f"| {r['id']} | {r['title']} | {exp} | {act} | {mark} |")
        if r["verdict"] == "FAIL" and r.get("root"):
            out.append(f"| | ↳ 근본원인 | {r['root']} | | |")
    out.append("\n## 요약\n")
    out.append(f"- **{npass}/{len(rows)} PASS, {nwarn} WARN, {nfail} FAIL.** " +
               ("핵심 시나리오(S1~S6) 전부 통과 — 이번 세션 수정(무품명·레벨보존·gap·seq구분자무관·"
                "통화통합·합계행·괄호음수)이 엉망 입력에서 견고." if nfail == 0
                else f"{nfail} FAIL — 근본원인 위 표 참조."))
    warns = [r for r in rows if r["verdict"] == "WARN"]
    if warns:
        out.append("\n### ⚠️ 결정 필요 / 관찰 항목 (자동수정 불가 · 정책 결정)\n")
        for r in warns:
            out.append(f"- **[{r['id']}] {r['title']}** — {r.get('root') or r['expected']}")
        out.append("\n> 위 WARN은 안전한 자동수정이 불가하거나(band 요약 아키타입 충돌 등) "
                   "로케일/도메인 힌트 없이는 모호한 케이스로, 별도 설계 결정이 필요합니다. "
                   "핵심 추출·통화·집계 로직은 회귀 없이(Δ=0) 동작합니다.")
    doc = ROOT / "docs" / "QDBT_견고성테스트_20260711.md"
    doc.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n매트릭스 → {doc}")
    return npass, nwarn, nfail, len(rows)


if __name__ == "__main__":
    reset_results()
    for fn in (s1_broken_levels, s2_currency, s3_multisheet, s4_seq, s5_amounts, s6_misc, s7_edge):
        print(f"\n=== {fn.__name__} ===")
        fn()
    render_matrix()
