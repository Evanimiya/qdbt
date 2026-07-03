"""
Flask 앱 팩토리.

Blueprint 구조:
  auth_bp   /auth/         로그인·로그아웃
  proj_bp   /              프로젝트 목록·생성
  bid_bp    /bids/         입찰 상세·생성
  sub_bp    /submissions/  제출서 업로드·상세
  cmp_bp    /compare/      비교 분석
  admin_bp  /admin/        사용자 관리 (admin 전용)
"""
import os
import sys
from pathlib import Path
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, render_template, session, g
from config import VERSION, STATUS, ANTHROPIC_API_KEY, ROOT


def create_app():
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
    app.permanent_session_lifetime = timedelta(hours=8)
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    # 쿠키 없는 토큰 세션 사용 — Replit iframe third-party 쿠키 차단 우회
    # before_request 에서 ?_t= 파라미터로 세션을 매 요청마다 복원

    # ── 매 요청마다 토큰으로 세션 복원 ─────────────
    from flask import request as _req, g as _g
    @app.before_request
    def restore_session_from_token():
        from auth.token_session import get_token_data
        token = (_req.args.get('_t') or
                 _req.form.get('_t') or
                 _req.cookies.get('qdbt_t', ''))
        if token:
            data = get_token_data(token)
            if data:
                from flask import session as _sess
                _sess['user_id']    = data['user_id']
                _sess['user_name']  = data['user_name']
                _sess['user_role']  = data['user_role']
                _sess['user_email'] = data['user_email']
                _g.auth_token = token

    # ── 커스텀 Jinja2 필터 ─────────────────────────
    import json as _json

    @app.template_filter("fromjson")
    def fromjson_filter(value):
        try:
            return _json.loads(value or "[]")
        except Exception:
            return []

    @app.template_filter("enumerate")
    def enumerate_filter(iterable, start=0):
        return list(enumerate(iterable, start=start))

    @app.template_filter("from_json_array")
    def from_json_array_filter(value):
        """JSON 배열 문자열 → 파이썬 리스트 (별칭 목록 등)."""
        try:
            v = _json.loads(value or "[]")
            return v if isinstance(v, list) else []
        except Exception:
            return []

    @app.template_filter("from_json_list")
    def from_json_list_filter(value):
        """JSON 배열 문자열 → 쉼표 구분 문자열 (편집 입력값·존재확인용)."""
        try:
            v = _json.loads(value or "[]")
            if isinstance(v, list):
                return ", ".join(str(x) for x in v)
            return ""
        except Exception:
            return ""

    @app.template_filter("leaf")
    def leaf_filter(value):
        """경로 문자열에서 잎(마지막 세그먼트)만. '재료비 > 전장/제어부 > 전장/제어부' → '전장/제어부'."""
        if not value:
            return value
        s = str(value)
        if ">" in s:
            seg = s.replace(" > ", ">").split(">")[-1].strip()
            return seg or s
        return s.strip()

    @app.template_filter("treepath")
    def treepath_filter(value):
        """경로를 트리 표시용으로 정규화(구분자 ' › ' 통일). 경로가 아니면 빈 문자열."""
        if not value:
            return ""
        s = str(value)
        if ">" not in s:
            return ""
        parts = [p.strip() for p in s.replace(" > ", ">").split(">") if p.strip()]
        return " › ".join(parts)

    def _norm_seg(x):
        """트리 세그먼트 비교용 정규화(공백 제거·소문자)."""
        import re as _re
        return _re.sub(r"\s+", "", str(x or "")).lower()

    _CUR_SYMBOL = {"KRW": "₩", "USD": "$", "CNY": "¥", "JPY": "¥", "EUR": "€"}

    @app.template_filter("cursym")
    def cursym_filter(currency):
        """통화 코드 → 기호. 미등록 통화는 코드 그대로 + 공백."""
        c = str(currency or "KRW").upper()
        return _CUR_SYMBOL.get(c, c + " ")

    @app.template_filter("treepath_above")
    def treepath_above_filter(path, name=""):
        """품명 '바로 위 단계'까지로 통일된 트리 경로.

        업체마다 path의 잎이 품명과 같기도(자재>서버>GPU서버) 다르기도(자재>서버) 하다.
        품명과 (정규화 후) 같은 잎을 제거하여, 모든 업체가 '품명 위 단계'까지만
        일관되게 보이도록 한다. 잎이 품명과 다르면 이미 품명 위이므로 그대로 둔다.
        """
        if not path:
            return ""
        s = str(path)
        if ">" not in s:
            return ""
        parts = [p.strip() for p in s.replace(" > ", ">").split(">") if p.strip()]
        if not parts:
            return ""
        # 품명과 같은 잎(마지막) 제거 → 품명 바로 위까지
        if name and _norm_seg(parts[-1]) == _norm_seg(name):
            parts = parts[:-1]
        return " › ".join(parts)

    # ── Blueprint 등록 ──────────────────────────
    from web.blueprints.auth      import bp as auth_bp
    from web.blueprints.projects   import bp as proj_bp
    from web.blueprints.bids       import bp as bid_bp
    from web.blueprints.submissions import bp as sub_bp
    from web.blueprints.compare    import bp as cmp_bp
    from web.blueprints.admin      import bp as admin_bp
    from web.blueprints.profile    import bp as profile_bp
    from web.blueprints.catalog    import bp as catalog_bp

    app.register_blueprint(auth_bp,    url_prefix="/auth")
    app.register_blueprint(proj_bp,    url_prefix="/")
    app.register_blueprint(bid_bp,     url_prefix="/bids")
    app.register_blueprint(sub_bp,     url_prefix="/submissions")
    app.register_blueprint(cmp_bp,     url_prefix="/compare")
    app.register_blueprint(admin_bp,   url_prefix="/admin")
    app.register_blueprint(profile_bp, url_prefix="/profile")
    app.register_blueprint(catalog_bp, url_prefix="/catalog")

    # ── 템플릿 전역 변수 ────────────────────────
    @app.context_processor
    def inject_globals():
        from auth.auth import current_user, is_logged_in, has_role
        from db.queries import get_user_llm_settings
        from extractors.providers import list_providers

        uid = session.get("user_id", "")
        llm = get_user_llm_settings(uid) if uid else {}

        return {
            "app_version":   VERSION,
            "app_status":    STATUS,
            "api_available": bool(llm.get("api_key")),
            "current_user":  current_user(),
            "is_logged_in":  is_logged_in(),
            "has_role":      has_role,
            "providers":     list_providers(),
            "current_llm":   llm,
        }

    # ── 캐시 비활성화 (Replit iframe 미리보기에서 옛 페이지 표시 방지) ──
    @app.after_request
    def add_no_cache_headers(resp):
        if os.environ.get("FLASK_ENV") != "production":
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

    # ── 오류 핸들러 ─────────────────────────────
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403,
                               message="접근 권한이 없습니다."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404,
                               message="페이지를 찾을 수 없습니다."), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", code=500,
                               message=f"서버 오류가 발생했습니다."), 500

    return app
