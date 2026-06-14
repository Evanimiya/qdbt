# QDBT 로컬 실행 메모

## 서버 켜기 (터미널 1)
cd ~/downloads/qdbt
source .venv/bin/activate
python main.py
# → http://127.0.0.1:5001 접속

## 포트가 막혔다고 나오면 (5001 점유 강제 종료)
lsof -ti:5001 | xargs kill -9

## 떠 있는 서버 전부 끄기
pkill -f "python main.py"

## Claude Code 켜기 (터미널 2, .venv 불필요)
cd ~/downloads/qdbt
claude

## 로그인 정보
# 주소: http://127.0.0.1:5001
# 이메일: admin@company.com
# 비밀번호: qdbt1234

## 환경변수 (이미 ~/.zshrc 에 저장됨 — 자동 적용)
# PORT=5001
# SECRET_KEY=qdbt-local-dev-secret-key-change-me-2026
# ADMIN_PASSWORD=qdbt1234
