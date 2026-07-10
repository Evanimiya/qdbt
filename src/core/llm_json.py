"""LLM 응답 JSON 견고 추출 (코드리뷰 M6).

세 곳(llm_extractor·matcher·catalog_clusterer)이 제각기 코드펜스만 벗기던 것을
단일 창구로 통합. 코드펜스·서두 산문("Here is the JSON: ...")·후행 텍스트가 붙어도
최외곽 JSON 객체/배열을 괄호균형으로 추출한다(문자열 내부 괄호는 무시).
"""
from __future__ import annotations
import json as _json
import re as _re


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = _re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)   # ``` 또는 ```json 라벨 제거
        s = _re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _extract_balanced(s: str):
    """문자열 내 첫 최외곽 {...} 또는 [...] 를 괄호균형으로 추출(문자열/이스케이프 인지)."""
    start = None
    depth = 0
    opener = closer = None
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if start is None:
            if ch in "{[":
                start, opener, closer, depth = i, ch, ("}" if ch == "{" else "]"), 1
            continue
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def extract_json(text):
    """LLM 응답 문자열에서 JSON(dict/list)을 파싱해 반환. 실패 시 ValueError.

    순서: (1) 코드펜스 제거 후 직접 파싱 → (2) 최외곽 괄호균형 추출 후 파싱.
    """
    if text is None:
        raise ValueError("빈 LLM 응답")
    s = _strip_fences(str(text))
    if not s:
        raise ValueError("빈 LLM 응답")
    try:
        return _json.loads(s)
    except _json.JSONDecodeError:
        pass
    frag = _extract_balanced(s)
    if frag is not None:
        try:
            return _json.loads(frag)
        except _json.JSONDecodeError as e:
            raise ValueError(f"JSON 파싱 실패(괄호추출 후): {e}")
    raise ValueError("응답에서 JSON 객체를 찾지 못했습니다")
