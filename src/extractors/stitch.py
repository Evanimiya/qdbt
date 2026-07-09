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
    is_total_label, CAT_ROLES, PATH_SEP,
)
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
            for sname in summary_sheets:
                srows = [it for it in items
                         if it.get("_sheet") == sname and not it.get("merge_status")]
                pend = []
                for s in srows:
                    amt = float(s.get("amount") or 0)
                    if not amt:
                        continue
                    key = _norm(s.get("name_normalized"))
                    for c, tot in cat_total.items():
                        if c and (c == key or c in key or key in c) and \
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
    info = {r: col for col, r in mapping.items() if r in ("qty", "unit", "price", "amount", "spec", "maker", "part")}

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
            rec["seq"] = str(v).strip() if v not in (None, "") else None
        for role, col in info.items():
            v = cv(r, col)
            rec[role] = _to_number(v) if role in ("qty", "price", "amount") else (
                str(v).strip() if v not in (None, "") else None)
        recs.append(rec)
    wb.close()
    meta = {"levels": sorted(catcols), "has_name": name_col is not None,
            "has_seq": seq_col is not None, "info": sorted(info)}
    return recs, meta


def _is_total_row(rec, levels):
    """총계/소계 행 판정 — 정밀 경계 매칭(부분문자열 오탐 방지). [C.i]"""
    txt = " ".join(str(rec.get(k) or "") for k in (["name"] + levels)).strip()
    return is_total_label(txt)


def _classify_sheet(recs, meta):
    """시트 역할: 'leaf'(품목 보유) / 'band'(분류만) / 'seq_tree' / 'seq_list'."""
    dotted = meta["has_seq"] and any(
        re.match(r"^\d+\.\d+", str(r.get("seq") or "")) for r in recs)
    has_item = meta["has_name"] and any(
        (r.get("price") or r.get("qty")) for r in recs)
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
            items.append(item)
            if not matched:
                residuals.append({
                    "reason": "ambiguous_join_key" if ambiguous else "skeleton_unmatched",
                    "leaf_key": r.get(leaf_top), "name": r.get("name"),
                    "assigned_path": item["path"], "row": r["row"]})
    return items, residuals, "band", sorted(cand_paths)


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


