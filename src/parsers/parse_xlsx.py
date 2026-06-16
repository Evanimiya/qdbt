"""
XLSX 파서 — LLM 입력용 구조화 텍스트 생성

핵심 설계: LLM이 계층 구조를 추론할 수 있도록 다음 신호를 모두 보존
- 셀 좌표 (행, 열)
- 병합 셀 범위 (예: A5:F5는 병합되어 있음)
- 빈 셀 (groupby 추론용)
- 들여쓰기 (앞 공백 개수)
- 셀의 시각적 강조 (fill 색상 → 카테고리 헤더 신호)
- 셀 폰트 굵기 (헤더 신호)

출력: 시트별 텍스트 표현
- 시트 메타 (이름, 차원)
- 행 단위로 셀 값 + 위치 정보를 |로 구분
- 빈 셀은 "(empty)"로 명시
- 병합 셀 정보는 별도 섹션
"""
import openpyxl
from openpyxl import load_workbook
import json
from pathlib import Path


def cell_to_str(cell, indent_info=False):
    """셀 값을 문자열로, 들여쓰기 정보 옵션 포함"""
    v = cell.value
    if v is None or v == "":
        return "(empty)"
    if isinstance(v, str):
        # 들여쓰기 보존: 앞쪽 공백 개수 별도 표시
        leading = len(v) - len(v.lstrip(" "))
        text = v.strip()
        if indent_info and leading > 0:
            return f"[indent={leading}]{text}"
        return text
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, str) and v.startswith("="):
        # 수식 그대로
        return v
    return str(v)


def get_merged_ranges(sheet):
    """병합 셀 범위 목록"""
    return [str(rng) for rng in sheet.merged_cells.ranges]


def get_cell_format_signal(cell):
    """셀의 강조 신호: fill 색상이 있거나 bold면 헤더 가능성 높음"""
    signals = []
    if cell.font and cell.font.bold:
        signals.append("BOLD")
    fill = cell.fill
    if fill and fill.start_color and fill.start_color.rgb:
        rgb = fill.start_color.rgb
        if rgb and rgb not in ("00000000", "FFFFFFFF", None):
            # 색상이 있음 = 강조 행 가능성
            signals.append(f"FILL={rgb[-6:]}")
    return ",".join(signals) if signals else ""


def parse_sheet(sheet, max_rows=None):
    """시트를 LLM 입력용 텍스트로 변환"""
    lines = []
    lines.append(f"### Sheet: {sheet.title}")
    lines.append(f"Dimensions: {sheet.dimensions}")
    lines.append(f"Max row: {sheet.max_row}, Max col: {sheet.max_column}")
    lines.append("")

    # 병합 셀 정보
    merged = get_merged_ranges(sheet)
    if merged:
        lines.append("### Merged Cells")
        for m in merged:
            lines.append(f"  {m}")
        lines.append("")

    # 셀 데이터 (행 단위)
    lines.append("### Cell Data (row | col_A | col_B | ... | format_signals)")
    n_rows = min(sheet.max_row, max_rows) if max_rows else sheet.max_row
    n_cols = sheet.max_column

    for r in range(1, n_rows + 1):
        row_cells = []
        signals = []
        for c in range(1, n_cols + 1):
            cell = sheet.cell(r, c)
            val = cell_to_str(cell, indent_info=True)
            row_cells.append(val)
            sig = get_cell_format_signal(cell)
            if sig:
                signals.append(f"{openpyxl.utils.get_column_letter(c)}{r}:{sig}")

        sig_str = "  ;  ".join(signals) if signals else "-"
        line = f"R{r:03d} | " + " | ".join(row_cells)
        if signals:
            line += f"   <<{sig_str}>>"
        lines.append(line)

    return "\n".join(lines)


def parse_xlsx(path, max_rows_per_sheet=None):
    """전체 XLSX 파일을 파싱"""
    wb = load_workbook(path, data_only=False)  # 수식 보존
    wb_calc = load_workbook(path, data_only=True)  # 계산값도 같이 (계산값 우선)

    sections = []
    sections.append(f"# File: {Path(path).name}")
    sections.append(f"# Sheets: {wb.sheetnames}")
    sections.append("")

    for name in wb.sheetnames:
        # 수식이 있는 셀은 계산값으로 표시
        sheet = wb_calc[name]
        sections.append(parse_sheet(sheet, max_rows=max_rows_per_sheet))
        sections.append("\n" + "=" * 80 + "\n")

    return "\n".join(sections)


if __name__ == "__main__":
    import sys
    files = [
        "/home/claude/sample_bid/A상사_입찰서.xlsx",
        "/home/claude/sample_bid/B제조_견적서.xlsx",
        "/home/claude/sample_bid/C공업_견적서_원본.xlsx",
    ]
    for f in files:
        out = parse_xlsx(f)
        out_path = Path("/home/claude/poc") / (Path(f).stem + ".parsed.txt")
        out_path.write_text(out, encoding="utf-8")
        print(f"  {Path(f).name} -> {out_path.name} ({len(out)} chars)")
