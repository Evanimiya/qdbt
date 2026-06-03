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
from config import VERSION, STATUS, ANTHROPIC_API_KEY


def create_app():
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
    app.permanent_session_lifetime = timedelta(hours=8)
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

    # ── Blueprint 등록 ──────────────────────────
    from web.blueprints.auth   import bp as auth_bp
    from web.blueprints.projects import bp as proj_bp
    from web.blueprints.bids   import bp as bid_bp
    from web.blueprints.submissions import bp as sub_bp
    from web.blueprints.compare import bp as cmp_bp
    from web.blueprints.admin  import bp as admin_bp

    app.register_blueprint(auth_bp,  url_prefix="/auth")
    app.register_blueprint(proj_bp,  url_prefix="/")
    app.register_blueprint(bid_bp,   url_prefix="/bids")
    app.register_blueprint(sub_bp,   url_prefix="/submissions")
    app.register_blueprint(cmp_bp,   url_prefix="/compare")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # ── 템플릿 전역 변수 ────────────────────────
    @app.context_processor
    def inject_globals():
        from auth.auth import current_user, is_logged_in, has_role
        return {
            "app_version":   VERSION,
            "app_status":    STATUS,
            "api_available": bool(ANTHROPIC_API_KEY),
            "current_user":  current_user(),
            "is_logged_in":  is_logged_in(),
            "has_role":      has_role,
        }

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
