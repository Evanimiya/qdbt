# -*- coding: utf-8 -*-
"""T7 다중시트 스티칭 엔진 (코어).

여러 시트로 흩어진 견적서를 하나의 잎-보존 데이터셋으로 조립한다.
세 아키타입을 하나의 파이프라인으로 흡수:
  · PASSTHROUGH : 한 시트에 전 레벨(대/중/소/세/품목) — 그대로 잎 경로 생성.
  · BAND        : 겹치는 밴드([대/중/소]·[중/소/세]·[세/품목]) — 공유 레벨 값으로 파노라마 조인.
  · SEQ         : 목록/트리/세부(번호계층 1.1.1) — seq→이름 해석으로 명명 경로 복원.

정책(합의): 코드가 1차 자동 조인 → (LLM 검증 훅) → 사람 최종 판별(residuals).
LLM/사람 단계는 훅만 두고, 코드 자동 조인과 미해결(residual) 산출까지 담당.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from openpyxl import load_workbook
from extractors.extract_by_mapping import (
    suggest_column_mapping, extract_by_mapping, _build_merge_fill, _to_number,
    is_total_label, seq_tuple, apply_currency_fields, CAT_ROLES, PATH_SEP,
)


def _apply_currency(item, rec):
    """[코드리뷰 H10] rec의 통화·원화 열을 item에 실어 원화 정합(extract와 동일 규칙).
    통화 미매핑(KRW) 시엔 amount/unit_price 무변경 → 회귀 없음."""
    item["currency_raw"] = rec.get("currency")
    item["amount_krw"] = rec.get("amount_krw")
    item["unit_price_krw"] = rec.get("price_krw")
    return apply_currency_fields(item)
import re

TOTAL_KW = ("합계", "소계", "총계", "total", "subtotal", "grand", "계")

# [레벨 정렬] 짧은 시트(중/소분류 시작)의 미매칭 항목을 대분류로 승격시키지 않도록,
#  누락된 상위 레벨 자리에 끼우는 placeholder 이름(의미 레벨 보존 + 미연계 표시).
#  드래그로 올바른 상위에 붙이면 이 placeholder는 벗겨진다.
_LEVEL_PLACEHOLDER = {1: "⟨미연계·대분류⟩", 2: "⟨미연계·중분류⟩",
                      3: "⟨미연계·소분류⟩", 4: "⟨미연계·세분류⟩"}
_LEVEL_PLACEHOLDER_DEFAULT = "⟨미연계⟩"


def _cat_level(role):
    return int(role[3:])  # 'cat3' -> 3


def _cat_name_match(c, key):
    """[코드리뷰 M20] 요약행명↔상세 카테고리 매칭. 정확일치 또는 '2자 이상' 포함만.
    단일 문자(예 '관')의 부분일치 오탐 방지."""
    if not c or not key:
        return False
    if c == key:
        return True
    short, long = (c, key) if len(c) <= len(key) else (key, c)
    return len(short) >= 2 and short in long


def _norm(s):
    """비교용 정규화: 공백 제거 + 소문자."""
    return re.sub(r"\s+", "", str(s if s is not None else "")).lower()


def _path_parts(p):
    """분류 경로 문자열 → 세그먼트 리스트(빈 값 제외)."""
    return [x for x in (p or "").split(PATH_SEP) if x and x.strip()]


def _dedup_and_rollup(items, tol_ratio=0.005):
    """[중복 병합] 견적서(상위 요약)와 상세(하위 잎)에서 같은 금액이 두 번
    계상되지 않도록 비파괴로 정리한다. (합의 규칙: 상세 우선·정확매칭 자동병합·
    나머지 residual·총액 불변)

      (A) 완전중복 잎 제거: 서로 다른 시트에서 (분류경로+품명+규격+금액)이 동일한
          잎 → 깊은(상세) 시트 것을 정본으로 남기고 나머지는 총액에서 제외.
      (B) 요약행 roll-up: 얕은(요약) 시트의 행 금액이 깊은(상세) 시트의 해당 카테고리
          잎 합계와 일치하면 → 그 행은 상위 요약이므로 총액에서 제외(상세 잎이 정본).

    안전장치:
      · 금액이 정합(±tol_ratio)될 때만 제외 → 총액 불변.
      · 요약 시트라도 한 행도 정합되지 않으면 '독립 상세 시트'로 보고 전부 보존
        (예: 자재내역 + 인력내역처럼 분리 시트 오병합 방지).
      · 미매칭 요약행은 잎으로 보존하고 residual 표시 → 사람이 판단.

    반환: (items, report)  — items는 전량 보존(삭제 안 함).
      · 제외 대상 행은 it["merge_status"] = 'duplicate' | 'rolled_up' 플래그만 붙임
        (DB에서 is_header=1로 저장돼 합계에서 제외, 되돌리기 가능).
      · 미매칭 요약행은 it["_summary_unmatched"] = True (잎 유지 + residual).
      report: {duplicates, rollups[], unmatched_summary}
    """
    by_sheet = {}
    for it in items:
        by_sheet.setdefault(it.get("_sheet"), []).append(it)

    def sheet_depth(name):
        return max((len(_path_parts(x.get("path"))) for x in by_sheet.get(name, [])),
                   default=0)

    report = {"duplicates": 0, "rollups": [], "unmatched_summary": 0}

    # ── (A) 완전중복 잎 플래그 (깊은 시트를 정본으로) ──
    seen = {}
    for it in sorted(items, key=lambda x: -sheet_depth(x.get("_sheet"))):
        amt = it.get("amount")
        key = (_norm(it.get("path")), _norm(it.get("name_normalized")),
               _norm(it.get("spec")), round(float(amt or 0), 2))
        prev = seen.get(key)
        # 금액이 있고, 이미 '다른 시트'에서 같은 잎이 나왔으면 중복으로 제외 플래그
        if amt and prev is not None and prev.get("_sheet") != it.get("_sheet"):
            it["merge_status"] = "duplicate"
            report["duplicates"] += 1
            continue
        seen.setdefault(key, it)

    # ── (B) 요약행 roll-up 플래그 ──
    depths = {n: sheet_depth(n) for n in by_sheet if n is not None}
    if depths:
        max_depth = max(depths.values())
        detail_sheets = {n for n, d in depths.items() if d == max_depth and d >= 1}
        summary_sheets = {n for n, d in depths.items() if d < max_depth}
        if detail_sheets and summary_sheets:
            cat_total = {}   # 정본(상세) 상위카테고리 → 잎 합계 (병합 제외행은 빼고)
            for it in items:
                if it.get("_sheet") in detail_sheets and it.get("amount") \
                        and not it.get("merge_status"):
                    parts = _path_parts(it.get("path"))
                    topcat = parts[0] if parts else (it.get("category") or "")
                    cat_total[_norm(topcat)] = cat_total.get(_norm(topcat), 0.0) + \
                        float(it.get("amount") or 0)
            detail_grand = sum(cat_total.values())   # 상세 정본 총액(전체)
            for sname in summary_sheets:
                srows = [it for it in items
                         if it.get("_sheet") == sname and not it.get("merge_status")]
                # [견고성 6f · 전체요약] 요약 시트의 상위 레벨(대분류)이 상세의 상위카테고리
                #  (더 깊은 레벨)와 이름이 안 맞아 per-category roll-up이 실패해도, '시트 총액이
                #  상세 정본 총액과 일치'하면 이 시트 전체가 top-level 요약(갑지·공종목록)이다.
                #  → 전 행 roll-up(이중계상 방지, Δ=0). 독립 breakdown(총액 불일치)은 여기 미해당
                #  → 아래 per-category/summary_unmatched 경로로 잎·residual 보존(손실 0).
                sheet_sum = sum(float(s.get("amount") or 0) for s in srows)
                whole = (detail_grand > 0
                         and abs(sheet_sum - detail_grand) <= max(1.0, detail_grand * tol_ratio))
                if whole:
                    for s in srows:
                        if s.get("amount") and not s.get("merge_status"):
                            s["merge_status"] = "rolled_up"
                            report["rollups"].append({
                                "name": s.get("name_normalized"), "amount": s.get("amount"),
                                "matched_category": "(전체요약)", "detail_sum": detail_grand,
                                "sheet": sname})
                    continue
                pend = []
                for s in srows:
                    amt = float(s.get("amount") or 0)
                    if not amt:
                        continue
                    key = _norm(s.get("name_normalized"))
                    for c, tot in cat_total.items():
                        # [코드리뷰 M20] 짧은 카테고리(예 '관')가 무관한 요약행명에 substring
                        #  매칭돼 금액 우연 일치 시 정상행이 오 roll-up되던 것 방지: 정확일치 또는
                        #  '2자 이상' 포함만 인정.
                        if _cat_name_match(c, key) and \
                                abs(amt - tot) <= max(1.0, tot * tol_ratio):
                            pend.append((s, c, tot))
                            break
                if pend:   # 최소 1행 정합 → 이 시트는 '요약' 시트로 확정
                    matched_ids = {id(s) for s, _, _ in pend}
                    for s, c, tot in pend:
                        s["merge_status"] = "rolled_up"
                        report["rollups"].append({
                            "name": s.get("name_normalized"), "amount": s.get("amount"),
                            "matched_category": c, "detail_sum": tot, "sheet": sname})
                    for s in srows:   # 미매칭 요약행 → 잎 유지 + residual
                        if id(s) not in matched_ids and not s.get("merge_status") and s.get("amount"):
                            s["_summary_unmatched"] = True
                            report["unmatched_summary"] += 1

    return items, report


def _read_records(path, sheet, mapping, header_row):
    """시트를 레코드 리스트로 읽는다(병합 복원 적용). 레벨키는 절대레벨 정수."""
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet]
    fill = _build_merge_fill(ws)

    def cv(r, c):
        v = ws.cell(r, c).value
        if (v is None or str(v).strip() == "") and (r, c) in fill:
            return fill[(r, c)]
        return v

    catcols = {_cat_level(role): col for col, role in mapping.items() if role in CAT_ROLES}
    name_col = next((c for c, r in mapping.items() if r == "name"), None)
    seq_col = next((c for c, r in mapping.items() if r == "seq"), None)
    # [코드리뷰 H10] 통화·원화 열도 읽어 스티칭 item이 통화 정합을 받도록.
    info = {r: col for col, r in mapping.items()
            if r in ("qty", "unit", "price", "amount", "spec", "maker", "part",
                     "currency", "price_krw", "amount_krw")}

    recs = []
    for r in range(header_row + 1, ws.max_row + 1):
        rowvals = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        if not any(v is not None and str(v).strip() for v in rowvals):
            continue
        rec = {"row": r}
        for lv, col in catcols.items():
            v = cv(r, col)
            rec[lv] = str(v).strip() if v not in (None, "") else None
        if name_col:
            v = cv(r, name_col)
            rec["name"] = str(v).strip() if v not in (None, "") else None
        if seq_col:
            v = cv(r, seq_col)
            # [구분자 무관] 원본 문자열은 보존하고, 계층·조인용 숫자 그룹 튜플을 함께 저장.
            rec["seq"] = str(v).strip() if v not in (None, "") else None
            rec["seq_t"] = seq_tuple(v)
        for role, col in info.items():
            v = cv(r, col)
            rec[role] = _to_number(v) if role in (
                "qty", "price", "amount", "price_krw", "amount_krw") else (
                str(v).strip() if v not in (None, "") else None)
        recs.append(rec)
    wb.close()
    meta = {"levels": sorted(catcols), "has_name": name_col is not None,
            "has_seq": seq_col is not None, "info": sorted(info)}
    return recs, meta


def _is_total_row(rec, levels):
    """총계/소계 행 판정 — 정밀 경계 매칭(부분문자열 오탐 방지). [C.i]

    [견고성 5-dc] 합계 마커가 품명 셀에 있어도 분류 셀 값이 조인 뒤에 붙으면
    ("소계 재료비") is_total_label 의 endswith 앵커가 무력화돼 미탐 → 이중계상.
    → 조인 문자열 검사에 더해 '각 셀 단독'으로도 판정(마커가 어느 셀에 있든 검출).
    셀 단독 판정은 is_total_label 자체가 정밀(강마커 endswith·단독'계'·영문 전체일치)
    하므로 오탐 위험 없음. 실샘플 총액 불변(Δ=0) 확인.
    """
    keys = ["name"] + list(levels)
    txt = " ".join(str(rec.get(k) or "") for k in keys).strip()
    if is_total_label(txt):
        return True
    return any(is_total_label(rec.get(k)) for k in keys if rec.get(k))


def _classify_sheet(recs, meta):
    """시트 역할: 'leaf'(품목 보유) / 'band'(분류만) / 'seq_tree' / 'seq_list'."""
    # [구분자 무관] 숫자 그룹이 2개 이상인 번호가 있으면 계층 시트(구분자 종류 무관).
    dotted = meta["has_seq"] and any(
        (r.get("seq_t") is not None and len(r["seq_t"]) >= 2) for r in recs)
    # [코드리뷰 M19] 품명+금액만 있고 수량·단가 열이 없는 요약형 잎 시트도 leaf로 인식
    #  (amount 포함). 금액만 있는 견적서가 band/seq_list로 오분류돼 잎이 안 붙던 것 방지.
    has_item = meta["has_name"] and any(
        (r.get("price") or r.get("qty") or r.get("amount")) for r in recs)
    if dotted and has_item:
        return "seq_leaf"
    if dotted:
        return "seq_tree"
    # 품목(단가/수량) 보유 = 잎. 정수 seq('No')가 섞여 있어도 잎 우선.
    if has_item:
        return "leaf"
    # 정수 seq + (품목명 또는 분류열) = 목록/트리 시트.
    #  [버그수정] 목록/트리 시트의 레벨 이름을 사용자가 대분류/중분류(cat)로 매핑해도
    #  seq로 계층을 잇는 '이름 제공' 시트로 인식(band 오분류 방지).
    if meta["has_seq"] and (meta["has_name"] or meta["levels"]):
        return "seq_list"
    if meta["levels"]:
        return "band"
    return "unknown"


# ── BAND 아키타입: 겹치는 레벨 값으로 파노라마 조인 ──
def _band_join(bands):
    """bands: [(level_set, [tuple{lv:val}])]. 공유 레벨 equi-join으로 전체 경로 relation."""
    rels = []
    for levels, rows in bands:
        tuples = []
        seen = set()
        for row in rows:
            t = tuple((k, row.get(k)) for k in sorted(levels) if row.get(k))
            if t and t not in seen:
                seen.add(t)
                tuples.append(dict(t))
        rels.append([set(levels), tuples])
    rels.sort(key=lambda x: min(x[0]))
    merged_levels = set(rels[0][0])
    merged = [dict(t) for t in rels[0][1]]
    used = [False] * len(rels)
    used[0] = True
    changed = True
    while changed:
        changed = False
        for i in range(len(rels)):
            if used[i]:
                continue
            lv, tuples = rels[i]
            shared = merged_levels & lv
            if not shared:
                continue
            out = []
            seen = set()
            for m in merged:
                for t in tuples:
                    # [동일명 인식] 시트 간 공유 레벨 조인키는 정규화 비교
                    # (공백·대소문자·후행공백 차이로 같은 분류를 놓치지 않도록).
                    if all(_norm(m.get(s)) == _norm(t.get(s)) for s in shared):
                        nm = dict(m)
                        nm.update(t)
                        key = tuple(sorted(nm.items()))
                        if key not in seen:
                            seen.add(key)
                            out.append(nm)
            merged = out
            merged_levels |= lv
            used[i] = True
            changed = True
    return merged_levels, merged


def _stitch_band(sheets):
    """band 시트 + leaf 시트를 조인해 잎 경로 복원."""
    band_sheets = [(m["levels"], recs) for (recs, m, role) in sheets if role == "band"]
    leaves = [(recs, m) for (recs, m, role) in sheets if role == "leaf"]
    items, residuals = [], []
    if not leaves:
        return items, residuals, "band(no-leaf)", []

    skel_levels, skeleton = (set(), [])
    if band_sheets:
        skel_levels, skeleton = _band_join(band_sheets)
    # 후보 경로: 스켈레톤이 복원한 모든 정상 분류 경로(오배치와 무관하게 전체 제공)
    cand_paths = set()
    for s in skeleton:
        p = PATH_SEP.join(s[lv] for lv in sorted(skel_levels) if s.get(lv))
        if p:
            cand_paths.add(p)

    for recs, m in leaves:
        leaf_top = m["levels"][0] if m["levels"] else None   # 잎 시트의 최상위 분류 레벨
        for r in recs:
            if _is_total_row(r, m["levels"]) or not r.get("name"):
                continue
            parts = []
            matched = True
            ambiguous = False
            if skeleton and leaf_top is not None and r.get(leaf_top):
                # [동일명 인식] 잎→스켈레톤 조인키도 정규화 비교(공백·대소문자 차이 흡수)
                cand = [s for s in skeleton if _norm(s.get(leaf_top)) == _norm(r.get(leaf_top))]
                # 모호 조인 검출: 같은 조인키가 서로 다른 상위 경로로 이어지면 추측 금지→residual
                distinct = {tuple(_norm(s.get(lv)) for lv in sorted(skel_levels)) for s in cand}
                if len(distinct) > 1:
                    ambiguous = True
                    matched = False
                if cand:
                    s = cand[0]
                    for lv in sorted(skel_levels):
                        if s.get(lv):
                            parts.append(s[lv])
                else:
                    matched = False
                    # 스켈레톤 미매칭 → 잎 자체 분류만
                    for lv in m["levels"]:
                        if r.get(lv):
                            parts.append(r[lv])
            else:
                for lv in m["levels"]:
                    if r.get(lv):
                        parts.append(r[lv])
            # 잎 시트가 자체 보유한 하위 레벨(스켈레톤보다 깊은) 추가
            for lv in m["levels"]:
                if lv > (max(skel_levels) if skel_levels else 0) and r.get(lv) and r[lv] not in parts:
                    parts.append(r[lv])
            _pp, _ln = _part_promote(r, PATH_SEP.join(parts), r.get("name"))
            item = {
                "path": _pp,
                "depth": len([x for x in _pp.split(PATH_SEP) if x]),
                "name_normalized": _ln,
                "spec": r.get("spec"),
                "maker": r.get("maker"),
                "quantity": r.get("qty"),
                "unit": r.get("unit"),
                "unit_price": r.get("price"),
                "amount": r.get("amount"),
                "line_no": f"R{r['row']}",
                "_matched": matched,
            }
            _apply_currency(item, r)   # [H10] 통화 정합
            items.append(item)
            if not matched:
                residuals.append({
                    "reason": "ambiguous_join_key" if ambiguous else "skeleton_unmatched",
                    "leaf_key": r.get(leaf_top), "name": r.get("name"),
                    "assigned_path": item["path"], "row": r["row"]})
    return items, residuals, "band", sorted(cand_paths)


def _emit_uncovered_band_costs(sheets, items, residuals):
    """[견고성 7-4] band 요약 시트의 금액 행 중 '하위 상세에 대응 없는 고유비용'
    (부대비 등)을 잎/residual 로 보존해 총액 누락을 막는다.

    · '완전 고립' 판정(보수적 · Δ=0 우선): 요약 band 행의 분류 레벨 값이 방출된 어떤 잎의
      경로 세그먼트에도 '전혀 등장하지 않으면'(공유값 0) → 하위 상세가 전무한 독립 고유비용
      (예 부대비) → 잎+residual로 보존(총액 포함). 한 값이라도 잎 경로에 등장하면 그 행은
      분류 계층의 일부(빈 카테고리 소계 등)이므로 방출 안 함 → C2 실샘플 Δ=0(회귀 없음).
    · 총계/소계·금액 없는 행 제외, 통화 정합 적용, band 시트 간 동일경로 중복 방출 방지.
    """
    union_segs = set()
    for it in items:
        if it.get("merge_status"):
            continue
        union_segs |= {_norm(x) for x in _path_parts(it.get("path"))}
    seen_paths = set()
    for recs, meta, role in sheets:
        if role != "band":
            continue
        levels = meta["levels"]
        for r in recs:
            amt = r.get("amount")
            if not amt or _is_total_row(r, levels):
                continue
            vals = [r.get(lv) for lv in levels if r.get(lv)]
            if not vals:
                continue
            vset = {_norm(v) for v in vals}
            if vset & union_segs:
                continue   # 잎 경로와 값 하나라도 공유 = 계층 일부 → 방출 안 함(Δ=0)
            path = PATH_SEP.join(vals)
            if _norm(path) in seen_paths:
                continue   # band 시트 간 동일 고유비용 중복 방출 방지
            seen_paths.add(_norm(path))
            item = {
                "path": path, "depth": len(vals),
                "name_normalized": vals[-1], "name_raw": vals[-1],
                "spec": r.get("spec"), "maker": r.get("maker"),
                "quantity": r.get("qty"), "unit": r.get("unit"),
                "unit_price": r.get("price"), "amount": amt,
                "line_no": f"R{r['row']}", "_matched": False,
                "_band_unique": True, "category": vals[0],
                "is_category_header": False, "_sheet": None,
            }
            _apply_currency(item, r)   # [H10] 통화 정합
            items.append(item)
            residuals.append({"reason": "band_unique_cost", "name": vals[-1],
                              "assigned_path": path, "row": r["row"]})


# ── SEQ 아키타입: 번호계층 → 이름 해석 ──
def _rec_name(r):
    """seq 행의 '레벨 이름'을 얻는다. 품목명(name)이 있으면 그것, 없으면 가장 깊은
    분류(cat) 값을 이름으로 사용. (목록/트리 시트의 이름이 대분류/중분류로 매핑된 경우 대응)"""
    if r.get("name"):
        return r.get("name")
    for lv in (5, 4, 3, 2, 1):
        if r.get(lv):
            return r.get(lv)
    return None


def _stitch_seq(sheets, names=None):
    """번호계층(seq) 조인. names는 sheets와 평행한 시트명 리스트(있으면 항목에 _sheet 태깅).

    [다중 seq_leaf] 세부(품목) 시트가 여러 개면 '모두' 처리한다. (기존엔 첫 시트만
    처리해 나머지 세부 시트 항목이 통째로 소실됐음 — 다중시트 통합 시 데이터 유실.)
    seq 조인은 번호계층(1.1.1)의 '점 자릿수'로만 부모를 잇고, 서로 다른 세부 시트의
    잎은 각각 독립 항목으로 보존(단순 seq 동일으로 병합하지 않음). 시트 출처는 _sheet로
    구분되어 뒤의 _dedup_and_rollup이 시트 경계를 고려해 중복만 정리(총액 불변).
    """
    if names is None:
        names = [None] * len(sheets)
    seqmap = {}       # 번호 튜플 prefix -> 이름 (계층 정의: 목록/트리 시트 우선)

    def _add_name_sources(role_ok):
        for (recs, m, role), _sn in zip(sheets, names):
            if not role_ok(role, m):
                continue
            for r in recs:
                st = r.get("seq_t")   # [구분자 무관] 숫자 그룹 튜플
                nm = _rec_name(r)
                if st and nm and not _is_total_row(r, []):
                    seqmap.setdefault(st, nm)   # 기존 정의 우선(회귀 방지)

    # 1차: 명시적 목록/트리/세부 시트가 계층 이름을 정의(우선).
    _add_name_sources(lambda role, m: role in ("seq_list", "seq_tree", "seq_leaf"))
    # 2차: [C3 실파일 대분류 소실] 정수-seq '이름 목록' 시트(예 공종목록: 번호 1,2,3,4 +
    #  공종명 + 금액)는 금액 때문에 role='leaf'로 분류돼 1차에서 빠졌다 → 상위 분류(대분류)
    #  이름이 seqmap에 없어 잎 경로에서 대분류가 소실됐다(gap: 1→1.1.1). has_seq인 leaf 시트를
    #  이름 소스로 추가하되 setdefault로 '미정의 prefix(대분류 자리)만' 채움 → 세부/트리 이름은
    #  그대로, 대분류만 복원. 금액은 뒤의 roll-up이 정리(Δ=0).
    _add_name_sources(lambda role, m: role == "leaf" and m.get("has_seq"))

    items, residuals = [], []
    leaf_sheets = [(recs, sn) for (recs, m, role), sn in zip(sheets, names)
                   if role == "seq_leaf"]
    if not leaf_sheets:
        return items, residuals, "seq(no-leaf)", []
    for leaf, sname in leaf_sheets:
        for r in leaf:
            st = r.get("seq_t")
            nm = r.get("name")
            if not nm or _is_total_row(r, []):
                continue
            parts = []
            if st is not None:
                # 튜플 prefix로 부모 이름 조인 (구분자 종류 무관).
                for d in range(1, len(st)):
                    prefix = st[:d]
                    if prefix in seqmap:
                        parts.append(seqmap[prefix])
                parts.append(nm)
            else:
                parts = [nm]
            # 부모 이름을 하나도 못 찾았으면(잎만) residual. 계층(2튜플 이상)인데 부모 없으면 미매칭.
            matched = len(parts) > 1 or not (st is not None and len(st) >= 2)
            _leaf = r.get("part") or nm   # [부품] 부품 있으면 잎=부품(경로 끝은 이미 품목)
            item = {
                "path": PATH_SEP.join(parts), "depth": len(parts),
                "name_normalized": _leaf, "spec": r.get("spec"), "maker": r.get("maker"),
                "quantity": r.get("qty"), "unit": r.get("unit"),
                "unit_price": r.get("price"), "amount": r.get("amount"),
                "line_no": f"R{r['row']}", "_matched": matched,
            }
            _apply_currency(item, r)   # [H10] 통화 정합
            if sname is not None:
                item["_sheet"] = sname   # [다중 seq_leaf] 시트 출처 태깅(중복정리 경계)
            items.append(item)
            if not matched:
                residuals.append({"reason": "seq_parent_missing",
                                  "seq": r.get("seq"), "name": nm, "row": r["row"]})
    cand = sorted({it["path"] for it in items if it.get("_matched") and it.get("path")})
    return items, residuals, "seq", cand


def _stitch_passthrough(path, sheet, mapping, header_row):
    """단일 완결 시트. 병합-충전(정당)과 행간 상속(불완전 신호)을 구분해 residual 판정.

    핵심: _read_records는 '셀 병합 복원'만 적용(같은 논리그룹이라 정당)하고
    '행간 fill-down'은 하지 않는다. 따라서 병합복원 후에도 상위 분류가 빈 행은
    → 원본이 레벨을 생략(레벨스킵)한 것 → 행간 상속으로 부모를 지어내면 오연결.
    이런 행을 residual로 올려 LLM 검증·사람 판별로 넘긴다.
    """
    recs, meta = _read_records(path, sheet, mapping, header_row)
    levels = meta["levels"]
    deepest = max(levels) if levels else 0
    # [레벨 정렬] 이 시트 최상위 분류의 '의미 레벨'(대=1/중=2/소=3/세=4). 매핑 역할 기준.
    #  시트가 [중>소>세]면 top_level=2 → 교차시트 미매칭 시 대분류로 승격하지 않도록 사용.
    top_level = min(levels) if levels else 1
    items, residuals = [], []
    last_cat = {}
    for r in recs:
        # [품명 없는 입찰서] 품목명(name) 열이 없으면 '가장 깊은 분류값'을 잎으로 삼는다.
        #  (중/소분류까지만 기입된 견적서도 그 분류가 곧 항목이 되도록 — 추출 0 방지)
        _leafnm = r.get("name")
        if not _leafnm and not meta.get("has_name") and r.get("amount"):
            _pres = [lv for lv in levels if r.get(lv)]
            if _pres:
                _leafnm = r.get(max(_pres))
        if _is_total_row(r, levels) or not _leafnm:
            # 분류만 있고 이름/금액 없는 소계행 등은 상속 소스로도 쓰지 않음
            continue
        # 병합복원 후 실제 존재하는 레벨
        present = [lv for lv in levels if r.get(lv)]
        complete = all(r.get(lv) for lv in levels)  # 1..deepest 모두 존재?
        # [중간레벨 부재=직접 붙임, extract와 통일] 존재값만 이어 붙인다.
        #  ① 미매핑 레벨(비연속 gap) → placeholder 없이 직접 스킵. ② 매핑 빈 레벨은 fill-down
        #  상속(정당) 후에도 비면 직접 스킵. 누락 의심은 아래 matched=complete → residual(level_skip)로
        #  이미 표기되므로 가짜 placeholder 노드를 만들지 않는다(경로/depth만 바뀜, 총액 Δ=0).
        parts = []
        _row_gap = False
        _lv_set = set(levels)
        for lv in (range(min(levels), max(levels) + 1) if levels else ()):
            if lv in _lv_set:
                v = r.get(lv)
                if v:
                    last_cat[lv] = v
                else:
                    v = last_cat.get(lv)
                if v:
                    parts.append(v)
            # else: ① 미매핑 gap → 직접 붙임(스킵)
        matched = complete
        _pp, _ln = _part_promote(r, PATH_SEP.join(parts), _leafnm)
        item = {
            "path": _pp, "depth": len([x for x in _pp.split(PATH_SEP) if x]),
            "name_normalized": _ln, "spec": r.get("spec"), "maker": r.get("maker"),
            "quantity": r.get("qty"), "unit": r.get("unit"),
            "unit_price": r.get("price"), "amount": r.get("amount"),
            "line_no": f"R{r['row']}", "_matched": matched, "_top_level": top_level,
        }
        _apply_currency(item, r)   # [H10] 통화 정합
        if _row_gap:
            # 중간 레벨 gap placeholder가 낀 항목 → 미연계 표기(수기 연결 대기).
            #  _run_stitch가 _level_residual를 residual(cross_level_unmatched)로 올린다.
            item["_level_residual"] = True
        items.append(item)
        if not matched:
            missing = [lv for lv in levels if not r.get(lv)]
            residuals.append({"reason": "level_skip",
                              "missing_levels": missing,
                              "inherited_path": item["path"],
                              "name": r.get("name"), "row": r["row"]})
    cand = sorted({it["path"] for it in items if it.get("_matched") and it.get("path")})
    return items, residuals, "passthrough", cand


def _part_promote(rec, path_str, leaf_name):
    """[부품] 부품 값이 있으면 품목(leaf_name)을 분류 경로 끝으로 내리고 부품을 잎으로.
    반환: (새 path_str, 새 leaf_name). 부품 없으면 그대로."""
    pv = rec.get("part")
    if pv:
        if leaf_name:
            _last = path_str.split(PATH_SEP)[-1] if path_str else None
            if leaf_name != _last:   # 세분류와 품목이 동일하면 중복 방지
                path_str = (path_str + PATH_SEP + leaf_name) if path_str else leaf_name
        return path_str, pv
    return path_str, leaf_name


def _cross_sheet_reparent(items):
    """[D.ii] 서로 다른 시트가 '공유 분류명'으로 이어질 때, 얕은 시트의 루트 서브트리를
    다른 시트에서 같은 이름의 상위 분류 노드 아래로 이어붙여 하나의 트리로 연결한다.

    예) 견적서 [대>중>소>품명], 주요부품 [소>품명>부품]:
        주요부품의 루트 '소분류'가 견적서에서 [대>중] 아래에 있으면, 주요부품 서브트리를
        그 [대>중] 아래로 접합 → [대>중>소>품명>부품]으로 하나의 트리에 묶인다.

    안전 원칙(총액 불변):
      · **경로(트리 위치)만 이동**하고 금액·잎 수는 절대 바꾸지 않는다 → Δ=0 보장.
      · 대상 상위 경로가 **유일**할 때만 접합(모호하면 보존). 완전중복/roll-up으로
        제외된(merge_status) 행은 접합 대상·기준에서 제외.
      · dedup·roll-up **이후**에 실행 → 기존 병합/총액 로직에 영향 없음.
    """
    live = [it for it in items if not it.get("merge_status")]
    by_sheet = {}
    for it in live:
        by_sheet.setdefault(it.get("_sheet"), []).append(it)
    if len([s for s in by_sheet if s is not None]) < 2:
        return items

    # 각 시트의 '비루트 분류 노드' 이름 → 그 노드까지의 조상경로. (stitch path엔 품명이
    #  별도이므로 path의 모든 세그먼트가 분류 노드다. i=0[루트] 제외, i>=1만 대상.)
    interior = {}   # norm(name) -> set of (sheet, ancestor_prefix_str)
    for sh, its in by_sheet.items():
        for it in its:
            parts = _path_parts(it.get("path"))
            for i in range(1, len(parts)):
                nm = _norm(parts[i])
                if nm:
                    interior.setdefault(nm, set()).add((sh, PATH_SEP.join(parts[:i])))
    if not interior:
        return items

    def _roots_of(its):
        roots = {}
        for it in its:
            parts = _path_parts(it.get("path"))
            if parts:
                roots.setdefault(_norm(parts[0]), parts[0])
        return roots

    def _ext_prefixes(sh, rnorm, imap):
        return {pre for (s, pre) in imap.get(rnorm, set()) if s != sh and pre}

    # ── 1차: 매칭 안 되는 짧은 시트의 상위 레벨 placeholder padding ──
    #  (접합 대상이 될 시트를 '먼저' 절대 레벨로 채운 뒤 interior를 재구성해야, 다른 시트가
    #   이 시트의 '정렬된' 조상경로 아래로 접합돼 시트 간 같은 레벨이 같은 depth로 정렬된다.)
    for sh, its in by_sheet.items():
        sheet_top = min((it.get("_top_level", 1) for it in its), default=1)
        if sheet_top <= 1:
            continue
        pad = [_LEVEL_PLACEHOLDER.get(lv, _LEVEL_PLACEHOLDER_DEFAULT)
               for lv in range(1, sheet_top)]
        pre = PATH_SEP.join(pad)
        for rnorm in _roots_of(its):
            # 이 루트가 다른 시트의 내부 노드로 유일 접합되면 → padding 말고 2차에서 접합.
            if len(_ext_prefixes(sh, rnorm, interior)) == 1:
                continue
            for it in its:
                parts = _path_parts(it.get("path"))
                if parts and _norm(parts[0]) == rnorm:
                    it["path"] = pre + PATH_SEP + it["path"]
                    it["depth"] = len(_path_parts(it["path"]))
                    it["category"] = pad[0]
                    it["_level_residual"] = True   # 상위 미매칭 → 미연계 표기

    # interior 재구성(1차 padding 반영) — 접합 시 padding된 조상경로를 쓰도록.
    interior2 = {}
    for sh, its in by_sheet.items():
        for it in its:
            parts = _path_parts(it.get("path"))
            for i in range(1, len(parts)):
                nm = _norm(parts[i])
                if nm:
                    interior2.setdefault(nm, set()).add((sh, PATH_SEP.join(parts[:i])))

    # ── 2차: 매칭되는 시트 루트를 (padding 정렬된) 조상경로 아래로 접합 ──
    for sh, its in by_sheet.items():
        for rnorm in _roots_of(its):
            prefixes = {pre for (s, pre) in interior2.get(rnorm, set()) if s != sh and pre}
            if len(prefixes) == 1:
                prefix = next(iter(prefixes))
                for it in its:
                    parts = _path_parts(it.get("path"))
                    if parts and _norm(parts[0]) == rnorm:
                        it["path"] = prefix + PATH_SEP + it["path"]
                        it["depth"] = len(_path_parts(it["path"]))
                        it["category"] = _path_parts(it["path"])[0]   # 최상위 분류 갱신
    return items


# ─────────────────────────────────────────────────────────────────────────────
# [항목 연결 3단계 원칙]  (반드시 유지 — 스티칭이 '자체적으로' 3단계를 소유)
#   대상 축: 한 제출서 내 시트 간(intra-submission cross-sheet) 항목 연결.
#   1단계 완전합치(코드)  [구현됨] : 결정론적 exact-match. 시트 내/시트 간 완전동일
#       (정규화 분류경로+품명+규격+금액)만 자동 병합 → _dedup_and_rollup (A)+(B).
#   2단계 유사 합치(LLM)  [계획]   : 완전합치 안 된 '유사' 항목쌍을 LLM 유사도로 '연결 제안'
#       (자동 병합 금지). catalog_clusterer/matcher의 LLM 유사도 엔진을 intra-submission
#       축으로 재사용(온디맨드 라우트로 분리 — 추출 헤드리스 인라인 금지, 폐쇄망=휴리스틱 폴백).
#   3단계 사람 확정       [계획]   : 제안을 residual 패널/연계 캔버스에서 accept/reject·확정.
#
# ⚠️ 유사(비완전합치)를 코드가 자동 병합하거나 '미연계'로 종결 표기하면 2단계(LLM)를 건너뛴
#    오합치·오종결이다. 2·3단계 구현 전까지 유사 항목은 별도 정본 잎으로 보존(총액 Δ=0).
#    (설계 계획: docs/QDBT_스티칭_LLM유사연결_계획_20260711.md · CHANGELOG 참조.)
# ─────────────────────────────────────────────────────────────────────────────


def _finalize_items(items):
    """insert_items_bulk 호환 필드 보강: category, name_raw."""
    for it in items:
        segs = (it.get("path") or "").split(PATH_SEP)
        it.setdefault("category", segs[0] if segs and segs[0] else "기타")
        it.setdefault("name_raw", it.get("name_normalized"))
        it.setdefault("is_category_header", False)
    return items


def _detect_link_candidates(items, tol_ratio=0.01):
    """[항목연결 1→2단계 다리] 시트 간 '유사 연결 후보'(코드)를 비파괴 산출.

    기준(보수적 시작): **정규화 품명 동일 + 서로 다른 분류경로 + 서로 다른 시트 + 금액 근접**.
    이는 자동 병합·미연계 종결이 '아니다'. 2단계(LLM 유사 제안)의 입력 후보 목록만 만든다.
    (완전동일=경로까지 같음은 이미 _dedup_and_rollup (A)에서 dedup되어 여기 안 들어옴.)

    item_id는 이 시점(추출)엔 없다 → 후보 멤버는 (line_no, sheet, path)로 식별하고,
    DB 저장 후 2단계 라우트가 line_no+path로 item_id를 복원한다(_build_residual_view와 동일).

    반환: [{"key", "name", "members": [{line_no, sheet, path, amount, spec, name}...]}]
    금액·merge_status 불변 → 총액 Δ=0(읽기 전용).
    """
    groups = {}
    for it in items:
        if it.get("merge_status"):
            continue
        amt = it.get("amount")
        nm = _norm(it.get("name_normalized"))
        if not amt or not nm:
            continue
        groups.setdefault(nm, []).append(it)
    cands = []
    for nm, grp in groups.items():
        if len(grp) < 2:
            continue
        sheets = {g.get("_sheet") for g in grp}
        paths = {_norm(g.get("path")) for g in grp}
        if len(sheets) < 2 or len(paths) < 2:
            continue   # 시트경계·경로차이 없으면 후보 아님
        amts = [float(g.get("amount")) for g in grp]
        amax = max(abs(a) for a in amts) or 1.0
        if (max(amts) - min(amts)) > max(1.0, amax * tol_ratio):
            continue   # 금액 근접 아님 → 다른 항목일 가능성(보수적 제외)
        members = [{
            "line_no": g.get("line_no"), "sheet": g.get("_sheet"),
            "path": g.get("path"), "amount": g.get("amount"),
            "spec": g.get("spec"), "name": g.get("name_normalized"),
        } for g in grp]
        cands.append({"key": nm, "name": grp[0].get("name_normalized"),
                      "members": members})
    return cands


def _run_stitch(path, sheet_infos, sheet_names):
    """sheet_infos: [{name, mapping, header_row}]. 공통 스티칭 실행부."""
    sheets = []
    infos = []
    for si in sheet_infos:
        recs, meta = _read_records(path, si["name"], si["mapping"], si["header_row"])
        role = _classify_sheet(recs, meta)
        sheets.append((recs, meta, role))
        infos.append({"name": si["name"], "role": role, "levels": meta["levels"],
                      "header_row": si["header_row"], "mapping": si["mapping"],
                      "n_rows": len(recs)})

    roles = [role for _, _, role in sheets]
    candidates = []
    if "seq_leaf" in roles:
        # [다중 seq_leaf] 시트명을 넘겨 각 항목에 _sheet 태깅 → 세부 시트가 여럿이어도
        #  모두 처리(첫 시트만 처리해 나머지 소실되던 문제 해결).
        items, residuals, mode, candidates = _stitch_seq(sheets, [i["name"] for i in infos])
        _leafname = next((info["name"] for (_, _, r), info in zip(sheets, infos)
                          if r == "seq_leaf"), None)
        for x in items:
            x.setdefault("_sheet", _leafname)   # 안전망(태깅 누락 시 첫 leaf명)
        # [버그2] _stitch_seq는 seq_leaf 시트만 항목으로 방출한다. 자체 금액을 지닌
        #  seq_list/seq_tree 시트(요약이든 breakdown이든)는 이름사전으로만 쓰이고 통째로
        #  누락됨 → 그 시트가 breakdown(별도 금액)이면 데이터·연계 준비 모두 소실.
        #  보강: 그런 시트를 passthrough로 추가 방출해 residual(미연계)로 보존(캔버스 수기
        #  연결 대기). 요약(총액이 leaf와 중복)인 경우는 뒤의 _dedup_and_rollup이 시트경계·
        #  카테고리 합 일치로 roll-up 처리 → 총액 불변(Δ=0). 즉 breakdown은 보존, 요약은 정리.
        #  [견고성 6f] 'leaf' 역할 시트(정수번호+금액, 예 갑지·공종목록)도 동일하게 방출.
        #   기존엔 seq 모드에서 seq_* 만 방출해 leaf 시트가 흔적 없이 통째 소실됐다.
        #   방출 후 요약이면 _dedup_and_rollup 이 카테고리 합 일치로 roll-up(Δ=0), 독립
        #   breakdown이면 residual(미연계)로 보존 → 손실 0. (C3 공종목록=요약 → roll-up 유지.)
        for (recs, meta, role), info in zip(sheets, infos):
            if role in ("seq_list", "seq_tree", "leaf") and any(r.get("amount") for r in recs):
                _it, _rs, _, _cd = _stitch_passthrough(
                    path, info["name"], info["mapping"], info["header_row"])
                for x in _it:
                    x["_sheet"] = info["name"]
                    x["_matched"] = False           # 미연계(residual) 후보
                    x["_seq_list_unlinked"] = True  # residual은 dedup/rollup 이후 확정
                items += _it
                # residuals는 아래 _dedup_and_rollup 이후에 '살아남은'(roll-up 안 된)
                #  항목만 올린다 — 요약이 정리되면 미연계 목록에서 자동 제외.
    elif len([r for r in roles if r == "band"]) >= 1 and "leaf" in roles:
        items, residuals, mode, candidates = _stitch_band(sheets)
        _leafname = next((info["name"] for (_, _, r), info in zip(sheets, infos)
                          if r == "leaf"), None)
        for x in items:
            x.setdefault("_sheet", _leafname)
        # [견고성 7-4] band 요약(갑지 대분류별 금액)에 '상세에 없는 고유비용'(부대비 등)이
        #  있으면 골격으로만 쓰여 소실됐다. band 금액을 '무조건' 방출하면 band-JOIN(C2:
        #  분류체계+세부내역 골격 + 품목명세 잎)에서 같은 돈이 이중/삼중 계상돼 회귀한다.
        #  → '대응 잎이 없는(커버 안 되는) 요약행'만 선별 방출: C2처럼 모두 커버되면 미방출
        #  (Δ=0), 부대비처럼 하위 상세 없는 고유비용만 잎+residual로 보존(총액 누락 방지).
        _emit_uncovered_band_costs(sheets, items, residuals)
    else:
        # 단일(또는 밴드/시퀀스 아님) → leaf 시트 passthrough 병합
        items, residuals, mode = [], [], "passthrough"
        cset = set()
        # [품명 없는 다중시트] leaf(품명+수량/단가) 시트가 하나도 없으면, 금액/수량을
        #  지닌 데이터 시트를 각각 passthrough로 추출한다(_stitch_passthrough가 품명
        #  없을 때 최말단 분류를 잎으로 승격 — 단일시트 경로와 동일 원칙).
        #  → 모든 시트가 '분류+금액'만인 다중시트 통합에서 추출 0/총액 0 방지.
        #  leaf가 있으면 band/seq/기존 passthrough가 처리하므로 이 폴백은 비활성(회귀 없음).
        #  중복(요약↔상세)은 뒤의 _dedup_and_rollup이 총액 불변으로 정리.
        _has_leaf = any(role == "leaf" for _, _, role in sheets)
        for (recs, meta, role), info in zip(sheets, infos):
            _data_bearing = any(r.get("amount") or r.get("price") or r.get("qty")
                                for r in recs)
            if role == "leaf" or len(sheet_names) == 1 or (not _has_leaf and _data_bearing):
                it, rs, _, cd = _stitch_passthrough(path, info["name"], info["mapping"], info["header_row"])
                for x in it:
                    x["_sheet"] = info["name"]   # [중복 병합] 출처 시트 태깅
                items += it
                residuals += rs
                cset |= set(cd)
        candidates = sorted(cset)

    _finalize_items(items)

    # [중복 병합] 견적서(요약)+상세 중복 계상 정리 (총액 불변, 행 보존·플래그).
    items, reconciliation = _dedup_and_rollup(items)
    # [버그2] seq 모드에서 추가 방출한 seq_list/seq_tree 항목 중 roll-up으로 정리되지
    #  '않은'(breakdown 성격) 것만 residual(미연계)로 표기 → 요약행은 자동 제외(잡음 방지).
    for it in items:
        if it.get("_seq_list_unlinked") and not it.get("merge_status"):
            ln = it.get("line_no", "")
            row = int(ln[1:]) if isinstance(ln, str) and ln[1:].isdigit() else None
            residuals.append({"reason": "seq_list_unlinked",
                              "name": it.get("name_normalized"),
                              "assigned_path": it.get("path"), "row": row})
    for it in items:   # 미매칭 요약행 → residual(사람 확인)
        if it.get("_summary_unmatched"):
            ln = it.get("line_no", "")
            row = int(ln[1:]) if isinstance(ln, str) and ln[1:].isdigit() else None
            residuals.append({"reason": "summary_unmatched",
                              "name": it.get("name_normalized"),
                              "assigned_path": it.get("path"), "row": row})

    # [개선 8] "○○ 외 10종/개/점" 요약 항목 탐지 → 비파괴 flag + residual(요약항목).
    #  상위 시트가 하위를 묶어 기술한 경우로, 세부 시트로 대체 필요함을 사람에게 알림(자동 대체는 안 함).
    _sum_re = re.compile(r"외\s*\d+\s*(종|개|점|가지|품목|식)")
    for it in items:
        if it.get("merge_status"):   # 이미 병합 제외된 행은 skip
            continue
        nm = it.get("name_normalized") or ""
        if _sum_re.search(nm):
            it["_summary"] = True
            ln = it.get("line_no", "")
            row = int(ln[1:]) if isinstance(ln, str) and ln[1:].isdigit() else None
            residuals.append({"reason": "summary_item", "name": nm,
                              "assigned_path": it.get("path"), "row": row})

    # [D.ii] 교차시트 공유 분류명 접합 — 경로만 이동(총액 불변). dedup·roll-up 이후 실행.
    items = _cross_sheet_reparent(items)
    # [레벨 정렬] 짧은 시트의 상위 미매칭 항목(placeholder 배치) → residual(미연계) 표기.
    for it in items:
        if it.get("_level_residual") and not it.get("merge_status"):
            ln = it.get("line_no", "")
            row = int(ln[1:]) if isinstance(ln, str) and ln[1:].isdigit() else None
            residuals.append({"reason": "cross_level_unmatched",
                              "name": it.get("name_normalized"),
                              "assigned_path": it.get("path"), "row": row})

    # [항목 연결 3단계 원칙 — 위 배너 참조] 시트 간 (품명+규격+금액) 동일하나 '분류경로가
    #  다른' 잎은 '유사(비완전합치)'이므로 코드가 자동 병합·미연계 종결하지 않는다. 2·3단계
    #  (LLM 유사 제안→사람 확정)는 스티칭 플로우가 자체 수행 예정(계획 문서 참조). 그 전까지
    #  유사 항목은 별도 정본 잎으로 보존(총액 Δ=0). 완전동일(경로까지)은 이미 (A)에서 dedup.

    # 총액·건수는 병합 제외행(merge_status)을 뺀 정본 잎 기준.
    leaf_total = sum(it["amount"] for it in items
                     if it.get("amount") and not it.get("merge_status"))
    n_dropped = reconciliation["duplicates"] + len(reconciliation["rollups"])
    n_kept = sum(1 for it in items if not it.get("merge_status"))
    # [항목연결 1단계] 유사 연결 후보(코드, 비파괴) → 2단계 LLM 제안 입력.
    link_candidates = _detect_link_candidates(items)
    return {
        "mode": mode, "items": items, "n_items": n_kept,
        "residuals": residuals, "n_residuals": len(residuals),
        "candidates": candidates,
        "link_candidates": link_candidates,
        "n_link_candidates": len(link_candidates),
        "sheets": infos, "totals": {"leaf_sum": leaf_total},
        "reconciliation": reconciliation,
        "n_dropped": n_dropped,
    }


def stitch_sheets(path, sheet_specs):
    """확정된 시트별 매핑으로 스티칭. (추출 파이프라인 연결용)

    sheet_specs: [{"sheet"|"name": str, "mapping": {col:role}, "header_row": int}, ...]
    반환: _run_stitch 결과 dict.
    """
    infos = []
    for sp in sheet_specs:
        infos.append({"name": sp.get("sheet") or sp.get("name"),
                      "mapping": {int(k): v for k, v in (sp.get("mapping") or {}).items()},
                      "header_row": int(sp.get("header_row") or 1)})
    names = [i["name"] for i in infos]
    return _run_stitch(path, infos, names)


def classify_workbook(path):
    """워크북이 스티칭 대상(밴드/시퀀스 다중시트)인지 자동 판정. UI 자동제안용.
    반환: {stitchable: bool, mode: band|seq|passthrough, sheets:[{name,role}]}"""
    wb = load_workbook(path, read_only=True)
    names = [s for s in wb.sheetnames if not s.startswith("_")]
    wb.close()
    roles = []
    for sh in names:
        sug = suggest_column_mapping(path, sh)
        recs, meta = _read_records(path, sh, sug["mapping"], sug["header_row"])
        roles.append((sh, _classify_sheet(recs, meta)))
    rset = [r for _, r in roles]
    if "seq_leaf" in rset and len(names) > 1:
        mode = "seq"
    elif rset.count("band") >= 1 and "leaf" in rset:
        mode = "band"
    else:
        mode = "passthrough"
    return {"stitchable": mode in ("band", "seq"), "mode": mode,
            "sheets": [{"name": n, "role": r} for n, r in roles]}


def stitch_workbook(path, only_sheets=None):
    """엑셀 워크북 하나를 통합 잎 데이터셋으로 조립(매핑 자동제안).

    only_sheets: 포함할 시트명 리스트(None이면 '_' 접두 제외한 전체). 갑지·설명 시트를
    빼고 데이터 시트만 골라 넘길 수 있다.
    반환: {mode, items, residuals, sheets, totals}
    """
    wb = load_workbook(path, read_only=True)
    sheet_names = [s for s in wb.sheetnames if not s.startswith("_")]
    wb.close()
    if only_sheets:
        want = [s for s in only_sheets if s in sheet_names]
        if want:
            sheet_names = want
    infos = []
    for sh in sheet_names:
        sug = suggest_column_mapping(path, sh)
        infos.append({"name": sh, "mapping": sug["mapping"], "header_row": sug["header_row"]})
    return _run_stitch(path, infos, sheet_names)
