# -*- coding: utf-8 -*-
"""
코드 기반 추출 엔진 (extract_by_mapping)

LLM 없이, 확인된 열 매핑으로 엑셀을 결정론적으로 추출한다.
- 열 역할(대/중/소/세분류, 품목명, 규격, 수량, 단위, 단가, 금액, 비고)을 받아
- 셀 병합 풀기 + 빈칸 상속을 코드로 처리
- 분류 열을 조립해 path 구성 (구분자 " > " 통일)
- 분류(path)와 정보(규격/단가 등)를 역할로 분리

설계: docs/QDBT_추출아키텍처_재설계.md
"""
from openpyxl import load_workbook

# 분류 역할 (순서대로 path 구성)
CAT_ROLES = ["cat1", "cat2", "cat3", "cat4", "cat5"]
# 정보 역할
INFO_ROLES = {
    "name": "name_normalized",
    "spec": "spec",
    "qty": "quantity",
    "unit": "unit",
    "price": "unit_price",       # 단가(통화 기준일 수 있음)
    "amount": "amount",          # 금액(통화 기준일 수 있음)
    "currency": "currency_raw",  # 통화 열 (USD/CNY/KRW 등)
    "price_krw": "unit_price_krw",  # 원화 단가 (있으면 확정 원화)
    "amount_krw": "amount_krw",     # 원화 금액 (있으면 확정 원화)
    "maker": "maker",               # 메이커(제조사/브랜드)
    "remark": "remark",
}

PATH_SEP = " > "


def _build_merge_fill(sheet):
    """병합 영역을 풀어 {(row,col): 채울값} 반환. (parse_xlsx와 동일 로직)"""
    fill = {}
    for m in sheet.merged_cells.ranges:
        top_val = sheet.cell(row=m.min_row, column=m.min_col).value
        if top_val is None:
            continue
        for r in range(m.min_row, m.max_row + 1):
            for c in range(m.min_col, m.max_col + 1):
                if r == m.min_row and c == m.min_col:
                    continue
                fill[(r, c)] = top_val
    return fill


