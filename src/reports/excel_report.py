"""
입찰 내 N개사 비교 Excel 보고서 생성.

compare_bid_submissions()의 결과를 받아 생성.
"""
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.styles.differential import DifferentialStyle
from openpyxl.formatting import Rule
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH

OUTPUT_DIR = DB_PATH.parent
NAVY  = PatternFill("solid", start_color="1A3A5C")
BLUE  = PatternFill("solid", start_color="2E5C8A")
GREY  = PatternFill("solid", start_color="F3F4F6")
YELL  = PatternFill("solid", start_color="FFF9C4")
WF = Font(name="맑은 고딕", size=10, bold=True, color="FFFFFF")
NF = Font(name="맑은 고딕", size=10)
SF = Font(name="맑은 고딕", size=9)
BF = Font(name="맑은 고딕", size=10, bold=True)
THIN = Side(border_style="thin", color="CCCCCC")
BOX  = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
C    = Alignment(horizontal="center", vertical="center", wrap_text=True)
R    = Alignment(horizontal="right",  vertical="center")
L    = Alignment(horizontal="left",   vertical="center", wrap_text=True)


def _h(cell, text="", font=WF, fill=NAVY, align=C):
    cell.value = text
    cell.font  = font
    cell.fill  = fill
    cell.alignment = align
    cell.border = BOX


def _d(cell, value=None, font=NF, align=L, fmt=None):
    cell.value = value
    cell.font  = font
    cell.alignment = align
    cell.border = BOX
    if fmt:
        cell.number_format = fmt


