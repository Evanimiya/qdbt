# Changelog

**버전 정책**:
- `v0.x.y-test` — 운영 시작 전 테스트 단계 (현재)
- `v1.0.0` 이후 — 운영 시작, [Semantic Versioning](https://semver.org/) 적용

---

## [v1.0.0] - 예정 (운영 시작 시점)

### 운영 전환 체크리스트

#### 🔐 보안 — 세션 인증 방식 교체 (필수)

**배경:**
현재 v0.6.1-test는 Replit iframe 환경의 서드파티 쿠키 차단 문제를 우회하기 위해
URL 토큰 방식(`?_t=<token>`)을 사용하고 있음.
이 방식은 토큰이 URL에 노출되어 브라우저 히스토리·서버 로그·링크 공유 시 세션 정보가 유출될 위험이 있음.

**교체 방법:** `SameSite=None` 쿠키 (설정 3줄 + 코드 정리)

```python
# src/web/app.py 에 추가
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"]   = True   # Replit은 HTTPS라 OK
app.config["SESSION_COOKIE_HTTPONLY"] = True   # XSS 방어
```

**함께 제거할 코드:**
- `src/auth/token_session.py` 전체 삭제
- `src/auth/auth.py` — `login_user()`를 기존 `session[]` 방식으로 복원
- `src/web/app.py` — `before_request` 토큰 복원 로직 제거
- `src/web/blueprints/auth.py` — localStorage 저장 HTML 제거, 기존 redirect 방식 복원
- `src/web/templates/base.html` — 토큰 자동 주입 JS 제거
- 모든 Blueprint의 `redirect(..., _t=tok)` → `redirect(...)` 으로 정리

**주의:** `SESSION_COOKIE_SECURE = True`이면 HTTP 환경(로컬 개발)에서 쿠키가 전송되지 않음.
로컬 개발 시 별도 처리 필요:
```python
# 로컬 개발 환경에서는
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
```

---

#### 📦 대용량 견적서 추출 전략 고도화 (권장)

**배경:**
현재 v0.6.x는 방법 B (청크 분할)로 대용량 견적서를 처리함.
- 150행 이하: 단일 LLM 호출
- 150행 초과: 150행씩 청크 분할 → 청크별 LLM 호출 → 결과 합산
- 1,000항목 기준: 약 7회 LLM 호출 필요

**문제점:**
- API 호출 횟수 비례 증가 → 비용/시간 증가
- 청크 경계에서 카테고리 컨텍스트 일부 손실 가능
- 각 청크의 금액 합계 검증이 복잡

**v1.0.0에서 고려할 방법 D (파서 강화):**

```
현재 (방법 B):
  파서 → 텍스트 → LLM (구조화) → DB

방법 D:
  파서 (강화) → dict 구조 (직접 추출)
                  ↓
              LLM (정규화만: 품목명 통일, 카테고리 분류)
                  ↓
              DB
```

**방법 D 구현 포인트:**
- XLSX: 행 번호 + 들여쓰기 + 병합셀 정보로 계층 구조 직접 파악 가능
- PDF: pdfplumber의 테이블 추출 기능으로 행 직접 파싱
- DOCX: 테이블 셀 구조로 직접 파싱
- LLM 역할: 전체 텍스트 구조화 → 품목명 정규화 + 카테고리 분류만
- 효과: LLM 호출 1회 고정 (항목 수 무관), 비용 90% 절감 추정

**참고 코드 위치:**
- `src/extractors/llm_extractor.py` — `CHUNK_LINE_LIMIT`, `_split_into_chunks()`
- `src/parsers/parse_xlsx.py` — 파서 강화 시작점

#### 기타 운영 전환 항목
- [ ] `ADMIN_PASSWORD` 환경변수 강제화 (기본값 제거)
- [ ] `SECRET_KEY` 환경변수 강제화
- [ ] viewer-detail / viewer-summary 접근 제어 실제 구현
- [ ] Phase 2-C: bid_watchlist 기반 가격 이력 검색 범위 제한
- [ ] 버전 상수 변경: `src/config.py` → `VERSION = "1.0.0"`, `STATUS = "production"`

---

## [v0.6.2-test] - 2026-06-08

### Replit 테스트 기반 전면 동기화

Replit에서 진행된 마이너 업데이트들을 Claude 코드베이스에 반영.
이번부터 **Replit(마이너) ↔ Claude(메이저) 워크플로우** 공식 적용.

#### 추가 (Added)
- **백그라운드 추출** (`submissions.py`)
  - `_extraction_worker()` — 별도 스레드에서 LLM 추출 실행
  - `GET /<id>/status.json` — 추출 상태 폴링 엔드포인트 (progress bar용)
- **소프트 삭제** (`db/queries.py`, `submissions.py`)
  - `submissions.deleted_at` 컬럼 추가 (schema + migrate_db)
  - `soft_delete_submission()` / `restore_submission()` DB 함수
  - `list_deleted_submissions()` — 삭제된 목록 조회
  - `POST /<id>/soft-delete` / `POST /<id>/restore` 라우트
- **제출서 개별 초기화** (`submissions.py`)
  - `reset_submission()` — 파일 유지, 추출 데이터만 삭제
  - `POST /<id>/reset` 라우트
- **no-cache 헤더** (`app.py`)
  - `after_request`에서 Replit 미리보기 캐시 문제 방지

#### 변경 (Changed)
- **token_session.py** — 인터페이스 변경
  - `create_token(dict)` → `create_token(user_id, name, role, email)`
  - `load_token()` → `get_token_data()`
  - 저장 경로: `data/sessions/` → `data/token_sessions/`
- **pipeline.py** — 재추출 시 기존 items 먼저 삭제 후 재삽입, `extraction_error` 초기화
- **app.py** — 세션 복원 시 Flask `session[]`에도 동시 저장 (하이브리드)
- **auth.py (blueprint)** — 로그아웃 시 localStorage 정리 JS 추가
- **비교 테이블** (`compare/bid.html`) — 소계 행 `colspan` 버그 수정 (두 개의 별도 `<td>`로 분리)
- **`.gitignore`** — `data/token_sessions/` 제외 추가

---

## [v0.6.1-test] - 2026-06-07

### Replit 테스트 버그픽스 + 기능 추가

#### 추가 (Added)
- **`src/auth/token_session.py`** — UUID 파일 기반 토큰 세션 (신규)
  - Replit iframe 환경에서 서드파티 쿠키 차단 문제 우회
  - `create_token()` / `load_token()` / `delete_token()`
  - 만료된 토큰 자동 정리 (`_cleanup_expired`)
- **데모 초기화 기능** (`projects.reset_demo`)
  - 프로젝트 상세 페이지 🔄 데모 초기화 버튼
  - 제출서를 pending 상태로 초기화 (원본 파일·구조 유지)
  - `reset_project_submissions()` DB 함수 추가

#### 변경 (Changed)
- **세션 인증 방식 전면 교체** (쿠키 → URL 토큰)
  - `src/auth/auth.py` — `login_user()` → 토큰 생성 + `g.auth_token` 저장
  - `src/web/app.py` — `before_request`에서 `?_t=<token>`으로 세션 복원
  - `src/web/blueprints/auth.py` — 로그인 성공 시 localStorage에 토큰 저장 HTML 반환
  - `src/web/templates/base.html` — 모든 링크/폼에 `?_t=` 자동 주입 JS
- **`src/web/blueprints/submissions.py`** 버그 수정
  - `download_file`: `sqlite3.Row.get()` 미지원 → `dict(sub).get()` 수정
  - `extract`: redirect에 `_t=tok` 파라미터 명시적 포함
- **`src/web/blueprints/projects.py`** — `reset_demo` 라우트 추가
- **`src/web/templates/projects/detail.html`** — 데모 초기화 버튼 추가
- **`.gitignore`** — `data/sessions/` 제외 추가

---

## [v0.6.0-test] - 2026-06-04

### Phase 2-B: LLM 카탈로그 매칭 + 가격 이력

#### 추가 (Added)
- **`src/extractors/matcher.py`** — 카탈로그 매칭 LLM 모듈
  - `build_match_input()` — 라인 아이템 + 카탈로그를 LLM 입력 텍스트로 구성
  - `run_llm_matching()` — LLM으로 매칭 추천 실행 (provider 무관)
  - `save_match_suggestions()` — 추천 결과를 DB에 저장 (match_status='suggested')
  - `confirm_match()` — 담당자 확정 처리 → price_history 자동 생성
- **`src/web/templates/submissions/match.html`** — 매칭 검수 화면
  - 현황 통계 (전체/추천/확정/미매칭)
  - 라인 아이템별 드롭다운으로 카탈로그 품목 선택/변경
  - 신뢰도 % 표시 (색상 구분)
  - 전체 확정 버튼 + 개별 확정

#### 변경 (Changed)
- **`src/db/queries.py`** — 매칭 관련 쿼리 추가
  - `get_match_summary()` — 제출서 매칭 현황 (pending/suggested/confirmed/unmatched)
  - `get_items_with_match()` — 매칭 정보 포함 라인 아이템 목록
  - `get_price_history()` — 품목별 가격 이력 조회
- **`src/web/blueprints/submissions.py`** — 매칭 라우트 추가
  - `POST /submissions/<id>/match` (action=run_match) — LLM 매칭 실행
  - `GET  /submissions/<id>/match` — 검수 화면
  - `POST /submissions/<id>/match/confirm` — 확정 처리
- **`src/web/templates/submissions/detail.html`** — 🔗 카탈로그 매칭 버튼 추가
- **`src/web/blueprints/catalog.py`** — item_detail에 price_history 전달
- **`src/web/templates/catalog/item_detail.html`** — 가격 이력 테이블 표시 (확정 후 자동)

#### 전체 흐름
```
파일 업로드 → 추출 완료
    ↓ "🔗 카탈로그 매칭" 버튼
LLM이 라인 아이템 ↔ 카탈로그 품목 매칭 추천
    ↓
담당자가 검수 화면에서 확정/변경/거부
    ↓
확정 시 price_history 자동 생성
    ↓
카탈로그 품목 상세에서 가격 이력 확인 가능
```

---

## [v0.5.0-test] - 2026-06-04

### Phase 2-A: 품목 카탈로그 관리

#### 추가 (Added)
- **`src/web/blueprints/catalog.py`** — 카탈로그 Blueprint
  - `GET  /catalog/` — 품목 목록 (카테고리 필터 + 이름/별칭 검색)
  - `GET/POST /catalog/items/new` — 품목 등록
  - `GET  /catalog/items/<id>` — 품목 상세 (별칭 태그 표시)
  - `GET/POST /catalog/items/<id>/edit` — 품목 수정
  - `POST /catalog/items/<id>/delete` — 품목 소프트 삭제
  - `GET  /catalog/categories` — 카테고리 관리 (추가/삭제)
  - `POST /catalog/categories/new` — 카테고리 추가
  - `POST /catalog/categories/<id>/delete` — 카테고리 삭제 (품목 없을 때만)
- **`src/web/templates/catalog/`** — 카탈로그 HTML 4개
  - `index.html` — 품목 목록 (모바일 카드 / 데스크톱 테이블)
  - `item_form.html` — 등록/수정 폼 (별칭 다중 입력)
  - `item_detail.html` — 품목 상세 + 별칭 태그
  - `categories.html` — 카테고리 관리

#### 변경 (Changed)
- **`src/db/queries.py`** — 카탈로그 CRUD 함수 추가
  - `list/get/create/update/delete_catalog_category()`
  - `list/get/create/update/delete_catalog_item()`
  - `catalog_stats()` — 품목 수, 카테고리 수, 매칭 확정 수
- **`src/web/app.py`** — catalog Blueprint 등록 + `fromjson` Jinja2 필터 추가
- **`src/web/templates/base.html`** — 사이드바에 📦 품목 카탈로그 메뉴 추가

#### 설계 원칙
- 별칭(aliases)을 JSON 배열로 저장 — 업체별 다른 표기를 통합
- 소프트 삭제 (`is_active = 0`) — 이력 보존
- Phase 2-B (LLM 매칭) 연결 준비: `submission_items.catalog_item_id` 연결 대기 중

---

## [v0.4.0-test] - 2026-06-04

### LLM Provider 추상화 (Claude + GPT, 향후 확장 가능)

#### 추가 (Added)
- **`src/extractors/llm_provider.py`** — 추상 인터페이스 (`LLMProvider`, `LLMProviderError`)
- **`src/extractors/providers/claude.py`** — Anthropic Claude 구현
- **`src/extractors/providers/gpt.py`** — OpenAI GPT 구현
- **`src/extractors/providers/__init__.py`** — Provider 레지스트리 (`PROVIDERS`, `get_provider()`, `list_providers()`)
- **`docs/LLM_PROVIDER_DESIGN.md`** — 설계 문서

#### 변경 (Changed)
- **`src/extractors/llm_extractor.py`** — provider 추상화로 완전 교체
  - `extract_with_llm(provider_id=, model=)` 파라미터 추가
  - provider 무관 동일 인터페이스 유지
- **`src/extractors/pipeline.py`** — `provider_id`, `model` 파라미터 추가
- **`src/db/schema.py`** — users 테이블 변경
  - `anthropic_api_key_enc` → `llm_api_key_enc` (provider 무관)
  - `llm_provider TEXT DEFAULT 'claude'` 추가
  - `llm_model TEXT` 추가
  - `migrate_db()` 함수 추가 (기존 DB 자동 마이그레이션)
- **`src/db/queries.py`** — `save_user_llm_settings()`, `get_user_llm_settings()` 추가
- **`src/web/blueprints/profile.py`** — provider 선택 UI 로직
- **`src/web/templates/profile/index.html`** — provider 선택 카드 UI + 모델 드롭다운 (JS 동적 변경)
- **`src/web/blueprints/submissions.py`** — LLM 설정 전체(provider+model+key) 전달
- **`src/web/app.py`** — `get_user_llm_settings()` 기반으로 변경
- **`main.py`** — 시작 시 `migrate_db()` 자동 실행
- **`requirements.txt`** — `openai>=1.30.0` 추가

#### 향후 모델 추가 방법
```
1. src/extractors/providers/gemini.py 생성
2. GeminiProvider(LLMProvider) 구현
3. providers/__init__.py PROVIDERS에 등록
→ 기존 코드 수정 없음
```

---

## [v0.3.2-test] - 2026-06-04

### 사용자별 Anthropic API 키 관리

#### 추가 (Added)
- **`src/auth/crypto.py`** — API 키 암호화/복호화 유틸
  - Fernet 대칭키 암호화 (cryptography 라이브러리)
  - 없으면 base64 fallback (개발 환경용)
  - `encrypt_api_key()`, `decrypt_api_key()`, `mask_api_key()`
- **`src/web/blueprints/profile.py`** — 프로필 Blueprint
  - `GET /profile/` — 내 정보 + API 키 설정 화면
  - `POST /profile/api-key` — API 키 저장 (sk-ant- 형식 검증)
  - `POST /profile/api-key/delete` — API 키 삭제
- **`src/web/templates/profile/index.html`** — 프로필 화면
  - 현재 키 상태 (설정됨/미설정) + 마스킹 표시
  - 키 입력 폼 (암호화 저장)

#### 변경 (Changed)
- **`src/db/schema.py`** — `users` 테이블에 `anthropic_api_key_enc` 컬럼 추가
- **`src/db/queries.py`** — `save_user_api_key()`, `get_user_api_key()` 함수 추가
- **`src/extractors/llm_extractor.py`** — `api_key` 파라미터 추가 (사용자 키 우선)
- **`src/extractors/pipeline.py`** — `run_extraction(api_key=)` 파라미터 추가
- **`src/web/blueprints/submissions.py`** — 업로드 시 사용자 키 조회 후 LLM 전달
- **`src/web/app.py`** — `api_available`을 사용자 개인 키 기준으로 변경
- **`src/web/templates/base.html`** — 사이드바에 "⚙ 내 프로필 / API 키" 메뉴 추가
- **`src/web/templates/submissions/upload.html`** — 키 미설정 시 프로필로 안내
- **`requirements.txt`** — `cryptography>=42.0.0` 추가

#### 설계 원칙
- 환경변수(`ANTHROPIC_API_KEY`) 의존성 완전 제거
- 각 사용자가 본인 키를 프로필에서 입력 → Fernet 암호화 저장
- 파일 업로드 시 업로드한 사용자의 키로 LLM 호출
- 키 미설정 사용자는 업로드 불가 (프로필 설정 안내)

---

## [v0.3.1-test] - 2026-06-04

### 보안 설계 반영 (구조만, UI는 Phase 2)

#### 변경 (Changed)
- **role 4단계로 세분화** (`src/db/schema.py`, `src/auth/auth.py`)
  - 기존: `admin / manager / viewer`
  - 변경: `admin / manager / viewer-detail / viewer-summary`
  - `viewer-detail`: 라인 아이템(단가/수량) 전체 조회 가능
  - `viewer-summary`: 프로젝트/입찰 합계 데이터만 조회 가능
- **사용자 추가 폼** role 선택지 업데이트 (`admin/user_form.html`)
- **사용자 목록** role 배지 색상 추가 (`admin/users.html`)

#### 추가 (Added)
- **`bid_watchlist` 테이블** (`src/db/schema.py`)
  - 입찰별 가격 이력 비교 대상 품목을 명시적으로 지정하는 테이블
  - 전체 카탈로그 무제한 검색이 아닌, 이번 입찰의 비교 대상 품목만 검색
  - 현재는 구조만 생성 (UI + 쿼리는 Phase 2에서 활성화)
  - `bid_id + catalog_item_id` UNIQUE 제약으로 중복 방지

#### 기술 메모
- 실제 접근 제어 로직(화면별 role 체크)은 아직 미구현
- Phase 2에서 `viewer-detail` vs `viewer-summary` 분기 처리 예정
- `bid_watchlist`는 Phase 2 카탈로그 구현 시 함께 활성화

---

## [v0.3.0-test] - 2026-05-27

### 전체 재설계 (목적 재정의)

#### 배경
기존 PoC 구조가 실제 운영 목적(이후 입찰 참조, 품목 이력, 권한 관리)에 맞지 않아 DB 스키마와 UI를 처음부터 재설계.

#### 추가 (Added)
- **새 DB 스키마**: `projects → bids → submissions → submission_items` 계층 구조
  - `projects`: 발주 프로젝트 (동적 생성)
  - `bids`: 입찰 회차 (한 프로젝트에 여러 번 입찰 가능)
  - `submissions`: 업체별 제출서
  - `submission_items`: 라인 아이템 (전체 저장)
  - `catalog_categories`, `catalog_items`, `price_history`: Phase 2 준비 (구조만)
- **권한 관리**: `users` 테이블 + admin / manager / viewer 3단계
  - 세션 기반 인증 (이메일/비밀번호)
  - `@require_role("manager")` 데코레이터
- **Flask Blueprint 구조**: auth / projects / bids / submissions / compare / admin
- **입찰 내 비교** (`/compare/bid/<bid_id>`):
  - 카테고리별 N개사 항목별 가격 피벗
  - 최저가 녹색·최고가 빨강 조건부 서식
- **품목 가격 검색** (`/compare/search`):
  - 품목명으로 전체 프로젝트 가격 이력 조회 (Phase 1 임시)
  - 프로젝트 필터 옵션
- **프로젝트/입찰 동적 생성**: UI에서 직접 생성 (하드코딩 제거)
- **첫 실행 자동 초기화**: DB 없으면 자동 생성 + 기본 admin 계정

#### 변경 (Changed)
- 기존 `BID-001`, `BID-002` 하드코딩 제거 → 동적 UUID 기반
- 파서/추출 모듈 재사용, 경로만 config로 정리
- `main.py`가 bootstrap(DB 초기화 + admin 계정 생성) 포함

#### 기술 부채
- 품목 카탈로그 (Phase 2) 미구현 — 현재 name_normalized 기반 검색으로 임시 처리
- 사용자 비밀번호 변경 UI 없음
- 입찰 간 가격 이력은 catalog 연결 없이 단순 텍스트 검색

---

## [v0.2.0-test] - 2026-05-27

### Flask 웹 UI + 파일 업로드 자동 추출

#### 추가
- Flask + Tailwind CSS 모바일 반응형 웹 UI
- 파일 업로드 후 Anthropic API 자동 추출
- 버전 관리 시스템 (`__version__.py`, `CHANGELOG.md`)
- 테스트 배지 (🧪 v0.x.y-test / ✅ v1.0.0+)

---

## [v0.1.0-test] - 2026-05-26

### Replit 빌드 변환

#### 추가
- PoC 코드를 Replit에서 실행 가능한 단일 프로젝트로 정리
- `src/` 하위 모듈 분리 (parsers, validators, db, reports)
- `main.py` CLI 진입점
- Post-validator v1+v2+v3 통합 (`pipeline.py`)

---

## [PoC] - 2026-05-09 ~ 2026-05-13

- Round 1~4: 9개 업체 추출 100%, 매칭 F1=1.0 검증
- 통합 데모: 9개 업체 → DB → Excel 비교 보고서 2건
