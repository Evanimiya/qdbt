"""
LLM Provider 추상 인터페이스.

새 모델 추가 시:
  1. src/extractors/providers/ 에 새 파일 생성
  2. LLMProvider 상속 + extract() 구현
  3. providers/__init__.py의 PROVIDERS에 등록
  → 기존 코드 수정 없음
"""
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    provider_id:   str        # 'claude' | 'gpt' | 'gemini' ...
    provider_name: str        # 화면 표시용 (예: 'Anthropic Claude')
    default_model: str        # 기본 모델명
    key_prefix:    str        # API 키 형식 검증용 prefix
    models:        list       # 지원 모델 목록 [(model_id, display_name)]

    @abstractmethod
    def extract(self,
                parsed_text: str,
                system_prompt: str,
                api_key: str,
                model: str = None) -> str:
        """
        파싱된 텍스트를 LLM에 전달하여 응답 텍스트 반환.

        Args:
            parsed_text:   파서가 생성한 입력 텍스트
            system_prompt: 추출 지시 프롬프트
            api_key:       사용자 API 키
            model:         모델 지정 (None이면 default_model 사용)

        Returns:
            LLM 응답 텍스트 (JSON 문자열)

        Raises:
            LLMProviderError: API 호출 실패
        """
        pass

    def validate_key(self, key: str) -> bool:
        """API 키 형식 검증"""
        return bool(key) and key.startswith(self.key_prefix)

    def get_model(self, model: str = None) -> str:
        """사용할 모델 결정 (없으면 기본값)"""
        return model or self.default_model

    def to_dict(self) -> dict:
        """프론트엔드 전달용 dict"""
        return {
            "id":            self.provider_id,
            "name":          self.provider_name,
            "default_model": self.default_model,
            "key_prefix":    self.key_prefix,
            "models":        self.models,
        }


class LLMProviderError(Exception):
    """Provider 호출 관련 오류"""
    pass