def generate_bid_report(bid, data: dict) -> Path:
    """
    bid: sqlite3.Row  (bid_name, project_name, due_date, ...)
    data: compare_bid_submissions() 반환값
    """
    vendors = data["vendors"]
    n_v = len(vendors)

    wb = Workbook()

    # ─── 시트 1: 요약 ──────────────────────────────
    ws = wb.active
    ws.title = "요약"
    ws.merge_cells(f"A1:{get_column_letter(n_v+2)}1")
    ws["A1"] = f"입찰 비교 보고서  —  {bid['project_name']} / {bid['name']}"
    ws["A1"].font  = Font(name="맑은 고딕", size=16, bold=True, color="FFFFFF")
    ws["A1"].fill  = NAVY
    ws["A1"].alignment = C
    ws.row_dimensions[1].height = 30

    row = 3
    # 공급가액 요약
    for col, v in enumerate(vendors, start=2):
        _h(ws.cell(row, col), v, fill=BLUE)
    ws.cell(row, 1).value = "공급가액 (원)"
    ws.cell(row, 1).font = BF
    ws.cell(row, 1).alignment = L
    ws.cell(row, 1).border = BOX

    row += 1
    prices = []
    for col, v in enumerate(vendors, start=2):
        p = data["subtotals"].get(v)
        _d(ws.cell(row, col), p, align=R, fmt="#,##0")
        if p:
            prices.append((p, col))
    ws.cell(row, 1).value = "합계"
    ws.cell(row, 1).font = BF
    ws.cell(row, 1).border = BOX

    # 최저가 녹색
    if prices:
        min_col = min(prices, key=lambda x: x[0])[1]
        ws.cell(row, min_col).fill = PatternFill("solid", start_color="C6EFCE")

    # 카테고리별 소계
    row += 2
    _h(ws.cell(row, 1), "카테고리", fill=BLUE)
    for col, v in enumerate(vendors, start=2):
        _h(ws.cell(row, col), v, fill=BLUE)
    ws.row_dimensions[row].height = 22

    for cat, rows in data["categories"].items():
        row += 1
        _d(ws.cell(row, 1), cat, font=BF)
        for col, v in enumerate(vendors, start=2):
            total = data["category_totals"].get(v, {}).get(cat, 0)
            _d(ws.cell(row, col), total or None, align=R, fmt="#,##0")

    ws.column_dimensions["A"].width = 14
    for i in range(2, n_v+2):
        ws.column_dimensions[get_column_letter(i)].width = 16


    # ─── 시트 2: 클러스터 세부 항목 ─────────────────
    clusters = data.get("clusters", [])
    if clusters:
        wsc = wb.create_sheet("클러스터")
        wsc.merge_cells(f"A1:{get_column_letter(n_v+4)}1")
        wsc["A1"] = f"클러스터 세부 항목  —  {bid['project_name']} / {bid['name']}"
        wsc["A1"].font  = Font(name="맑은 고딕", size=12, bold=True, color="FFFFFF")
        wsc["A1"].fill  = NAVY
        wsc["A1"].alignment = C
        wsc.row_dimensions[1].height = 24

        hdr = 3
        _h(wsc.cell(hdr, 1), "클러스터",   fill=NAVY, align=L)
        _h(wsc.cell(hdr, 2), "카테고리",   fill=NAVY, align=C)
        _h(wsc.cell(hdr, 3), "품명",       fill=NAVY, align=L)
        for col, v in enumerate(vendors, start=4):
            _h(wsc.cell(hdr, col), v, fill=BLUE, align=C)
        _h(wsc.cell(hdr, n_v+4), "최저가 업체", fill=NAVY, align=C)
        wsc.row_dimensions[hdr].height = 22

        r = hdr
        for cl in clusters:
            cl_name  = cl.get("representative_name", "")
            cl_cat   = cl.get("cat", "")
            groups   = cl.get("groups", [])
            min_v    = cl.get("min_vendor", "")
            status   = cl.get("status", "")

            cl_fill  = PatternFill("solid", start_color="E8F5E9") if status == "accepted" \
                       else PatternFill("solid", start_color="F8FAFC")

            for gi, grp in enumerate(groups):
                r += 1
                # 클러스터명은 첫 행만 표시
                cell_cl = wsc.cell(r, 1)
                if gi == 0:
                    cell_cl.value = cl_name
                    cell_cl.font  = BF
                else:
                    cell_cl.value = ""
                    cell_cl.font  = NF
                cell_cl.alignment = L
                cell_cl.fill    = cl_fill
                cell_cl.border  = BOX

                _d(wsc.cell(r, 2), cl_cat if gi == 0 else "", font=SF, align=C)
                wsc.cell(r, 2).fill   = cl_fill

                _d(wsc.cell(r, 3), grp.get("group_name", ""), font=NF, align=L)
                wsc.cell(r, 3).fill   = cl_fill

                prices = []
                for col, v in enumerate(vendors, start=4):
                    cell_data = grp.get("cells", {}).get(v)
                    # [버그수정] 비교는 금액(amount) 기준. 단가(unit_price)가 아니라
                    # amount를 셀에 넣는다. (화면 비교표도 금액을 비교)
                    amt = cell_data["amount"] if cell_data else None
                    _d(wsc.cell(r, col), amt, align=R, fmt="#,##0")
                    wsc.cell(r, col).fill = cl_fill
                    if amt:
                        prices.append((amt, col, v))

                # 최저가 녹색 강조
                if prices:
                    min_up, min_col, _ = min(prices, key=lambda x: x[0])
                    wsc.cell(r, min_col).fill = PatternFill("solid", start_color="C6EFCE")

                # 최저가 업체명 (클러스터 첫 행만)
                cell_mv = wsc.cell(r, n_v+4)
                if gi == 0 and min_v:
                    cell_mv.value = min_v
                    cell_mv.font  = SF
                    cell_mv.fill  = PatternFill("solid", start_color="C6EFCE")
                else:
                    cell_mv.value = ""
                    cell_mv.fill  = cl_fill
                cell_mv.alignment = C
                cell_mv.border    = BOX

            # 클러스터 구분선 (빈 행)
            r += 1
            for col in range(1, n_v+5):
                wsc.cell(r, col).fill   = PatternFill("solid", start_color="E2E8F0")
                wsc.cell(r, col).border = BOX

        wsc.column_dimensions["A"].width = 22
        wsc.column_dimensions["B"].width = 8
        wsc.column_dimensions["C"].width = 28
        for col in range(4, n_v+4):
            wsc.column_dimensions[get_column_letter(col)].width = 14
        wsc.column_dimensions[get_column_letter(n_v+4)].width = 12
        wsc.freeze_panes = f"D{hdr+1}"

    # ─── 시트 3~N: 업체별 세부 데이터 (각 업체 별도 탭, 테이블 구조) ───
    # 트리가 아니라 전체 항목을 평면 테이블로. 헤더(소계)행 포함 전체 표시.
    for vd in data.get("vendor_details", []):
        vname = vd.get("vendor_name") or "업체"
        # 탭 이름은 31자 제한·중복 방지
        base_title = vname[:28]
        title = base_title
        _dup = 1
        while title in wb.sheetnames:
            _dup += 1
            title = f"{base_title[:26]}_{_dup}"
        wsv = wb.create_sheet(title)

        ncol = 7  # 번호·분류·품명·규격·수량·단가·금액
        wsv.merge_cells(f"A1:{get_column_letter(ncol)}1")
        wsv["A1"] = f"{vname}  세부 내역  —  {bid['project_name']} / {bid['name']}"
        wsv["A1"].font = Font(name="맑은 고딕", size=12, bold=True, color="FFFFFF")
        wsv["A1"].fill = NAVY
        wsv["A1"].alignment = C
        wsv.row_dimensions[1].height = 24

        # 공급가액 표기
        sub_amt = vd.get("subtotal")
        wsv["A2"] = "공급가액(원):"
        wsv["A2"].font = SF
        wsv["B2"] = sub_amt or 0
        wsv["B2"].number_format = "#,##0"
        wsv["B2"].font = BF

        hdr = 4
        headers = ["번호", "분류", "품명", "규격", "수량", "단가(원)", "금액(원)"]
        for col, htext in enumerate(headers, start=1):
            _h(wsv.cell(hdr, col), htext, fill=NAVY,
               align=(L if htext in ("품명", "규격") else C))
        wsv.row_dimensions[hdr].height = 22

        r = hdr
        for it in vd.get("items", []):
            r += 1
            is_header = it.get("is_header")
            _d(wsv.cell(r, 1), it.get("line_no"), align=C, font=SF)
            _d(wsv.cell(r, 2), it.get("category") or "", align=C, font=SF)
            # 품명: 헤더(소계)행은 굵게
            cell_nm = wsv.cell(r, 3)
            cell_nm.value = it.get("name_normalized") or it.get("name_raw") or ""
            cell_nm.font = BF if is_header else NF
            cell_nm.alignment = L
            cell_nm.border = BOX
            _d(wsv.cell(r, 4), it.get("spec") or "", align=L)
            _d(wsv.cell(r, 5), it.get("quantity"), align=R, fmt="#,##0.##")
            _d(wsv.cell(r, 6), it.get("unit_price"), align=R, fmt="#,##0")
            cell_amt = wsv.cell(r, 7)
            _d(cell_amt, it.get("amount"), align=R, fmt="#,##0")
            cell_amt.font = BF if is_header else NF
            # 헤더(소계)행 음영
            if is_header:
                for col in range(1, ncol + 1):
                    wsv.cell(r, col).fill = PatternFill("solid", start_color="EEF2F7")

        # 열 너비
        widths = [6, 12, 30, 20, 8, 14, 16]
        for col, w in enumerate(widths, start=1):
            wsv.column_dimensions[get_column_letter(col)].width = w
        wsv.freeze_panes = f"A{hdr+1}"

    # ─── 저장 ──────────────────────────────────────
    safe_name = bid['name'].replace("/", "_").replace("\\", "_")
    out_path = OUTPUT_DIR / f"입찰비교_{safe_name}.xlsx"
    wb.save(out_path)
    return out_path


