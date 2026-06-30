"""
API 키 암호화/복호화 유틸.

Fernet 대칭키 암호화 사용 (cryptography 라이브러리).
없으면 base64 obfuscation fallback (운영 환경에서는 cryptography 설치 권장).

암호화 키는 환경변수 ENCRYPT_KEY에서 로드.
없으면 SECRET_KEY를 기반으로 자동 생성 (재현 가능).
"""
import os
import base64
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _get_fernet():
    try:
        from cryptography.fernet import Fernet
        # 암호화 키: 환경변수 또는 SECRET_KEY 기반 생성
        raw = os.environ.get("ENCRYPT_KEY", "") or os.environ.get("SECRET_KEY", "dev-secret")
        key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        return Fernet(key)
    except ImportError:
        return None


def encrypt_api_key(plain_key: str) -> str:
    """API 키를 암호화하여 저장용 문자열로 반환"""
    if not plain_key:
        return ""
    f = _get_fernet()
    if f:
        return "fernet:" + f.encrypt(plain_key.encode()).decode()
    # fallback: base64 obfuscation (암호화 아님, 단순 난독화)
    return "b64:" + base64.b64encode(plain_key.encode()).decode()


def decrypt_api_key(enc_key: str) -> str:
    """저장된 암호화 API 키를 복호화하여 반환"""
    if not enc_key:
        return ""
    if enc_key.startswith("fernet:"):
        f = _get_fernet()
        if f:
            try:
                return f.decrypt(enc_key[7:].encode()).decode()
            except Exception:
                return ""
    if enc_key.startswith("b64:"):
        try:
            return base64.b64decode(enc_key[4:]).decode()
        except Exception:
            return ""
    return ""


def mask_api_key(plain_key: str) -> str:
    """화면 표시용 마스킹 (sk-ant-...XXXX 형태)"""
    if not plain_key or len(plain_key) < 8:
        return "****"
    return plain_key[:8] + "..." + plain_key[-4:]
