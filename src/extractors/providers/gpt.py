"""
OpenAI GPT Provider.
"""
from extractors.llm_provider import LLMProvider, LLMProviderError


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

    def extract(self, parsed_text: str, system_prompt: str,
                api_key: str, model: str = None) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            raise LLMProviderError(
                "openai 라이브러리가 없습니다. pip install openai"
            )

        try:
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=self.get_model(model),
                max_tokens=8000,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": parsed_text},
                ],
            )
            return response.choices[0].message.content
        except Exception as e:
            raise LLMProviderError(f"GPT API 오류: {type(e).__name__}: {e}")
