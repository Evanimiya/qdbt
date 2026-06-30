"""
OpenAI GPT Provider.
"""
from extractors.llm_provider import LLMProvider, LLMProviderError

# 모델별로 성공한 토큰 파라미터를 기억 (프로세스 수명 동안).
# 같은 모델 재호출 시 불필요한 재시도를 줄인다.
_TOKEN_PARAM_CACHE = {}


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

    _TOKEN_LIMIT = 16000
    # LLM 응답 대기 한도(초). 큰 입력이 무한 대기에 빠지는 것 방지.
    # 이 시간 내 응답이 없으면 타임아웃 에러로 빠져나옴.
    _REQUEST_TIMEOUT = 180.0

    def extract(self, parsed_text: str, system_prompt: str,
                api_key: str, model: str = None, base_url: str = None,
                verify_ssl: bool = True) -> str:
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
                client, self.get_model(model), system_prompt, parsed_text)
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
    def _create_with_token_param(self, client, model, system_prompt, parsed_text):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": parsed_text},
        ]
        order = self._preferred_token_params(model)
        last_err = None
        for param in order:
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **{param: self._TOKEN_LIMIT},
                )
                _TOKEN_PARAM_CACHE[model] = param
                return resp.choices[0].message.content
            except Exception as e:
                msg = str(e).lower()
                last_err = e
                token_related = (
                    "max_tokens" in msg
                    or "max_completion_tokens" in msg
                    or "unsupported parameter" in msg
                    or "unsupported_parameter" in msg
                )
                if token_related:
                    continue
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
