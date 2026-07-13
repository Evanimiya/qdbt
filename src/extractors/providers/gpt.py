"""
OpenAI GPT Provider.
"""
from extractors.llm_provider import LLMProvider, LLMProviderError

# 모델별로 성공한 토큰 파라미터를 기억 (프로세스 수명 동안).
# 같은 모델 재호출 시 불필요한 재시도를 줄인다.
_TOKEN_PARAM_CACHE = {}
# 모델별로 성공한 max_tokens 상한을 기억 — 모델마다 출력 한도가 달라
# (gpt-4o=16384, gpt-4-turbo=4096, gpt-5/o계열=더 큼) 첫 400 이후 학습해 반복 400 방지.
_TOKEN_LIMIT_CACHE = {}


class GPTProvider(LLMProvider):
    provider_id   = "gpt"
    provider_name = "OpenAI GPT"
    default_model = "gpt-4o"
    key_prefix    = "sk-"
    models = [
        ("gpt-4o",       "GPT-4o (권장)"),
        ("gpt-4o-mini",  "GPT-4o mini (빠름/저렴)"),
        ("gpt-4-turbo",  "GPT-4 Turbo"),
    ]

    _TOKEN_LIMIT = 32000   # [코드리뷰 M5] 대용량 응답 절단 완화
    # LLM 응답 대기 한도(초). 큰 입력이 무한 대기에 빠지는 것 방지.
    # 이 시간 내 응답이 없으면 타임아웃 에러로 빠져나옴.
    _REQUEST_TIMEOUT = 180.0

    def extract(self, parsed_text: str, system_prompt: str,
                api_key: str, model: str = None, base_url: str = None,
                verify_ssl: bool = True, temperature: float = None) -> str:
        try:
            from openai import OpenAI
            import httpx
        except ImportError:
            raise LLMProviderError(
                "openai 라이브러리가 없습니다. pip install openai"
            )

        try:
            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url.rstrip("/")
            # 타임아웃: 큰 입력이 무한 대기에 빠지는 것 방지.
            # 응답이 이 시간 내 안 오면 APITimeoutError로 빠져나옴.
            kwargs["timeout"] = self._REQUEST_TIMEOUT
            kwargs["max_retries"] = 1
            if not verify_ssl:
                kwargs["http_client"] = httpx.Client(verify=False, timeout=self._REQUEST_TIMEOUT)
            client = OpenAI(**kwargs)
            return self._create_with_token_param(
                client, self.get_model(model), system_prompt, parsed_text,
                temperature=temperature)
        except LLMProviderError:
            raise
        except Exception as e:
            etype = type(e).__name__
            # 타임아웃이면 사이즈 이슈일 가능성을 명확히 안내
            if "timeout" in etype.lower() or "timeout" in str(e).lower():
                raise LLMProviderError(
                    f"LLM 응답 시간 초과({self._REQUEST_TIMEOUT}초). "
                    f"입력이 너무 클 수 있습니다. 시트를 나눠서 추출해 보세요. "
                    f"(원인: {etype})"
                )
            raise LLMProviderError(f"GPT API 오류: {etype}: {e}")

    # 토큰 파라미터는 모델마다 다름:
    #   - 구형(gpt-4o 등):     max_tokens
    #   - 신형(gpt-5/o계열 등): max_completion_tokens
    # 하드코딩하지 않고, 표준 파라미터로 먼저 시도 후
    # 오류 메시지를 보고 자동으로 다른 파라미터로 재시도한다.
    def _create_with_token_param(self, client, model, system_prompt, parsed_text,
                                 temperature=None):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": parsed_text},
        ]
        import re as _re
        order = self._preferred_token_params(model)
        last_err = None
        extra = {"temperature": temperature} if temperature is not None else {}
        # 이 모델에서 이전에 성공한(또는 400으로 학습된) 상한이 있으면 그것부터.
        limit = min(self._TOKEN_LIMIT, _TOKEN_LIMIT_CACHE.get(model, self._TOKEN_LIMIT))
        for param in order:
            # 각 param마다 '값이 모델 상한 초과' 400이면 상한을 낮춰 재시도(최대 4회).
            for _attempt in range(4):
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        **{param: limit},
                        **extra,
                    )
                    _TOKEN_PARAM_CACHE[model] = param
                    _TOKEN_LIMIT_CACHE[model] = limit   # 성공한 상한 학습
                    # [코드리뷰 M7] content가 None일 수 있음(finish_reason=length/필터/함수호출).
                    #  그대로 반환하면 상위 json.loads(None)에서 불명확한 TypeError.
                    choice = resp.choices[0] if resp.choices else None
                    content = choice.message.content if choice else None
                    if not content:
                        fr = getattr(choice, "finish_reason", None) if choice else None
                        if fr == "length":
                            raise LLMProviderError(
                                "GPT 응답이 max_tokens에서 잘렸습니다(finish_reason=length). "
                                "입력을 줄이거나 시트를 나눠서 추출하세요.")
                        raise LLMProviderError(
                            f"GPT 응답이 비어 있습니다(finish_reason={fr}). "
                            "콘텐츠 필터·모델 응답 구조를 확인하세요.")
                    return content
                except LLMProviderError:
                    raise
                except Exception as e:
                    msg = str(e).lower()
                    last_err = e
                    # (a) 값이 모델 출력 한도 초과 → 허용 최대치로 낮춰 '같은 param' 재시도.
                    #     모델마다 한도가 달라(gpt-4o=16384, gpt-4-turbo=4096 등) 단일 상수로는
                    #     못 맞추므로 에러 메시지의 허용치를 파싱해 자동 축소.
                    if ("too large" in msg or "at most" in msg or "maximum" in msg) and limit > 2048:
                        mm = (_re.search(r"at most (\d+)", msg)
                              or _re.search(r"支持|supports?\D+(\d{3,})", msg)
                              or _re.search(r"(\d{4,})\s*(?:completion|tokens)", msg))
                        new_limit = int(mm.group(1)) if mm else max(2048, limit // 2)
                        if new_limit < limit:
                            limit = new_limit
                            _TOKEN_LIMIT_CACHE[model] = new_limit
                            continue   # 같은 param, 낮춘 limit로 재시도
                    # (b) param 이름 문제(max_tokens↔max_completion_tokens) → 다음 param으로.
                    token_related = (
                        "max_tokens" in msg
                        or "max_completion_tokens" in msg
                        or "unsupported parameter" in msg
                        or "unsupported_parameter" in msg
                    )
                    if token_related:
                        break   # 안쪽 재시도 루프 종료 → 다음 param
                    raise
        raise LLMProviderError(
            f"GPT API 오류(토큰 파라미터): {type(last_err).__name__}: {last_err}")

    @staticmethod
    def _preferred_token_params(model):
        """모델에 시도할 토큰 파라미터 순서.

        이전에 성공한 파라미터가 있으면 그것부터(캐시).
        없으면 표준(max_tokens) 먼저, 실패 시 max_completion_tokens.
        """
        cached = _TOKEN_PARAM_CACHE.get(model)
        if cached == "max_completion_tokens":
            return ["max_completion_tokens", "max_tokens"]
        if cached == "max_tokens":
            return ["max_tokens", "max_completion_tokens"]
        return ["max_tokens", "max_completion_tokens"]
