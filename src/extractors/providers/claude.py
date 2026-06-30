"""
Anthropic Claude Provider.
"""
from extractors.llm_provider import LLMProvider, LLMProviderError


class ClaudeProvider(LLMProvider):
    provider_id   = "claude"
    provider_name = "Anthropic Claude"
    default_model = "claude-sonnet-4-20250514"
    key_prefix    = "sk-ant-"
    models = [
        ("claude-opus-4-5",            "Claude Opus 4.5 (최고 성능)"),
        ("claude-sonnet-4-20250514",   "Claude Sonnet 4 (권장)"),
        ("claude-haiku-4-5-20251001",  "Claude Haiku 4.5 (빠름)"),
    ]

    def extract(self, parsed_text: str, system_prompt: str,
                api_key: str, model: str = None, base_url: str = None,
                verify_ssl: bool = True) -> str:
        try:
            from anthropic import Anthropic
            import httpx
        except ImportError:
            raise LLMProviderError(
                "anthropic 라이브러리가 없습니다. pip install anthropic"
            )

        try:
            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url.rstrip("/")
            kwargs["timeout"] = 180.0
            kwargs["max_retries"] = 1
            if not verify_ssl:
                kwargs["http_client"] = httpx.Client(verify=False, timeout=180.0)
            client = Anthropic(**kwargs)
            response = client.messages.create(
                model=self.get_model(model),
                max_tokens=16000,
                system=system_prompt,
                messages=[{"role": "user", "content": parsed_text}],
            )
            return response.content[0].text
        except Exception as e:
            etype = type(e).__name__
            if "timeout" in etype.lower() or "timeout" in str(e).lower():
                raise LLMProviderError(
                    "LLM 응답 시간 초과(180초). 입력이 너무 클 수 있습니다. "
                    f"시트를 나눠서 추출해 보세요. (원인: {etype})"
                )
            raise LLMProviderError(f"Claude API 오류: {etype}: {e}")
