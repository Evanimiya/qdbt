"""
Anthropic Claude Provider.
"""
from extractors.llm_provider import LLMProvider, LLMProviderError


class ClaudeProvider(LLMProvider):
    provider_id   = "claude"
    provider_name = "Anthropic Claude"
    default_model = "claude-sonnet-4-20250514"
    key_prefix    = "sk-ant-"
    max_output_tokens = 32000   # [코드리뷰 M5] 대용량 추출/클러스터 응답 절단 완화(Claude ≥32k)
    models = [
        ("claude-opus-4-8",            "Claude Opus 4.8 (최고 성능·분류 권장)"),
        ("claude-opus-4-5",            "Claude Opus 4.5"),
        ("claude-sonnet-5",            "Claude Sonnet 5"),
        ("claude-sonnet-4-20250514",   "Claude Sonnet 4"),
        ("claude-haiku-4-5-20251001",  "Claude Haiku 4.5 (빠름)"),
    ]

    def extract(self, parsed_text: str, system_prompt: str,
                api_key: str, model: str = None, base_url: str = None,
                verify_ssl: bool = True, temperature: float = None) -> str:
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
            create_kwargs = dict(
                model=self.get_model(model),
                max_tokens=self.max_output_tokens,   # [코드리뷰 M5] 상수화·상향(절단 완화)
                system=system_prompt,
                messages=[{"role": "user", "content": parsed_text}],
            )
            # 분류/클러스터링처럼 결정성이 중요한 호출은 temperature=0 전달.
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            response = client.messages.create(**create_kwargs)
            # [코드리뷰 H5] 첫 블록이 text라고 가정하지 않는다. 추론형 모델은 thinking/
            #  tool_use 블록이 먼저 올 수 있어 content[0].text가 깨진다. text 블록만 결합.
            blocks = response.content or []
            text = "".join(getattr(b, "text", "") for b in blocks
                           if getattr(b, "type", None) == "text")
            if not text.strip():
                raise LLMProviderError(
                    "Claude 응답에 text 블록이 없습니다(모델 응답 구조·max_tokens 확인).")
            return text
        except Exception as e:
            etype = type(e).__name__
            if "timeout" in etype.lower() or "timeout" in str(e).lower():
                raise LLMProviderError(
                    "LLM 응답 시간 초과(180초). 입력이 너무 클 수 있습니다. "
                    f"시트를 나눠서 추출해 보세요. (원인: {etype})"
                )
            raise LLMProviderError(f"Claude API 오류: {etype}: {e}")