def _to_number(v):
    """숫자 파싱 (콤마/공백/통화기호 제거). 실패 시 None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(",", "").replace(" ", "")
    for sym in ("₩", "원", "$", "USD", "\\"):
        s = s.replace(sym, "")
    if not s:
        return None
    try:
        return float(s) if ("." in s) else int(s)
    except ValueError:
        return None


import re as _re

# [C.i] 합계/소계 '행' 판정 — 부분문자열 오탐 방지(정밀 경계 매칭).
#  · 강한 마커(합계·소계·총계·총액·누계)는 텍스트가 그것으로 '끝날' 때만(예: "재료비 합계").
#    → 품명에 우연히 포함된 경우("소계장치","합계금액표")는 제외되지 않음.
#  · 단독 '계'는 라벨 전체가 '계'/'합 계'/'…  계'일 때만(기존 규칙 유지).
#  · 영문 total/subtotal/grand/sum 은 단어 경계로만 매칭("Summary","consumables"는 불매칭).
_TOTAL_STRONG = ("합계", "소계", "총계", "총액", "누계", "합 계", "소 계", "총 계")
_TOTAL_EN_RE = _re.compile(r"(?<![a-z])(sub[\s-]*total|grand[\s-]*total|total|subtotal|grand|sum)(?![a-z])")


def is_total_label(text):
    """한 행의 텍스트(품명+분류 등)를 받아 '합계/소계/총계 행'이면 True."""
    if not text:
        return False
    t = str(text).strip().lower()
    if not t:
        return False
    nospace = t.replace(" ", "")
    # 강한 한글 마커로 '끝나는' 라벨 (예: "재료비 합계","소계","총 계")
    for kw in _TOTAL_STRONG:
        if nospace.endswith(kw.replace(" ", "")):
            return True
    # 단독 '계'만(예: "계","합 계","… 계"). '설계·통계·회계' 등은 불매칭.
    if t == "계" or t.endswith(" 계"):
        return True
    # 영문 total 계열(단어 경계) — "Summary","consumables"는 불매칭.
    if _TOTAL_EN_RE.search(t):
        return True
    return False


def read_grid(path, sheet_name, max_rows=None, max_cols=None):
    """엑셀 시트를 화면 표시용 격자(2차원 리스트)로 읽는다.

    인터랙티브 열 매핑 화면에서 엑셀을 그대로 보여주기 위함.
    병합 복원을 적용해 빈 병합 칸도 값이 채워진 상태로 표시.
    행/열 수는 시트 실제 크기를 따른다 (max_rows/max_cols로 상한만 선택적 지정).

    반환: {
      "grid": [[셀값, ...], ...],   # 행×열 문자열
      "n_rows": int, "n_cols": int,
      "merges": [["A1","B2"], ...],  # 병합 범위 (참고용)
    }
    """
    wb = load_workbook(path, data_only=True)
    sheet = wb[sheet_name] if sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]
    merge_fill = _build_merge_fill(sheet)

    # 시트 실제 크기를 사용. max_rows/max_cols가 주어지면 상한으로만 적용.
    n_rows = sheet.max_row or 0
    n_cols = sheet.max_column or 0
    if max_rows:
        n_rows = min(n_rows, max_rows)
    if max_cols:
        n_cols = min(n_cols, max_cols)

    grid = []
    for r in range(1, n_rows + 1):
        row = []
        for c in range(1, n_cols + 1):
            v = sheet.cell(row=r, column=c).value
            if (v is None or str(v).strip() == "") and (r, c) in merge_fill:
                v = merge_fill[(r, c)]
            row.append("" if v is None else str(v))
        grid.append(row)

    merges = [[str(m.coord).split(":")[0], str(m.coord).split(":")[-1]]
              for m in sheet.merged_cells.ranges]

    wb.close()
    return {"grid": grid, "n_rows": n_rows, "n_cols": n_cols, "merges": merges}


def extract_by_mapping(path, sheet_name, column_mapping, header_row,
                       fill_down_categories=True, excluded_rows=None,
                       nego_rows=None):
    """확인된 열 매핑으로 엑셀 시트를 코드로 추출한다.

    인자:
      path: xlsx 파일 경로
      sheet_name: 시트명
      column_mapping: {열번호(1-based): 역할}
          역할: cat1~cat5(분류), name, spec, qty, unit, price, amount, remark, ignore
      header_row: 헤더 행 번호 (1-based). 이 다음 행부터 데이터.
      fill_down_categories: 분류 빈칸을 위 행에서 상속(코드 빈칸채우기). 기본 True.

    반환: {
      "items": [
        {"path": "재료비 > 기구부 > 차폐", "depth": 3,
         "name_normalized": "납 BLOCK", "spec": "순납",
         "quantity": 150, "unit": "EA", "unit_price": 64000,
         "amount": 9600000, "category": "재료비", "line_no": "R8",
         "is_category_header": False},
        ...
      ],
      "n_items": int,
      "warnings": [...],
    }
    """
    wb = load_workbook(path, data_only=True)
    if sheet_name not in wb.sheetnames:
        wb.close()
        raise ValueError(f"시트 '{sheet_name}' 없음")
    sheet = wb[sheet_name]

    merge_fill = _build_merge_fill(sheet)

    def cell_val(r, c):
        """병합 복원 적용한 셀 값."""
        v = sheet.cell(row=r, column=c).value
        if (v is None or str(v).strip() == "") and (r, c) in merge_fill:
            return merge_fill[(r, c)]
        return v

    # 분류 열들 (cat1, cat2... 순)
    cat_cols = []
    for role in CAT_ROLES:
        col = next((c for c, r in column_mapping.items() if r == role), None)
        if col:
            cat_cols.append((role, col))
    # 정보 열들
    info_cols = {}
    for role, field in INFO_ROLES.items():
        col = next((c for c, r in column_mapping.items() if r == role), None)
        if col:
            info_cols[field] = col
    # [품명 없는 시트] 품목명(name) 역할이 아예 매핑되지 않았는지. True면 '가장 깊은
    #  분류값'을 잎(품목)으로 삼아 집계에 포함한다(_stitch_passthrough와 동일 규칙).
    #  ※ 품명 열이 있는데 특정 행만 비어있는 경우(진짜 소계행)와 구분하기 위한 플래그.
    has_name_col = "name_normalized" in info_cols

    # 번호(seq) 열: "1", "1.1", "1.1.2" 패턴으로 계층 구성 (분류명 = 그 행 품목명)
    seq_col = next((c for c, r in column_mapping.items() if r == "seq"), None)
    # [BUG A 수정] seq 모드는 seq 열에 '점 계층'(예: 1.1)이 실제로 존재할 때만 발동.
    #  단순 행번호("No" 1,2,3…)가 seq로 매핑돼도, 분류(cat) 열이 있으면 계층을 버리지 않도록.
    #  '추측 말고 실제 데이터를 보라' — 데이터를 미리 스캔해 결정.
    if seq_col is not None:
        import re as _re_seq
        _has_dotted = False
        for _r in range(header_row + 1, sheet.max_row + 1):
            _v = cell_val(_r, seq_col)
            if _v is not None and _re_seq.match(r"^\d+\.\d+", str(_v).strip()):
                _has_dotted = True
                break
        _has_cat = any(role in column_mapping.values() for role in CAT_ROLES)
        # 점 계층이 없고 분류 열이 따로 있으면 → seq는 단순 행번호. seq 모드 비활성.
        if not _has_dotted and _has_cat:
            seq_col = None

    # [부품] '부품' 열이 지정되면: 품목(name)을 분류 경로의 한 단계로 내리고, 부품을 잎으로.
    #  → 한 품목에 부품 0..N. 부품 없으면 품목이 잎(기존과 동일). 스키마 무변경(path만 깊어짐).
    part_col = next((c for c, r in column_mapping.items() if r == "part"), None)

    items = []
    warnings = []
    excluded_rows = excluded_rows or set()
    nego_rows = nego_rows or set()
    # 분류 빈칸 상속용 (이전 행의 분류 값 기억)
    last_cat = {}  # role -> 마지막 값
    # 번호 계층용: 번호 → 그 행의 분류명 (예: "1" → "MATERIAL")
    seq_names = {}  # seq prefix -> name

    for r in range(header_row + 1, sheet.max_row + 1):
        # 사용자가 제외한 행 (subtotal 등) 스킵
        if r in excluded_rows:
            continue
        # 행 전체가 비었으면 스킵
        row_vals = [sheet.cell(row=r, column=c).value for c in range(1, sheet.max_column + 1)]
        if not any(v is not None and str(v).strip() for v in row_vals):
            continue

        # ── 번호(seq) 모드: 번호 패턴으로 계층 구성 ──
        if seq_col is not None:
            seq_raw = cell_val(r, seq_col)
            seq = str(seq_raw).strip() if seq_raw is not None else ""
            # 품목명/설명 (이 행의 이름)
            name_col = info_cols.get("name_normalized")
            row_name = ""
            if name_col:
                nv = cell_val(r, name_col)
                row_name = str(nv).strip() if nv is not None else ""

            # 번호가 숫자.숫자 패턴인지 (예: 1, 1.1, 1.1.2)
            import re as _re2
            if seq and _re2.match(r"^\d+(\.\d+)*$", seq):
                seq_parts = seq.split(".")
                if len(seq_parts) == 1:
                    # 최상위 번호 (예: "1") → 대분류 행. 이름을 기억하고 항목으론 스킵.
                    seq_names = {k: v for k, v in seq_names.items()}  # keep
                    seq_names[seq] = row_name or seq
                    # 대분류 행 자체는 항목 아님 (금액 없으면), 건너뜀
                    amt_col = info_cols.get("amount")
                    amt_val = _to_number(cell_val(r, amt_col)) if amt_col else None
                    if not amt_val:
                        continue  # 분류 헤더 행 → 스킵 (이름만 기억)
                    # 금액이 있으면 그대로 항목 처리 (path = 자기 이름)
                    parts = [row_name or seq]
                else:
                    # 하위 번호 (예: "1.1") → 부모들의 이름 + 자기 이름
                    parts = []
                    for d in range(1, len(seq_parts)):
                        prefix = ".".join(seq_parts[:d])
                        pname = seq_names.get(prefix, prefix)
                        parts.append(pname)
                    parts.append(row_name or seq)
            else:
                # 번호 패턴 아님 — 총계/소계 행 가능성 검사
                amt_col2 = info_cols.get("amount")
                amt2 = _to_number(cell_val(r, amt_col2)) if amt_col2 else None
                # seq 셀에 텍스트가 있으면(병합 총계행 등) 그것도 이름 후보로
                _check = ((row_name or "") + " " + (seq or "")).strip()
                is_total = is_total_label(_check)   # [C.i] 정밀 경계 매칭
                if amt2 and (is_total or (not seq and not row_name)):
                    continue  # 총계/소계거나 번호·이름 없이 금액만 → 스킵
                parts = [row_name] if row_name else []

            path_str = PATH_SEP.join(parts)
        else:
            # ── 기존 cat 열 모드 ──
            parts = []
            for role, col in cat_cols:
                v = cell_val(r, col)
                v = str(v).strip() if v is not None else ""
                if not v and fill_down_categories:
                    v = last_cat.get(role, "")
                if not v and parts:
                    v = parts[-1]
                if v:
                    last_cat[role] = v
                    parts.append(v)
                else:
                    if not fill_down_categories:
                        last_cat[role] = ""
            path_str = PATH_SEP.join(parts)

        # 정보 추출
        item = {
            "path": path_str,
            "depth": len(parts),
            "category": parts[0] if parts else None,
            "line_no": f"R{r}",
            "is_category_header": False,
        }
        for field, col in info_cols.items():
            v = cell_val(r, col)
            if field in ("quantity", "unit_price", "amount",
                         "unit_price_krw", "amount_krw"):
                item[field] = _to_number(v)
            else:
                item[field] = str(v).strip() if v is not None else None

        # ── 통화 처리 ─────────────────────────────
        # 규칙:
        #  1) 원화 열(amount_krw/price_krw)이 지정돼 있으면 그 값을 '원화 확정'으로 사용
        #  2) 통화 열(currency)에서 통화 코드(USD/CNY/KRW 등)를 읽음
        #  3) 통화·원화가 모두 있으면 환율 역산: fx = 원화 ÷ 통화
        #  4) 결과를 표준 필드로 정규화:
        #       unit_price(원화 단가), amount(원화 금액),
        #       unit_price_orig(원본 통화 단가), unit_price_currency_in_source(통화),
        #       fx_rate_used(역산 환율)
        cur_raw = (item.pop("currency_raw", None) or "").strip().upper()
        # 통화 코드 정규화 (기호·명칭 → ISO)
        _CUR_MAP = {
            "$": "USD", "US$": "USD", "USD": "USD", "달러": "USD",
            "¥": "CNY", "RMB": "CNY", "CNY": "CNY", "위안": "CNY", "元": "CNY",
            "€": "EUR", "EUR": "EUR", "유로": "EUR",
            "￦": "KRW", "₩": "KRW", "KRW": "KRW", "원": "KRW", "WON": "KRW",
            "JPY": "JPY", "엔": "JPY",
        }
        currency = _CUR_MAP.get(cur_raw, cur_raw if cur_raw else "KRW")

        amt_cur = item.get("amount")            # 통화 기준 금액(또는 원화)
        amt_krw = item.pop("amount_krw", None)  # 원화 금액(있으면 확정)
        price_cur = item.get("unit_price")
        price_krw = item.pop("unit_price_krw", None)

        fx = None
        # 환율 역산: 원화 ÷ 통화 (금액 우선, 없으면 단가)
        if amt_krw and amt_cur and amt_cur != 0 and currency != "KRW":
            fx = round(abs(amt_krw) / abs(amt_cur), 4)
        elif price_krw and price_cur and price_cur != 0 and currency != "KRW":
            fx = round(abs(price_krw) / abs(price_cur), 4)

        if currency != "KRW":
            # 외화 항목: 원본 통화값 보존 + 원화 확정값 산출
            # [T2] 원통화 값을 항상 보존 — 나중 환율 수정 시 역산 복원(누적 오차) 회피.
            item["unit_price_orig"] = price_cur          # 원본 통화 단가(없으면 None)
            item["amount_orig"] = amt_cur                # 원본 통화 금액(단가 없는 행 대비)
            item["unit_price_currency_in_source"] = currency
            item["fx_rate_used"] = fx
            # 원화 확정: 견적서 원화열 우선, 없으면 통화×환율
            if amt_krw is not None:
                item["amount"] = amt_krw
            elif fx and amt_cur is not None:
                item["amount"] = round(amt_cur * fx)
            if price_krw is not None:
                item["unit_price"] = price_krw
            elif fx and price_cur is not None:
                item["unit_price"] = round(price_cur * fx)
        else:
            # 원화 항목: 원화열이 별도로 있으면 그 값 우선
            item["unit_price_currency_in_source"] = "KRW"
            if amt_krw is not None:
                item["amount"] = amt_krw
            if price_krw is not None:
                item["unit_price"] = price_krw

        # 차감(special nego) 행: 금액·단가를 음수로. 절댓값으로 들어와도 차감 반영.
        is_nego = (r in nego_rows)
        item["is_nego"] = is_nego
        if is_nego:
            for f in ("amount", "unit_price"):
                if item.get(f) is not None:
                    item[f] = -abs(item[f])

        # [부품] 승격: 부품 값이 있으면 품목(name)을 분류 경로로 내리고 부품을 잎으로.
        if part_col is not None:
            _pv = cell_val(r, part_col)
            _pv = str(_pv).strip() if _pv is not None else ""
            if _pv:
                _poem = item.get("name_normalized")   # 품목
                if _poem:
                    _last = item["path"].split(PATH_SEP)[-1] if item.get("path") else None
                    if _poem != _last:   # 세분류와 품목이 동일하면 중복 방지
                        item["path"] = (item["path"] + PATH_SEP + _poem) if item.get("path") else _poem
                        item["depth"] = (item.get("depth") or 0) + 1
                    if not item.get("category"):
                        item["category"] = (item["path"].split(PATH_SEP)[0]) if item.get("path") else _poem
                item["name_normalized"] = _pv
                item["name_raw"] = _pv

        # 품목명이 없으면 — 분류 헤더 행이거나 빈 행일 수 있음
        name = item.get("name_normalized")
        if not name:
            # [품명 없는 시트] 품명 열 자체가 없으면 '가장 깊은 분류값'을 잎(품목)으로
            #  삼아 정상 집계(is_category_header=False). 중/소분류까지만 기입된 견적서도
            #  그 분류가 곧 항목이 되도록 — 추출 0/총액 0 방지(_stitch_passthrough와 동일).
            if not has_name_col and item.get("amount") and parts:
                item["name_normalized"] = parts[-1]
            # 품명 열은 있는데 이 행만 비었고 금액만 있으면 소계 가능성 → category_header 표시
            elif item.get("amount"):
                item["is_category_header"] = True
                item["name_normalized"] = parts[-1] if parts else "(소계)"
            else:
                continue  # 품목명도 금액도 없으면 스킵

        # name_raw도 채움 (없으면 normalized와 동일)
        item.setdefault("name_raw", item.get("name_normalized"))

        # 분류가 하나도 없으면(path 비어있음) 품목명을 path로.
        # 분류 없이 품목명만 있는 견적서 → 각 품목이 곧 최상위 항목.
        if not item.get("path"):
            nm = item.get("name_normalized") or item.get("name_raw") or ""
            if nm:
                item["path"] = nm
                item["depth"] = 1
                if not item.get("category"):
                    item["category"] = "기타"

        items.append(item)

    wb.close()
    return {
        "items": items,
        "n_items": len(items),
        "warnings": warnings,
    }


def suggest_column_mapping(path, sheet_name, max_scan_rows=8):
    """헤더 행을 코드로 1차 탐지해 열 매핑 후보를 제안한다.

    (LLM 헤더 인식의 코드 폴백 / 초기값. 키워드 매칭 기반.)
    반환: {"header_row": int, "mapping": {col: role}, "confidence": str}
    """
    wb = load_workbook(path, data_only=True)
    sheet = wb[sheet_name] if sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]

    # 역할별 헤더 키워드
    KW = {
        "seq": ["no.", "no", "번호", "순번", "항번", "item no"],
        "cat1": ["대분류"], "cat2": ["중분류"], "cat3": ["소분류"],
        "cat4": ["세분류", "세세분류"],
        "name": ["품명", "품목", "주요구성품", "주요부품", "name", "item",
                 "공종명", "항목명", "내역명", "공사명"],
        "spec": ["규격", "사양", "spec", "remark주요"],
        "maker": ["메이커", "제조사", "제조원", "브랜드", "maker", "manufacturer", "mfr", "make", "brand"],
        "part": ["부품", "부속품", "부속", "구성품", "구성부품", "세부품목", "part", "component"],
        "qty": ["수량", "q'ty", "qty", "수 량"],
        "unit": ["단위", "unit"],
        "currency": ["ccy", "통화", "currency", "화폐"],
        "price_krw": ["원화단가", "krw단가", "단가(krw)", "단가(원)"],
        "amount_krw": ["원화금액", "krw금액", "amount(krw)", "amount (krw)",
                       "금액(krw)", "금액(원)", "공급가액(원)"],
        "price": ["단가", "unit price", "unitprice"],
        "amount": ["금액", "amount", "total", "공급가", "합계금액"],
        "remark": ["비고", "remark", "remarks"],
    }

    ROLE_PRIORITY = ["seq", "amount_krw", "price_krw", "currency",
                     "cat1", "cat2", "cat3", "cat4", "name", "part", "spec", "maker",
                     "qty", "unit", "price", "amount", "remark"]

    best_row, best_map, best_hits = None, {}, 0
    for hr in range(1, min(sheet.max_row, max_scan_rows) + 1):
        mapping = {}
        for c in range(1, sheet.max_column + 1):
            v = sheet.cell(row=hr, column=c).value
            if not v:
                continue
            vs = str(v).strip().lower()
            for role in ROLE_PRIORITY:
                kws = KW.get(role, [])
                if any(kw.lower() in vs for kw in kws):
                    if role == "unit" and "price" in vs:
                        continue
                    # currency는 순수 통화 열만. "amount(ccy)"·"금액"이 섞이면 금액으로.
                    if role == "currency" and ("amount" in vs or "금액" in vs
                                               or "price" in vs or "단가" in vs):
                        continue
                    if role == "amount" and ("krw" in vs or "원" in vs):
                        continue
                    if role == "price" and ("krw" in vs or "원화" in vs):
                        continue
                    mapping[c] = role
                    break
        if len(mapping) > best_hits:
            best_hits = len(mapping)
            best_row = hr
            best_map = mapping

    wb.close()
    conf = "high" if best_hits >= 4 else ("low" if best_hits >= 2 else "none")
    return {"header_row": best_row or 1, "mapping": best_map, "confidence": conf}


def detect_total_rows(path, sheet_name, mapping: dict, header_row: int = 1):
    """합계·소계·총계로 의심되는 행을 감지해 목록으로 반환.

    자동 삭제가 아니라 '제외 후보 제안'용. UI가 이 목록을 기본 선택(제외 예정)
    상태로 표시하고, 사용자가 확인/해제 후 최종 반영한다.

    판별 기준(보수적: 오탐 시 사용자가 해제 가능):
      ① 이름/번호 셀에 합계 키워드(합계·소계·총계·계·total·subtotal·grand·sum) 포함
      ② 금액은 있으나 품명·번호가 비어 있고, 값이 병합/굵게 등 요약 성격
    반환: [{"row": int, "reason": str, "name": str, "amount": str}]
    """
    wb = load_workbook(path, data_only=True)
    sheet = wb[sheet_name] if sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]

    # 역할→열 역매핑
    role_col = {}
    for col, role in (mapping or {}).items():
        role_col.setdefault(role, int(col))
    name_col = role_col.get("name")
    seq_col = role_col.get("seq")
    amount_col = role_col.get("amount")
    price_col = role_col.get("price")
    # 이름 후보 열(품명 없으면 cat 계열/첫 텍스트 열로 폴백)
    text_cols = [role_col[r] for r in ("name", "seq", "cat1", "cat2", "cat3", "cat4")
                 if r in role_col]

    TOTAL_KW = ("합계", "소계", "총계", "총 계", "총액", "계",
                "total", "subtotal", "sub-total", "grand", "sum")

    def _norm(v):
        return "" if v is None else str(v).strip()

    detected = []
    for r in range(header_row + 1, sheet.max_row + 1):
        # 행의 텍스트 후보(이름/번호/분류) 모으기
        texts = [_norm(sheet.cell(row=r, column=c).value) for c in text_cols]
        joined = " ".join(t for t in texts if t).lower()
        amt_raw = _norm(sheet.cell(row=r, column=amount_col).value) if amount_col else ""
        amt_num = _to_number(amt_raw) if amt_raw else None
        name_val = _norm(sheet.cell(row=r, column=name_col).value) if name_col else ""

        # 완전 빈 행 스킵
        if not joined and amt_num is None:
            continue

        reason = None
        # ① 합계 키워드 — 정밀 경계 매칭(부분문자열 오탐 방지). [C.i]
        if is_total_label(joined):
            reason = "합계·소계 키워드"
        # ② 품명·번호 비었는데 금액만 있는 행(요약행 성격)
        elif amt_num is not None and not name_val and not (
                seq_col and _norm(sheet.cell(row=r, column=seq_col).value)):
            reason = "품명·번호 없이 금액만"
        # ③ [C.ii] 분류/품명은 있으나 금액과 단가가 모두 비어 있는 행 → 비교 대상 제외 후보
        elif name_val and amt_num is None and (
                _to_number(_norm(sheet.cell(row=r, column=price_col).value)) is None
                if price_col else True):
            reason = "금액·단가 없음"

        if reason:
            detected.append({
                "row": r,
                "reason": reason,
                "name": name_val or joined[:40],
                "amount": amt_raw,
            })

    wb.close()
    return detected
    """헤더 행을 코드로 1차 탐지해 열 매핑 후보를 제안한다.

    (LLM 헤더 인식의 코드 폴백 / 초기값. 키워드 매칭 기반.)
    반환: {"header_row": int, "mapping": {col: role}, "confidence": str}
    """
    wb = load_workbook(path, data_only=True)
    sheet = wb[sheet_name] if sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]

    # 역할별 헤더 키워드
    KW = {
        "seq": ["no.", "no", "번호", "순번", "항번", "item no"],
        "cat1": ["대분류"], "cat2": ["중분류"], "cat3": ["소분류"],
        "cat4": ["세분류", "세세분류"],
        "name": ["품명", "품목", "주요구성품", "주요부품", "name", "item",
                 "공종명", "항목명", "내역명", "공사명"],
        "spec": ["규격", "사양", "spec", "remark주요"],
        "maker": ["메이커", "제조사", "제조원", "브랜드", "maker", "manufacturer", "mfr", "make", "brand"],
        "part": ["부품", "부속품", "부속", "구성품", "구성부품", "세부품목", "part", "component"],
        "qty": ["수량", "q'ty", "qty", "수 량"],
        "unit": ["단위", "unit"],
        "price": ["단가", "unit price", "unitprice"],
        "amount": ["금액", "amount", "total", "공급가", "합계금액"],
        "remark": ["비고", "remark", "remarks"],
    }

    best_row, best_map, best_hits = None, {}, 0
    for hr in range(1, min(sheet.max_row, max_scan_rows) + 1):
        mapping = {}
        for c in range(1, sheet.max_column + 1):
            v = sheet.cell(row=hr, column=c).value
            if not v:
                continue
            vs = str(v).strip().lower()
            for role, kws in KW.items():
                if any(kw.lower() in vs for kw in kws):
                    # "unit price"는 unit이 아니라 price로 (price 우선)
                    if role == "unit" and "price" in vs:
                        continue
                    mapping[c] = role
                    break
        if len(mapping) > best_hits:
            best_hits = len(mapping)
            best_row = hr
            best_map = mapping

    wb.close()
    conf = "high" if best_hits >= 4 else ("low" if best_hits >= 2 else "none")
    return {"header_row": best_row or 1, "mapping": best_map, "confidence": conf}
