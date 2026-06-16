"""
쿠키 없는 서버사이드 토큰 세션.
Replit iframe 환경에서 third-party 쿠키가 차단되는 문제를 우회.
토큰은 UUID 파일로 저장되며 URL 파라미터 ?_t=<token> 으로 전달.
"""
import uuid, json, time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import ROOT

TOKEN_DIR = ROOT / "data" / "token_sessions"
TOKEN_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_TTL = 8 * 3600  # 8시간


def create_token(user_id: str, user_name: str, user_role: str, user_email: str) -> str:
    token = str(uuid.uuid4())
    (TOKEN_DIR / f"{token}.json").write_text(json.dumps({
        "user_id":    user_id,
        "user_name":  user_name,
        "user_role":  user_role,
        "user_email": user_email,
        "expires":    time.time() + TOKEN_TTL,
    }))
    return token


def get_token_data(token: str) -> dict | None:
    if not token or len(token) < 10:
        return None
    path = TOKEN_DIR / f"{token}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if time.time() > data.get("expires", 0):
        path.unlink(missing_ok=True)
        return None
    return data


def delete_token(token: str):
    if token:
        (TOKEN_DIR / f"{token}.json").unlink(missing_ok=True)
