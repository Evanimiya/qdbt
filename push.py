#!/usr/bin/env python3
"""
QDBT — GitHub 동기화 스크립트 (로컬 PC용)

Claude에서 받은 파일을 GitHub에 업로드합니다.

사용법:
  python push.py                        # 변경사항 감지 후 push
  python push.py "feat: 설명"           # 커밋 메시지 직접 지정
  python push.py --status               # 상태만 확인 (push 안 함)
  python push.py --init                 # 최초 1회: git 초기화 + 전체 push
"""
import os, sys, subprocess
from datetime import datetime

REPO = "https://github.com/Evanimiya/qdbt.git"
BRANCH = "main"


def run(cmd, capture=False, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=capture, text=True)
    if check and r.returncode != 0:
        print(f"❌ {r.stderr or r.stdout}")
        sys.exit(1)
    return r


def init():
    """최초 1회: git 초기화 + 원격 연결"""
    if not os.path.exists(".git"):
        print("📁 Git 초기화...")
        run("git init")
        run("git branch -M main")
        run(f'git remote add origin {REPO}')
    else:
        # remote URL 확인
        r = run("git remote get-url origin", capture=True, check=False)
        if r.returncode != 0:
            run(f'git remote add origin {REPO}')

    run('git config user.email "qdbt@local"', check=False)
    run('git config user.name "QDBT"', check=False)
    print("✅ Git 설정 완료")


def status():
    r = run("git status --porcelain", capture=True, check=False)
    lines = [l for l in r.stdout.strip().splitlines() if l]
    return lines


def push(msg=None):
    init()

    changed = status()
    if not changed:
        print("✅ 변경사항 없음 — 이미 최신입니다.")
        return

    print(f"\n📝 변경 파일 {len(changed)}개:")
    for l in changed:
        icon = {"A": "✚", "M": "✎", "D": "✖", "?": "✚"}.get(l[0], "•")
        print(f"   {icon} {l[3:]}")

    run("git add -A")

    if not msg:
        areas = set()
        for l in changed:
            f = l[3:]
            if "templates" in f:    areas.add("UI")
            elif "blueprints" in f: areas.add("routes")
            elif "db/" in f:        areas.add("DB")
            elif "extractors" in f: areas.add("extractor")
            elif "reports" in f:    areas.add("reports")
            elif "auth" in f:       areas.add("auth")
            elif "docs" in f or "CHANGELOG" in f: areas.add("docs")
            elif f in ("main.py", "requirements.txt", ".replit"): areas.add("config")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        area_str = ", ".join(sorted(areas)) or "misc"
        msg = f"chore: update {area_str} ({len(changed)} files) [{now}]"

    run(f'git commit -m "{msg}"')
    print(f"\n💬 커밋: {msg}")

    print("🚀 GitHub push 중...")
    r = run(f"git push origin {BRANCH}", capture=True, check=False)
    if r.returncode != 0:
        if "upstream" in r.stderr or "no upstream" in r.stderr:
            run(f"git push -u origin {BRANCH}")
        else:
            print(f"❌ Push 실패:\n{r.stderr}")
            sys.exit(1)

    print(f"✅ 완료! https://github.com/Evanimiya/qdbt")


def show_status():
    init()
    r = run("git log --oneline -5", capture=True, check=False)
    print("=== 최근 커밋 ===")
    print(r.stdout.strip() or "  (아직 없음)")

    changed = status()
    if changed:
        print(f"\n=== 미커밋 변경 ({len(changed)}개) ===")
        for l in changed:
            print(f"  {l}")
    else:
        print("\n✅ GitHub와 동기화됨")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--status" in args:
        show_status()
    elif "--init" in args:
        init()
        push("feat: 초기 커밋 — v0.3.0-test")
    elif args:
        push(" ".join(args))
    else:
        push()
