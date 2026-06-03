"""
세션 기반 인증 + 권한 데코레이터.

- 로그인: 이메일/비밀번호 → Flask session
- 권한: @require_role("manager") 데코레이터
- 비밀번호: bcrypt 해시 (없으면 sha256 fallback)
"""
import hashlib
import functools
from flask import session, redirect, url_for, flash, abort
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.queries import get_user_by_email, get_user, update_user_login

ROLE_LEVEL = {
    "admin":          4,
    "manager":        3,
    "viewer-detail":  2,   # 라인 아이템(단가/수량) 조회 가능
    "viewer-summary": 1,   # 프로젝트/입찰 합계만 조회
}


# ─── 비밀번호 ───────────────────────────────────

def hash_password(password: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        # fallback: sha256 (개발/테스트용)
        return "sha256:" + hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    if hashed.startswith("sha256:"):
        return hashed == "sha256:" + hashlib.sha256(password.encode()).hexdigest()
    try:
        import bcrypt
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


# ─── 세션 헬퍼 ─────────────────────────────────

def login_user(user_row):
    """사용자 정보를 세션에 저장"""
    session.permanent = True
    session["user_id"]   = user_row["user_id"]
    session["user_name"] = user_row["name"]
    session["user_role"] = user_row["role"]
    session["user_email"] = user_row["email"]
    update_user_login(user_row["user_id"])


def logout_user():
    session.clear()


def current_user():
    """현재 로그인된 사용자 dict (없으면 None)"""
    uid = session.get("user_id")
    if not uid:
        return None
    return get_user(uid)


def is_logged_in():
    return "user_id" in session


def current_role():
    return session.get("user_role", "")


def has_role(required: str) -> bool:
    """현재 사용자가 required 이상의 권한을 가지는지"""
    return ROLE_LEVEL.get(current_role(), 0) >= ROLE_LEVEL.get(required, 99)


# ─── 데코레이터 ────────────────────────────────

def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not is_logged_in():
            flash("로그인이 필요합니다.", "warning")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapper


def require_role(role: str):
    """@require_role("manager") — manager 이상만 접근 가능"""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if not is_logged_in():
                flash("로그인이 필요합니다.", "warning")
                return redirect(url_for("auth.login"))
            if not has_role(role):
                flash(f"'{role}' 이상의 권한이 필요합니다.", "error")
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ─── 로그인 처리 ───────────────────────────────

def attempt_login(email: str, password: str):
    """
    로그인 시도.
    반환: (success: bool, error_msg: str | None)
    """
    user = get_user_by_email(email.strip().lower())
    if not user:
        return False, "이메일 또는 비밀번호가 올바르지 않습니다."
    if not verify_password(password, user["password_hash"]):
        return False, "이메일 또는 비밀번호가 올바르지 않습니다."
    login_user(user)
    return True, None
