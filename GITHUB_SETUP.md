# GitHub 연동 설정 가이드

## 1회성 초기 설정

### Step 1: Replit Secrets에 토큰 저장

Replit에서:
1. 좌측 사이드바 → **Tools** → **Secrets** (자물쇠 아이콘)
2. **New Secret** 클릭
3. Key: `GITHUB_TOKEN`
4. Value: `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`
5. **Add Secret** 클릭

### Step 2: 첫 push (Replit Shell에서 한 번만 실행)

```bash
python gh_sync.py "feat: 초기 커밋 — v0.3.0-test"
```

완료되면 https://github.com/Evanimiya/qdbt 에서 파일이 보입니다.

---

## 이후 일상 사용법

### Claude에서 코드 수정 후 → Replit Shell에서 실행

```bash
# 변경사항 자동 감지 후 push (커밋 메시지 자동 생성)
python gh_sync.py

# 커밋 메시지 직접 지정
python gh_sync.py "feat: 비교 화면에 차트 추가"
python gh_sync.py "fix: 업로드 오류 수정"
python gh_sync.py "docs: CHANGELOG 업데이트"

# 현재 상태만 확인 (push 안 함)
python gh_sync.py --status
```

### 버전 태그 붙이기 (마일스톤마다)

```bash
git tag v0.4.0-test
git push origin v0.4.0-test
```

---

## 커밋 메시지 규칙 (권장)

| 접두사 | 용도 | 예시 |
|--------|------|------|
| `feat:` | 새 기능 | `feat: 품목 카탈로그 Phase 2` |
| `fix:` | 버그 수정 | `fix: 업로드 파일 크기 제한 오류` |
| `docs:` | 문서 변경 | `docs: CHANGELOG v0.4.0-test 업데이트` |
| `chore:` | 설정/잡무 | `chore: requirements.txt 업데이트` |
| `refactor:` | 리팩토링 | `refactor: 쿼리 모듈 분리` |

---

## 작동 흐름 전체

```
Claude 대화에서 코드 수정
        ↓
Replit Shell: python gh_sync.py
        ↓
변경 파일 자동 감지 → git add → git commit → git push
        ↓
https://github.com/Evanimiya/qdbt 에 반영
        ↓
Replit은 항상 GitHub과 동일한 상태
```
