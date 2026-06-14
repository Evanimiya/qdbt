"""
LLM Provider 레지스트리.

새 provider 추가 시 이 파일만 수정:
  1. import 추가
  2. PROVIDERS dict에 등록
"""
from extractors.providers.claude import ClaudeProvider
from extractors.providers.gpt    import GPTProvider

# 등록된 모든 provider
# 향후 추가 예시:
#   from extractors.providers.gemini  import GeminiProvider
#   from extractors.providers.clova   import ClovaProvider
PROVIDERS: dict = {
    "claude": ClaudeProvider(),
    "gpt":    GPTProvider(),
}

DEFAULT_PROVIDER = "claude"


def get_provider(provider_id: str):
    p = PROVIDERS.get(provider_id)
    if not p:
        raise ValueError(
            f"지원하지 않는 provider: '{provider_id}'. "
            f"사용 가능: {list(PROVIDERS.keys())}"
        )
    return p


def list_providers() -> list:
    return [p.to_dict() for p in PROVIDERS.values()]