def _stitch_seq(sheets):
    seqmap = {}       # 번호 prefix -> 이름
    for recs, m, role in sheets:
        if role in ("seq_list", "seq_tree", "seq_leaf"):
            for r in recs:
                sq = str(r.get("seq") or "").strip()
                nm = _rec_name(r)
                if sq and re.match(r"^\d+(\.\d+)*$", sq) and nm and not _is_total_row(r, []):
                    seqmap.setdefault(sq, nm)
    items, residuals = [], []
    leaf = next((recs for recs, m, role in sheets if role == "seq_leaf"), None)
    if leaf is None:
        return items, residuals, "seq(no-leaf)", []
    for r in leaf:
        sq = str(r.get("seq") or "").strip()
        nm = r.get("name")
        if not nm or _is_total_row(r, []):
            continue
        parts = []
        if re.match(r"^\d+(\.\d+)*$", sq):
            p = sq.split(".")
            for d in range(1, len(p)):
                prefix = ".".join(p[:d])
                if prefix in seqmap:
                    parts.append(seqmap[prefix])
            parts.append(nm)
        else:
            parts = [nm]
        # 부모 이름을 하나도 못 찾았으면(잎만) residual
        matched = len(parts) > 1 or not re.match(r"^\d+\.\d+", sq)
        _leaf = r.get("part") or nm   # [부품] 부품 있으면 잎=부품(경로 끝은 이미 품목)
        items.append({
            "path": PATH_SEP.join(parts), "depth": len(parts),
            "name_normalized": _leaf, "spec": r.get("spec"), "maker": r.get("maker"),
            "quantity": r.get("qty"), "unit": r.get("unit"),
            "unit_price": r.get("price"), "amount": r.get("amount"),
            "line_no": f"R{r['row']}", "_matched": matched,
        })
        if not matched:
            residuals.append({"reason": "seq_parent_missing", "seq": sq, "name": nm, "row": r["row"]})
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
        # 경로: 존재값 + (필요 시) 행간 상속(best-effort, 사용성 위해)
        parts = []
        for lv in levels:
            v = r.get(lv)
            if v:
                last_cat[lv] = v
            else:
                v = last_cat.get(lv)
            if v:
                parts.append(v)
        matched = complete
        _pp, _ln = _part_promote(r, PATH_SEP.join(parts), _leafnm)
        item = {
            "path": _pp, "depth": len([x for x in _pp.split(PATH_SEP) if x]),
            "name_normalized": _ln, "spec": r.get("spec"), "maker": r.get("maker"),
            "quantity": r.get("qty"), "unit": r.get("unit"),
            "unit_price": r.get("price"), "amount": r.get("amount"),
            "line_no": f"R{r['row']}", "_matched": matched, "_top_level": top_level,
        }
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

    for sh, its in by_sheet.items():
        # 이 시트 최상위 분류의 의미 레벨(대=1/중=2/…). 짧은 시트 판정용.
        sheet_top = min((it.get("_top_level", 1) for it in its), default=1)
        # 이 시트의 루트 값들(norm→표시값)
        roots = {}
        for it in its:
            parts = _path_parts(it.get("path"))
            if parts:
                roots.setdefault(_norm(parts[0]), parts[0])
        for rnorm in roots:
            # 다른 시트에서 같은 이름의 '내부 분류 노드' 조상경로 후보
            prefixes = {pre for (s, pre) in interior.get(rnorm, set()) if s != sh and pre}
            if len(prefixes) == 1:
                # 유일 매칭 → 조상 경로 아래로 접합(의미 레벨 자동 정렬)
                prefix = next(iter(prefixes))
                for it in its:
                    parts = _path_parts(it.get("path"))
                    if parts and _norm(parts[0]) == rnorm:
                        it["path"] = prefix + PATH_SEP + it["path"]
                        it["depth"] = len(_path_parts(it["path"]))
                        it["category"] = _path_parts(it["path"])[0]   # 최상위 분류 갱신
            elif sheet_top > 1:
                # [레벨 정렬 픽스] 매칭 실패 + 짧은 시트(최상위가 대분류가 아님):
                #  누락된 상위 레벨(1..top-1)만큼 placeholder를 앞에 붙여 '의미 레벨'을
                #  보존한다 → 중분류가 대분류(root)로 승격되지 않고 정확한 depth에 놓임.
                #  해당 잎은 residual(미연계)로 표기 → 사용자가 캔버스에서 올바른 대분류
                #  하위로 드래그(가지 재부모화)하면 placeholder가 벗겨지며 연결된다.
                pad = [_LEVEL_PLACEHOLDER.get(lv, _LEVEL_PLACEHOLDER_DEFAULT)
                       for lv in range(1, sheet_top)]
                pre = PATH_SEP.join(pad)
                for it in its:
                    parts = _path_parts(it.get("path"))
                    if parts and _norm(parts[0]) == rnorm:
                        it["path"] = pre + PATH_SEP + it["path"]
                        it["depth"] = len(_path_parts(it["path"]))
                        it["category"] = pad[0]
                        it["_level_residual"] = True   # 상위 미매칭 → 미연계 표기
            # else: 매칭 없음이나 이미 대분류 시작(top==1) → 보존(기존 동작)
    return items


def _finalize_items(items):
    """insert_items_bulk 호환 필드 보강: category, name_raw."""
    for it in items:
        segs = (it.get("path") or "").split(PATH_SEP)
        it.setdefault("category", segs[0] if segs and segs[0] else "기타")
        it.setdefault("name_raw", it.get("name_normalized"))
        it.setdefault("is_category_header", False)
    return items


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
        items, residuals, mode, candidates = _stitch_seq(sheets)
        _leafname = next((info["name"] for (_, _, r), info in zip(sheets, infos)
                          if r == "seq_leaf"), None)
        for x in items:
            x.setdefault("_sheet", _leafname)
    elif len([r for r in roles if r == "band"]) >= 1 and "leaf" in roles:
        items, residuals, mode, candidates = _stitch_band(sheets)
        _leafname = next((info["name"] for (_, _, r), info in zip(sheets, infos)
                          if r == "leaf"), None)
        for x in items:
            x.setdefault("_sheet", _leafname)
    else:
        # 단일(또는 밴드/시퀀스 아님) → leaf 시트 passthrough 병합
        items, residuals, mode = [], [], "passthrough"
        cset = set()
        for (recs, meta, role), info in zip(sheets, infos):
            if role == "leaf" or len(sheet_names) == 1:
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

    # 총액·건수는 병합 제외행(merge_status)을 뺀 정본 잎 기준.
    leaf_total = sum(it["amount"] for it in items
                     if it.get("amount") and not it.get("merge_status"))
    n_dropped = reconciliation["duplicates"] + len(reconciliation["rollups"])
    n_kept = sum(1 for it in items if not it.get("merge_status"))
    return {
        "mode": mode, "items": items, "n_items": n_kept,
        "residuals": residuals, "n_residuals": len(residuals),
        "candidates": candidates,
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
