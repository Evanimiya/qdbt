# LLM Provider 추상화 설계

> 상태: 설계 확정 / 빌드 예정 (v0.4.0-test)
> 작성일: 2026-06-04

## 배경

현재 시스템은 Anthropic Claude만 지원.
향후 OpenAI GPT, 기타 모델 추가 및 사용자별 provider 선택이 필요.

---

## 목표

- Claude / GPT 동시 지원, 사용자가 직접 선택
- 새 모델 추가 시 **기존 코드 수정 없이** 파일 하나만 추가
- 사용자별로 다른 provider + 모델 + API 키 사용 가능

---

## 디렉토리 구조

```
src/extractors/
    llm_provider.py          ← 공통 추상 인터페이스
    providers/
        __init__.py          ← provider 레지스트리
        claude.py            ← Anthropic Claude 구현
        gpt.py               ← OpenAI GPT 구현
        # 향후 추가:
        # gemini.py          ← Google Gemini
        # clova.py           ← Naver Clova
        # bedrock.py         ← AWS Bedrock
    llm_extractor.py         ← provider 선택 후 호출 (기존 인터페이스 유지)
    pipeline.py              ← 변경 없음
```

---

## 공통 인터페이스 (llm_provider.py)

```python
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    provider_id: str        # 'claude' | 'gpt' | 'gemini' ...
    provider_name: str      # 화면 표시용 이름
    default_model: str      # 기본 모델명
    key_prefix: str         # API 키 형식 검증용 prefix
    models: list[str]       # 지원 모델 목록

    @abstractmethod
    def extract(self,
                parsed_text: str,
                vendor_name: str,
                system_prompt: str,
                api_key: str,
                model: str = None) -> str:
        """파싱된 텍스트 → LLM 응답 텍스트 반환"""
        pass

    def validate_key(self, key: str) -> bool:
        """API 키 형식 검증"""
        return key.startswith(self.key_prefix)
```

---

## Provider 구현 예시

### claude.py
```python
class ClaudeProvider(LLMProvider):
    provider_id   = "claude"
    provider_name = "Anthropic Claude"
    default_model = "claude-sonnet-4-20250514"
    key_prefix    = "sk-ant-"
    models = [
        "claude-opus-4-5",
        "claude-sonnet-4-20250514",
        "claude-haiku-4-5-20251001",
    ]

    def extract(self, parsed_text, vendor_name, system_prompt, api_key, model=None):
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model or self.default_model,
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": parsed_text}]
        )
        return response.content[0].text
```

### gpt.py
```python
class GPTProvider(LLMProvider):
    provider_id   = "gpt"
    provider_name = "OpenAI GPT"
    default_model = "gpt-4o"
    key_prefix    = "sk-"
    models = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
    ]

    def extract(self, parsed_text, vendor_name, system_prompt, api_key, model=None):
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model or self.default_model,
            max_tokens=8000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": parsed_text}
            ]
        )
        return response.choices[0].message.content
```

---

## Provider 레지스트리 (providers/__init__.py)

```python
# 새 provider 추가 시 여기만 수정
from .claude import ClaudeProvider
from .gpt    import GPTProvider

PROVIDERS = {
    "claude": ClaudeProvider(),
    "gpt":    GPTProvider(),
}

def get_provider(provider_id: str) -> LLMProvider:
    if provider_id not in PROVIDERS:
        raise ValueError(f"지원하지 않는 provider: {provider_id}")
    return PROVIDERS[provider_id]

def list_providers() -> list[dict]:
    return [
        {"id": p.provider_id, "name": p.provider_name, "models": p.models}
        for p in PROVIDERS.values()
    ]
```

---

## DB 변경 (schema.py)

```sql
-- users 테이블 변경
-- 기존: anthropic_api_key_enc TEXT
-- 변경:
llm_provider      TEXT DEFAULT 'claude',  -- 'claude' | 'gpt' | ...
llm_model         TEXT,                   -- NULL이면 provider 기본값
llm_api_key_enc   TEXT,                   -- provider 무관 암호화 저장
```

### 마이그레이션 (기존 DB 보존)
```sql
ALTER TABLE users ADD COLUMN llm_provider TEXT DEFAULT 'claude';
ALTER TABLE users ADD COLUMN llm_model    TEXT;
ALTER TABLE users ADD COLUMN llm_api_key_enc TEXT;

-- 기존 anthropic_api_key_enc → llm_api_key_enc 복사
UPDATE users SET llm_api_key_enc = anthropic_api_key_enc
WHERE anthropic_api_key_enc IS NOT NULL;
```

---

## 프로필 화면 변경

```
⚙ LLM 설정

Provider  [ Claude (Anthropic) ▼ ]
           [ GPT (OpenAI)        ]
           [ 향후 추가 ...        ]

모델      [ 자동 (claude-sonnet-4) ▼ ]  ← provider 선택 시 목록 자동 변경
           직접 입력 옵션도 제공

API 키    [ sk-ant-... 또는 sk-... ]
           provider 선택에 따라 placeholder 자동 변경
```

---

## llm_extractor.py 변경 후 인터페이스

기존 호출 방식 완전 유지 (pipeline.py 변경 불필요):

```python
# 변경 전 (현재)
result = extract_with_validation(parsed_text, vendor_name, api_key=api_key)

# 변경 후 (동일)
result = extract_with_validation(parsed_text, vendor_name,
                                 api_key=api_key,
                                 provider="gpt",  # 추가
                                 model="gpt-4o")  # 추가 (선택)
```

---

## 새 모델 추가 방법 (향후)

예: Google Gemini 추가 시

1. `src/extractors/providers/gemini.py` 파일 생성
2. `GeminiProvider(LLMProvider)` 구현
3. `providers/__init__.py`의 `PROVIDERS`에 등록

**기존 코드 수정 없음.**

---

## 빌드 시 작업 목록

- [ ] `src/extractors/llm_provider.py` 추상 클래스 생성
- [ ] `src/extractors/providers/claude.py` 구현
- [ ] `src/extractors/providers/gpt.py` 구현
- [ ] `src/extractors/providers/__init__.py` 레지스트리
- [ ] `src/extractors/llm_extractor.py` provider 선택 로직 추가
- [ ] `src/db/schema.py` 컬럼 변경 + 마이그레이션 쿼리
- [ ] `src/db/queries.py` 함수명/로직 변경
- [ ] `src/web/blueprints/profile.py` provider 선택 UI
- [ ] `src/web/templates/profile/index.html` provider 선택 화면
- [ ] `requirements.txt` openai 추가
- [ ] 프롬프트 GPT 호환성 검증 (prompt_extract_v1.md)

예상 작업량: 반나절 (코드) + GPT 프롬프트 튜닝 별도

---

## 참고: 각 Provider API 비교

| 항목 | Claude | GPT |
|------|--------|-----|
| 라이브러리 | `anthropic` | `openai` |
| 키 형식 | `sk-ant-...` | `sk-...` |
| system prompt | 별도 파라미터 | messages 배열 첫 번째 |
| 응답 추출 | `response.content[0].text` | `response.choices[0].message.content` |
| 추천 모델 | claude-sonnet-4 | gpt-4o |
| 추출 프롬프트 | 검증 완료 (F1=1.0) | 재튜닝 필요 |
