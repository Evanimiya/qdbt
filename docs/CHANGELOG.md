# Changelog

**버전 정책**:
- `v0.x.y-test` — 운영 시작 전 테스트 단계 (현재)
- `v1.0.0` 이후 — 운영 시작, [Semantic Versioning](https://semver.org/) 적용

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
