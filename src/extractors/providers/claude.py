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
                api_key: str, model: str = None) -> str:
        try:
            from anthropic import Anthropic
        except ImportError:
            raise LLMProviderError(
                "anthropic 라이브러리가 없습니다. pip install anthropic"
            )

        try:
            client = Anthropic(api_key=api_key)
            response = client.messages.create(
                model=self.get_model(model),
                max_tokens=16000,
                system=system_prompt,
                messages=[{"role": "user", "content": parsed_text}],
            )
            return response.content[0].text
        except Exception as e:
            raise LLMProviderError(f"Claude API 오류: {type(e).__name__}: {e}")
