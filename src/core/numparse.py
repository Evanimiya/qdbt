"""공용 숫자·통화 파서 (코드리뷰 H1·H2 통합).

추출(extract_by_mapping)과 집계(db.queries)가 각기 다른 `_to_number` 구현을 갖던
것을 단일 창구로 통합한다. 순수 함수(외부 의존 없음).

규칙(도메인: 한국 조달 견적, 정수 KRW 우세 + 외화/소수 혼재):
  · 전각→반각 정규화(NFKC): 전각 숫자·기호·콤마·마이너스.
  · 통화기호/코드/문자 제거: ₩ ￦ 원 $ US$ USD ¥ 円 元 RMB CNY 위안 € EUR 유로
    £ GBP ₹ ₽ JPY 엔 달러 \\ 등 — 숫자·구분자만 남긴다.
  · 부호 보존(절대 뒤집지 않음): 괄호 `(1,234)`, 선행 `△▲▽`, `-`/`−`(U+2212)/`－` = 음수.
  · 소수 구분자 판정:
      - '.'과 ',' 공존 → 뒤에 오는 기호가 소수점(예 "1.234,56"→1234.56, "1,234.56"→1234.56).
      - ',' 단독 → 천단위로 간주해 제거(정수 KRW 우세). 예 "1,234,000"→1234000.
      - '.' 단독, 점이 2개 이상 → 유럽식 천단위로 간주해 제거(예 "1.234.567"→1234567).
      - '.' 단독, 점 1개 → 소수점 유지(예 "3.5"→3.5).
  · 파싱 실패 시 None(원문 손상 없이 호출부가 판단).

정수로 떨어지면 int, 아니면 float 반환(기존 두 구현과 호환).
"""
from __future__ import annotations
import re as _re
import unicodedata as _ud

# 숫자 파싱 시 제거할 통화 기호·코드·명칭(소문자 비교). 길이 내림차순으로 먼저 긴 것 제거.
_CURRENCY_TOKENS = (
    "us$", "usd", "won", "rmb", "cny", "eur", "gbp", "jpy", "krw",
    "달러", "위안", "유로", "엔", "원",
    "₩", "￦", "$", "¥", "円", "元", "€", "£", "₹", "₽", "\\",
)

# 선행 음수 표기(회계 삼각·각종 마이너스). U+2212 minus, U+FF0D fullwidth minus.
_NEG_PREFIX = "△▲▽-−－"


def parse_amount(v):
    """수량/단가/금액을 안전하게 숫자로 변환. 실패 시 None. (부호 보존)"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    s = _ud.normalize("NFKC", str(v)).strip()
    if not s:
        return None

    neg = False
    # ① 괄호 음수: (1,234) / （1,234）
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()

    # ② 통화 토큰 제거(대소문자 무시). 숫자에 섞인 명칭까지 제거.
    low = s.lower()
    for tok in _CURRENCY_TOKENS:
        if tok in low:
            low = low.replace(tok, "")
    s = low

    # ③ 선행 음수/삼각 마커(연속 가능). '-'/'−' 는 부호 토글, △▲▽ 는 음수 지정.
    s = s.strip()
    while s and s[0] in _NEG_PREFIX:
        if s[0] in "-−－":
            neg = not neg
        else:  # △▲▽
            neg = True
        s = s[1:].strip()

    # ③-b [비금액/범위 보존] 통화·부호 제거 후 '숫자[구분자]숫자' 범위 표기(예 "1-5",
    #  "1~5", "1 - 5")는 금액이 아니다. 억지 숫자화(1-5→15) 대신 None(미상)으로 보존해
    #  금액 열 오계상을 막는다. (계층 번호 "1-1"은 seq 역할 열에서 seq_tuple이 별도 처리.)
    if _re.fullmatch(r"\d+\s*[-–—~]\s*\d+", s):
        return None

    # ④ 숫자·구분자만 남기기
    s = _re.sub(r"[^\d.,]", "", s)
    if not s:
        return None

    # ⑤ 소수 구분자 판정
    has_dot, has_comma = ("." in s), ("," in s)
    if has_dot and has_comma:
        if s.rfind(".") > s.rfind(","):   # 점이 뒤 → 점이 소수점, 콤마는 천단위
            s = s.replace(",", "")
        else:                              # 콤마가 뒤 → 콤마가 소수점(유럽식)
            s = s.replace(".", "").replace(",", ".")
    elif has_comma:
        s = s.replace(",", "")             # 콤마 단독 = 천단위
    elif s.count(".") > 1:
        s = s.replace(".", "")             # 점 다수 = 유럽식 천단위
    elif has_dot:
        # 점 정확히 1개. [로케일 휴리스틱] 원화 정수 우세 도메인:
        #  단일 점 + '정확히 3자리' 소수부 → 유럽식 천단위로 간주(점 제거).
        #    "1.000"→1000, "27.400"→27400, "1.234"→1234.
        #  1~2자리·4자리+ 소수부는 진짜 소수로 유지("3.5","1.25","1.2345").
        #  정수부가 "0"/빈값이면(예 "0.125") 명백한 소수 → 천단위 해석 안 함.
        _intp, _frac = s.split(".")
        if len(_frac) == 3 and _intp.isdigit() and _intp not in ("0", ""):
            s = _intp + _frac

    if s in ("", ".", "-"):
        return None
    try:
        num = float(s)
    except ValueError:
        return None
    if neg:
        num = -num
    # 정수로 떨어지면 int (기존 구현 호환)
    if num == int(num):
        num = int(num)
    return num
