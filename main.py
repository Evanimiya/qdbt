"""
입찰 데이터 관리 시스템 v2 — 서버 진입점

실행: python main.py
접속: http://localhost:5000
"""
import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from web.app import create_app
from db.schema import init_db
from db.queries import create_user, get_user_by_email
from auth.auth import hash_password


def bootstrap():
    """첫 실행 시 DB 초기화 + 기본 관리자 계정 생성"""
    from config import DB_PATH
    if not DB_PATH.exists():
        print("  DB 초기화 중...")
        init_db()

        # 기본 admin 계정 (운영 전 반드시 변경)
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@company.com")
        admin_pass  = os.environ.get("ADMIN_PASSWORD", "admin1234!")
        if not get_user_by_email(admin_email):
            create_user(
                email=admin_email, name="관리자",
                role="admin", dept="시스템",
                password_hash=hash_password(admin_pass),
            )
            print(f"  기본 관리자 계정 생성: {admin_email}")
            print(f"  비밀번호: {admin_pass}  ← 운영 시 반드시 변경하세요!")


def main():
    bootstrap()
    app  = create_app()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    print("=" * 60)
    print("  입찰 데이터 관리 시스템 v0.3.0-test")
    print(f"  http://0.0.0.0:{port}")
    print("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