# ─── [#3] 레벨별 캔버스 엑셀 추출 ──────────────────────────────

def export_level_tree_xlsx(submission_id) -> Path:
    """확정/연계 레벨 트리를 엑셀로. 열 = 대/중/소/세/품명/부품 + 규격·수량·단위·단가·금액
    (+부품수량·부품단가·파트금액). 상위 분류 셀은 반복 생략(병합 느낌), 빈 레벨은 빈 칸,
    부품 행은 합계 제외(단가 내역), 합계 = Σ품목금액. 재구성 오버레이(recon) 반영.
    """
    from db.queries import build_items_tree, get_submission, get_conn, _split_path
    sub = get_submission(submission_id)
    tree = build_items_tree(submission_id)   # recon 뷰(레벨·이동·재레벨 반영)

    # 품목(잎)을 조상 체인(level,name)과 함께 평면화.
    leaves = []
    def _walk(nodes, chain):
        for n in nodes:
            ch = chain + [(n.get("level") or n.get("depth") or 1, n.get("name"))]
            if n.get("is_leaf"):
                leaves.append((ch, n.get("leaf_data") or {}, n.get("path")))
            else:
                _walk(n.get("children") or [], ch)
    _walk(tree.get("tree") or [], [])

    # 부품(bom_part) — 품목 전체경로별.
    parts_by_path = {}
    with get_conn() as c:
        for r in c.execute("""
            SELECT name_normalized, part_qty, part_price, part_amount, path
            FROM submission_items WHERE submission_id=? AND part_amount IS NOT NULL
            ORDER BY sort_order
        """, (submission_id,)):
            d = dict(r)
            parts_by_path.setdefault(" > ".join(_split_path(d["path"] or "")), []).append(d)

    wb = Workbook()
    ws = wb.active
    ws.title = "레벨트리"
    HEAD = ["대분류", "중분류", "소분류", "세분류", "품명", "부품",
            "규격", "수량", "단위", "단가", "금액", "부품수량", "부품단가", "파트금액"]
    for ci, h in enumerate(HEAD, 1):
        _h(ws.cell(row=1, column=ci), h)
    NUM = "#,##0"
    row = 2
    prev = [None] * 6   # 대~부품 상위 셀 반복 생략용
    total = 0.0
    for chain, ld, fullpath in leaves:
        cells = [None] * 6   # 1..5 분류/품명, 6 부품(품목행은 빈칸)
        for lv, nm in chain:
            # placeholder(⟨미연계·N분류⟩)는 '빈 레벨'로 취급 → 빈 칸.
            if 1 <= lv <= 5 and nm and not str(nm).startswith("⟨미연계"):
                cells[lv - 1] = nm
        # 상위 분류(1..4) 반복 생략(병합 느낌). 품명(5)은 항상 표기.
        for k in range(4):
            if cells[k] is not None and cells[k] == prev[k]:
                _d(ws.cell(row=row, column=k + 1), None)
            else:
                _d(ws.cell(row=row, column=k + 1), cells[k])
                prev[k] = cells[k]
                for j in range(k + 1, 4):
                    prev[j] = None   # 상위 바뀌면 하위 반복상태 리셋
        _d(ws.cell(row=row, column=5), cells[4])   # 품명
        _d(ws.cell(row=row, column=6), None)        # 품목행 부품칸 빈칸
        _d(ws.cell(row=row, column=7), ld.get("spec"))
        _d(ws.cell(row=row, column=8), ld.get("qty"), align=R, fmt=NUM)
        _d(ws.cell(row=row, column=9), ld.get("unit"), align=C)
        _d(ws.cell(row=row, column=10), ld.get("unit_price"), align=R, fmt=NUM)
        _d(ws.cell(row=row, column=11), ld.get("amount"), align=R, fmt=NUM)
        total += (ld.get("amount") or 0)
        row += 1
        # 부품 행(합계 제외).
        for p in parts_by_path.get(" > ".join(_split_path(fullpath or "")), []):
            _d(ws.cell(row=row, column=6), "↳ " + str(p.get("name_normalized") or ""))
            _d(ws.cell(row=row, column=12), p.get("part_qty"), align=R, fmt=NUM)
            _d(ws.cell(row=row, column=13), p.get("part_price"), align=R, fmt=NUM)
            _d(ws.cell(row=row, column=14), p.get("part_amount"), align=R, fmt=NUM)
            row += 1
    # 합계 행(Σ품목금액, 부품 제외).
    _h(ws.cell(row=row, column=1), "합계 (품목 금액 합 · 부품 제외)", font=BF, fill=GREY, align=L)
    for ci in range(2, 11):
        _d(ws.cell(row=row, column=ci), None, font=BF)
    _d(ws.cell(row=row, column=11), round(total), font=BF, align=R, fmt=NUM)
    for ci in range(12, 15):
        _d(ws.cell(row=row, column=ci), None, font=BF)

    widths = [12, 12, 12, 12, 16, 16, 14, 8, 7, 12, 14, 9, 11, 12]
    for ci, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.freeze_panes = "A2"

    vend = (dict(sub).get("vendor_name") if sub else None) or submission_id
    safe = str(vend).replace("/", "_").replace("\\", "_")
    out_path = OUTPUT_DIR / f"레벨트리_{safe}.xlsx"
    wb.save(out_path)
    return out_path
