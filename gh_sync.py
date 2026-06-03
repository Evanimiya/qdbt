#!/usr/bin/env python3
"""
GitHub 자동 동기화 스크립트.

Claude에서 파일을 수정한 후 이 스크립트를 실행하면
변경된 모든 파일이 GitHub에 자동으로 push됩니다.

사용법:
    python gh_sync.py                         # 변경사항 자동 감지 후 push
    python gh_sync.py "feat: 비교 화면 수정"  # 커밋 메시지 직접 지정
    python gh_sync.py --status                # 현재 상태만 확인

설정:
    환경변수 GITHUB_TOKEN 또는 스크립트 실행 시 입력
"""
import os
import sys
import subprocess
from datetime import datetime


REPO_URL  = "https://github.com/Evanimiya/qdbt.git"
BRANCH    = "main"


def run(cmd, capture=False, check=True):
    result = subprocess.run(
        cmd, shell=True, capture_output=capture, text=True
    )
    if check and result.returncode != 0:
        print(f"❌ 오류: {result.stderr or result.stdout}")
        sys.exit(1)
    return result


def git_init_if_needed():
    if not os.path.exists(".git"):
        print("📁 Git 저장소 초기화...")
        run("git init")
        run("git branch -M main")

        # remote 설정
        token = get_token()
        auth_url = REPO_URL.replace("https://", f"https://{token}@")
        run(f'git remote add origin "{auth_url}"')
        print("✅ Git 초기화 완료")
    else:
        # token이 갱신될 수 있으니 remote URL 업데이트
        token = get_token()
        auth_url = REPO_URL.replace("https://", f"https://{token}@")
        run(f'git remote set-url origin "{auth_url}"')


def get_token():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN 환경변수가 없습니다.")
        print("Replit Secrets에 GITHUB_TOKEN을 추가하거나 지금 입력하세요.")
        token = input("GitHub Personal Access Token: ").strip()
        if not token:
            print("❌ 토큰이 필요합니다.")
            sys.exit(1)
    return token


def get_status():
    result = run("git status --porcelain", capture=True, check=False)
    return result.stdout.strip()


def get_changed_files():
    status = get_status()
    if not status:
        return []
    files = []
    for line in status.splitlines():
        state = line[:2].strip()
        fname = line[3:].strip()
        files.append((state, fname))
    return files


def auto_commit_message(changed_files):
    """변경된 파일 목록으로 커밋 메시지 자동 생성"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not changed_files:
        return f"chore: sync {now}"

    # 파일 경로에서 주요 변경 영역 추출
    areas = set()
    for _, f in changed_files:
        if "templates" in f:       areas.add("UI")
        elif "blueprints" in f:    areas.add("routes")
        elif "db/" in f:           areas.add("DB")
        elif "extractors" in f:    areas.add("extractor")
        elif "reports" in f:       areas.add("reports")
        elif "auth" in f:          areas.add("auth")
        elif "parsers" in f:       areas.add("parsers")
        elif "docs" in f or "CHANGELOG" in f: areas.add("docs")
        elif f in ("main.py", "requirements.txt", ".replit"): areas.add("config")

    area_str = ", ".join(sorted(areas)) if areas else "misc"
    n = len(changed_files)
    return f"chore: update {area_str} ({n} files) [{now}]"


def sync(commit_msg=None):
    git_init_if_needed()

    # git config (Replit 환경에서 필요)
    run('git config user.email "claude-sync@qdbt.local"', check=False)
    run('git config user.name "QDBT Sync"', check=False)

    changed = get_changed_files()
    if not changed:
        print("✅ 변경사항 없음 — 이미 최신 상태입니다.")
        return

    print(f"\n📝 변경된 파일 ({len(changed)}개):")
    for state, fname in changed:
        icon = "✚" if "A" in state or "?" in state else "✎" if "M" in state else "✖"
        print(f"   {icon} {fname}")

    # 스테이징
    run("git add -A")

    # 커밋
    msg = commit_msg or auto_commit_message(changed)
    run(f'git commit -m "{msg}"')
    print(f"\n💬 커밋: {msg}")

    # Push
    print("\n🚀 GitHub에 push 중...")
    result = run(f"git push origin {BRANCH}", capture=True, check=False)
    if result.returncode != 0:
        # 첫 push인 경우
        if "rejected" in result.stderr or "no upstream" in result.stderr:
            run(f"git push -u origin {BRANCH}")
        else:
            print(f"❌ Push 실패:\n{result.stderr}")
            sys.exit(1)

    print(f"✅ 완료! https://github.com/Evanimiya/qdbt")


def show_status():
    git_init_if_needed()
    result = run("git log --oneline -5", capture=True, check=False)
    print("=== 최근 커밋 5개 ===")
    print(result.stdout or "  (아직 커밋 없음)")

    changed = get_changed_files()
    if changed:
        print(f"\n=== 미커밋 변경사항 ({len(changed)}개) ===")
        for state, fname in changed:
            print(f"  {state:2s} {fname}")
    else:
        print("\n✅ 모든 파일이 GitHub와 동기화됨")


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--status" in args:
        show_status()
    elif args:
        # 첫 인자를 커밋 메시지로 사용
        sync(commit_msg=" ".join(args))
    else:
        sync()
