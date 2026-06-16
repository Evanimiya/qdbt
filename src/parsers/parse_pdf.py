"""
PDF 파서 — pdfplumber로 표 구조와 텍스트 좌표 모두 추출

PDF는 XLSX와 달리 셀 병합 정보가 없으므로,
대신 다음 신호를 LLM에 전달:
- 페이지별 분할
- 텍스트 위치 (x, y)
- 표 추출 (pdfplumber.extract_tables)
- 페이지 분류 힌트 (페이지 첫 줄로 갑지/명세/도면/거래조건 추정)
"""
import pdfplumber
from pathlib import Path


def detect_page_type(first_lines):
    """페이지 첫 몇 줄로 페이지 종류 추정"""
    text = "\n".join(first_lines).lower()
    if "랙 배치도" in "\n".join(first_lines) or "front view" in text:
        return "DRAWING"
    if "구성도" in "\n".join(first_lines) and "logical" in text:
        return "DIAGRAM"
    if "거래조건" in "\n".join(first_lines) or "거 래 조 건" in "\n".join(first_lines):
        return "TERMS"
    if "견적서" in "\n".join(first_lines) and "명세" not in "\n".join(first_lines):
        return "SUMMARY (갑지)"
    if "명세" in "\n".join(first_lines):
        return "DETAIL (명세)"
    return "UNKNOWN"


def parse_pdf(path):
    sections = []
    sections.append(f"# File: {Path(path).name}")
    sections.append("")

    with pdfplumber.open(path) as pdf:
        sections.append(f"# Total pages: {len(pdf.pages)}")
        sections.append("")

        for i, page in enumerate(pdf.pages, start=1):
            sections.append(f"=" * 80)
            sections.append(f"## Page {i}")
            sections.append(f"  Page size: {page.width:.1f} x {page.height:.1f} pt")

            # 첫 줄들로 페이지 타입 추정
            text = page.extract_text() or ""
            first_lines = text.split("\n")[:5]
            page_type = detect_page_type(first_lines)
            sections.append(f"  Detected type: {page_type}")
            sections.append("")

            # 표 추출
            tables = page.extract_tables()
            if tables:
                sections.append(f"### Tables on page {i}: {len(tables)} found")
                for ti, table in enumerate(tables):
                    sections.append(f"\n#### Table {ti + 1}")
                    for ri, row in enumerate(table):
                        cleaned = [
                            (cell.strip().replace("\n", " ") if cell else "(empty)")
                            for cell in row
                        ]
                        sections.append(f"R{ri:03d} | " + " | ".join(cleaned))
                sections.append("")

            # 표가 없는 페이지 (도면, 갑지 일부, 거래조건 등)는 텍스트만
            if not tables or page_type in ("DRAWING", "DIAGRAM", "TERMS"):
                sections.append(f"### Raw text on page {i}")
                sections.append(text)
                sections.append("")

    return "\n".join(sections)


if __name__ == "__main__":
    path = "/home/claude/sample_bid/C공업_견적서.pdf"
    out = parse_pdf(path)
    out_path = Path("/home/claude/poc/C공업_견적서.parsed.txt")
    out_path.write_text(out, encoding="utf-8")
    print(f"  {Path(path).name} -> {out_path.name} ({len(out)} chars)")
