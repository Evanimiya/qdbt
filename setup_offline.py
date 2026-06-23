"""
Windows 오프라인 실행 설정 스크립트
------------------------------------
인터넷이 연결된 상태에서 한 번만 실행하세요.
  python setup_offline.py

실행 후에는 인터넷 없이도 프로그램이 정상 표시됩니다.
"""

import urllib.request
import os
import shutil
import sys

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR  = os.path.join(BASE_DIR, "src", "web", "static", "vendor")
TAILWIND_JS = os.path.join(VENDOR_DIR, "tailwind.js")
BASE_HTML   = os.path.join(BASE_DIR, "src", "web", "templates", "base.html")

TAILWIND_URL    = "https://cdn.tailwindcss.com"
CDN_LINE        = '<script src="https://cdn.tailwindcss.com"></script>'
LOCAL_LINE      = '<script src="/static/vendor/tailwind.js"></script>'
PRETENDARD_LINE = '<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css" rel="stylesheet">'

def step(msg):
    print(f"\n[{'OK' if '완료' in msg or '이미' in msg else '..'}] {msg}")

def download_tailwind():
    if os.path.exists(TAILWIND_JS) and os.path.getsize(TAILWIND_JS) > 100_000:
        step("tailwind.js 이미 존재 — 건너뜁니다.")
        return True
    os.makedirs(VENDOR_DIR, exist_ok=True)
    step("tailwind.js 다운로드 중...")
    try:
        urllib.request.urlretrieve(TAILWIND_URL, TAILWIND_JS)
        size_kb = os.path.getsize(TAILWIND_JS) // 1024
        step(f"tailwind.js 다운로드 완료 ({size_kb} KB)")
        return True
    except Exception as e:
        print(f"  오류: {e}")
        print("  인터넷 연결을 확인하세요.")
        return False

def patch_base_html():
    with open(BASE_HTML, encoding="utf-8") as f:
        content = f.read()

    if LOCAL_LINE in content:
        step("base.html 이미 로컬 경로로 설정됨 — 건너뜁니다.")
        return

    # 백업
    shutil.copy(BASE_HTML, BASE_HTML + ".bak")

    # CDN → 로컬 교체
    content = content.replace(CDN_LINE, LOCAL_LINE)
    # Pretendard CDN 제거 (없어도 맑은 고딕으로 표시됨)
    content = content.replace(PRETENDARD_LINE, "")
    # 폰트 fallback 조정
    content = content.replace(
        "body { font-family:'Pretendard',system-ui,sans-serif; }",
        "body { font-family:'Pretendard','맑은 고딕','Apple SD Gothic Neo',system-ui,sans-serif; }"
    )

    with open(BASE_HTML, "w", encoding="utf-8") as f:
        f.write(content)
    step("base.html 패치 완료  (원본 백업: base.html.bak)")

def main():
    print("=" * 50)
    print("  QDBT 오프라인 설정")
    print("=" * 50)

    ok = download_tailwind()
    if not ok:
        sys.exit(1)

    patch_base_html()

    print("\n" + "=" * 50)
    print("  설정 완료!")
    print("  이제 인터넷 없이도 실행 가능합니다.")
    print()
    print("  실행 방법:")
    print("    set PYTHONPATH=src")
    print("    python main.py")
    print("=" * 50)

if __name__ == "__main__":
    main()
