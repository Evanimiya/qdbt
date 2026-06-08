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

        uid = session.get("user_id", "")
        llm = get_user_llm_settings(uid) if uid else {}

        return {
            "app_version":   VERSION,
            "app_status":    STATUS,
            "api_available": bool(llm.get("api_key")),
            "current_user":  current_user(),
            "is_logged_in":  is_logged_in(),
            "has_role":      has_role,
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
