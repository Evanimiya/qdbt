# QDBT Changelog

이 프로젝트의 모든 주요 변경 사항을 이 파일에 기록합니다.

형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르며,
버전은 [유의적 버전(SemVer)](https://semver.org/lang/ko/)을 준수합니다.

## [0.9.87] - 2026-07-07

### Fixed
- 업체별 비교 엑셀 추출 시 클러스터 시트에 금액이 아닌 단가가 들어가던 문제 (amount 기준으로 수정)

### Changed
- 엑셀 보고서 구조 재편: 요약 → 클러스터 → 업체별 세부(각 업체 별도 탭, 전체 항목 테이블 구조)
- 카테고리별 비교 시트 제거 (클러스터·업체별 세부로 대체)

## [0.9.86] - 2026-07-07

### Fixed
- 비교 단위 트리에서 항목 삭제 시 화면이 갱신되지 않던 문제 (존재하지 않는 rebuildTree 참조 조건 제거, 삭제 성공 시 항상 새로고침)
- 업체별 비교에서 범주 이동 시 항상 '자재'로 이동되던 문제 (moveForm 내 name="category" 중복 필드 제거)
- 비교 단위 트리에 환율 적용된 단가가 표시되지 않던 문제 (잎 노드에 "단가 × 수량" 표기 추가)

### Added
- 다중 시트 누적 추출 백엔드 (삭제 1회 + 시트별 매핑 누적 삽입, map_config v2)
- 합계 검증 공통 모듈 (core/totals.py — 4종 불변식 단일 창구)
- item_match 테이블 (견적 원본/비교 결과 물리적 분리, 되먹임 분리)
- schema_meta 테이블 (1회성 보정 추적)
- amount_orig 컬럼 (단가 없는 외화 행의 원통화 보존)
- 환율 삭제 기능 (통화 환율 제거·원통화 복귀)

### Changed
- 환율 재계산 시 amount = 단가 × 수량 불변식 보존
- 도메인 보정 마이그레이션 1회성화 (schema_meta 가드)
- 클러스터 순서 드래그 시 행 그룹 전체 이동 (테이블 섞임 해결)

카테고리 규칙: `Added`(추가) · `Changed`(변경) · `Fixed`(수정) ·
`Deprecated`(폐기 예정) · `Removed`(제거) · `Security`(보안).
각 항목은 변경이 일어난 파일을 괄호로 명시하고, 필요 시 원인·조치를 함께 적습니다.

> 이 파일은 커밋 단위로 관리합니다. 새 변경은 최상단 `[Unreleased]`에 쌓고,
> 릴리스 시 버전·날짜를 부여해 아래로 내립니다.

---

## [Unreleased]

### 예정
- **그룹 D — 이모지 정리**: 상태·의미 이모지는 텍스트·기호로 대체, 장식용(로봇 아이콘 등) 제거 (약 28개 파일)

---

## [0.9.7] — 2026-07-03 — 비교표 UX 고도화 · 통화 파이프라인 · 클러스터링 정합

v0.9.6에서 이어진 13개 개선 항목(그룹 A·B·C)과, 실사용 중 발견된 통화·클러스터링
결함의 근본 수정을 포함한다. 그룹 D(CHANGELOG·이모지)는 진행 중.

### Added

- **기준정보 도메인 바인딩** (그룹 A) (`queries.py`, `admin.py`, `projects.py`, `admin/attr_defs.html`, `projects/detail.html`)
  - `list_attr_defs(active_only, domain=None)`에 도메인 필터 추가 — `'공통'` 항목은 전 도메인 공유, 도메인 전용 항목은 해당 도메인 프로젝트에만 노출
  - 기준정보 관리 화면에 소속 도메인 선택(추가·편집)·표시 열 추가
  - 프로젝트 상세 → "기준정보 항목 관리" 이동 버튼, 관리 화면 → "프로젝트로 돌아가기" 복귀 링크(`return_project`)로 왕복 동선 완성
  - 항목 관리(추가·삭제·변경)와 값 입력을 구분하는 안내 문구 추가
- **분류 배지 인라인 드롭다운** (그룹 B) (`compare/bid.html`)
  - 배지 클릭 시 중앙 모달 대신 배지 바로 아래 인라인 드롭다운 노출(`#qdCatInlinePop`), 선택 즉시 분류 이동
  - 분류 목록·현재 분류 표시(✓)·구분선·"＋ 신규 분류 만들기" 포함, 기존 `qdCatChangeForm` 재사용
- **분류 순서 재정렬·되돌리기** (그룹 B) (`schema.py`, `queries.py`, `compare.py`, `compare/bid.html`)
  - `bids.category_order`(JSON) 컬럼 추가 — 입찰별 분류 표시 순서 저장
  - `category_order_for_bid`가 저장 순서 우선 적용(없으면 분류관리 기본순서), `set_bid_category_order`·`reset_bid_category_order` 추가
  - 분류 헤더 드래그 핸들(⋮⋮)로 순서 변경, "↺ 기본순서로" 버튼으로 분류관리 기본순서 복원
  - 라우트 `reorder_categories_route`, `reset_categories_order_route` 추가
- **분류 내 금액 정렬** (그룹 B) (`compare/bid.html`)
  - 분류 헤더의 "금액↑/금액↓" 버튼으로 해당 분류 내부 클러스터를 금액순 정렬(분류 경계 유지)
  - 클러스터 행에 `data-cl-amount`(최저 총액) 부여, `qdSortCatByAmount`로 그룹 단위 재배치
- **트리 경로 통일 필터** (그룹 C) (`app.py`)
  - `treepath_above(path, name)` 필터 신설 — 품명과 같은 잎(마지막 세그먼트)을 제거해 모든 업체가 "품명 바로 위 단계"까지 일관 표시
- **통화(외화) 처리 파이프라인** (`extract_by_mapping.py`, `submissions/column_map.html`, `queries.py`, `schema.py`, `app.py`, `compare/bid.html`)
  - 추출 역할에 `currency`(통화)·`price_krw`(원화단가)·`amount_krw`(원화금액) 추가 (`INFO_ROLES`)
  - 통화 코드 정규화($·¥·€·₩ → ISO), 환율 역산(`환율 = 원화 ÷ 통화`), 원화 확정(견적서 원화열 우선)
  - `suggest_column_mapping`에 통화·원화 헤더 키워드 + `ROLE_PRIORITY`(구체적 역할 우선 매칭) 추가 — "Amount (CCY)"↔"Amount (KRW)" 구분
  - 열 매핑 화면 드롭다운(`ROLES`)에 통화·원화단가·원화금액 옵션 추가
  - `submission_items.fx_rate_used`(항목별 적용 환율) 컬럼 추가, `insert_items_bulk`에 저장
  - `cursym` 필터(KRW·USD·CNY·EUR·JPY 기호 매핑) 추가, 비교표에 원화기준(₩) + 외화 2단(환율·통화단가) 표시

### Changed

- **비교표 열 정렬** (그룹 C) (`compare/bid.html`)
  - `table-layout:auto` → `fixed` + `<colgroup>`으로 헤더·본문 열 너비 강제 정합, `width:max-content`로 최저열을 마지막 업체열에 밀착
  - 본문 td의 개별 width 지정 제거(colgroup 위임), 열 너비 조절(`qdSetColW`)은 CSS 변수로 유지
- **트리 표시 위치·통일** (그룹 C) (`compare/bid.html`)
  - 트리 경로를 품명 옆(인라인)에서 품명 아래 줄로 이동, 3개 지점(업체 셀·미분류 행·미분류 세부) 모두 `treepath_above` 적용
- **클러스터 정렬 드래그 피드백** (그룹 C) (`compare/bid.html`)
  - 드래그 중 원본 행 강조(`qd-sort-src`), 드롭 위치 표시선(`qd-sort-over-top/bot`), grabbing 커서(`qd-sorting`), `dragend` 정리 추가
- **제출서 대표 환율 집계** (`queries.py`)
  - `recompute_subtotal`이 항목별 통화·환율에서 제출서 대표 환율(최빈값)·외화 포함 여부(`has_usd_items`) 집계 — 비교 화면 환율 표시용
- **환율 표시 일반화** (`compare/bid.html`)
  - 기존 USD 하드코딩(`== 'USD'`) 제거, 모든 외화(USD·CNY·EUR 등)에 통화 기호 자동 매핑으로 표시

### Fixed
- 환율 재계산 시 amount = 단가 × 수량 불변식 보존 (독립 환산으로 라인 정합이 깨지던 버그 수정)

- **클러스터링 잎 매칭 정합** (`queries.py`)
  - 원인: `list_submission_items_for_clustering`의 묶기 매칭이 잎의 상위 경로(`path`)만으로 사용자 지정 `compare_units`(잎까지 완전 경로)를 대조 → 트리가 깊은 업체(예: `자재 > 서버 장비 > GPU 가속 서버`)에서 잎이 공통 상위(`서버 장비`)로 뭉쳐, 얕은 업체의 잎과 레벨 불일치 → 최초 클러스터링 실패·오병합(한 묶음이 여러 클러스터에 중복 배정)
  - 조치: 매칭 기준을 잎의 완전 경로(`path + " > " + name_normalized`)로 변경 → 사용자가 지정한 비교 단위(잎/중분류)가 그대로 반영, 데이터는 항상 잎까지 보존
- **열 매핑 화면 500 오류** (`extract_by_mapping.py`)
  - 원인: `column_map` 라우트가 import하는 `suggest_column_mapping` 함수가 이전 `detect_total_rows` 추가 시 같은 위치에서 덮어써져 소실 → `ImportError`
  - 조치: 원본에서 헤더 키워드 매칭 로직 복원(`detect_total_rows` 앞에 재배치)
- **분류 배지 클릭 무반응** (`compare/bid.html`)
  - 원인: `onclick`의 `{{ ...|tojson }}`이 HTML 속성 큰따옴표와 충돌해 `SyntaxError`
  - 조치: 배지를 `data-*` 속성 + 이벤트 위임 방식으로 전환(특수문자·따옴표에 견고)
- **통화 비교 이종통화 왜곡** (`queries.py`, `schema.py`, `compare/bid.html`)
  - 원인: 통화 추출 미완성으로 원화 미저장 → 이종통화(CNY vs KRW)가 원화 환산 없이 비교됨
  - 조치: 추출 시 원화 확정·환율 역산 저장, 소계·클러스터 금액이 원화(`amount`) 기준으로 집계되도록 정합 → 원화 기준 통일 비교

---

## [0.9.6] — 2026-07 — 도메인 확장 기반 · 비교표 정비

전 도메인(설비·용역·IT 등) 확장을 위한 데이터 모델과 비교표 기반 정비.

### Added

- **도메인 모델** (`schema.py`, `queries.py`)
  - `domains` 테이블, `projects.domain`·`bids.domain` 컬럼 추가 — 프로젝트·입찰의 도메인 귀속
  - `project_attr_defs`·`project_attrs` 테이블 — 도메인별 기준정보 정의·값
- **분류 관리** (`admin.py`, `admin/categories.html`)
  - 카테고리 정의·별칭(`catalog_categories.aliases`) 관리 화면, 기본 표시 순서(`sort_order`) 지정
- **클러스터 표시 순서** (`schema.py`)
  - `catalog_clusters.display_order` 컬럼 추가 — 클러스터 사용자 지정 정렬

### Changed

- **비교 단위 기반 클러스터링 정비** (`catalog_clusterer.py`, `queries.py`)
  - `CLUSTER_PROMPT` 개선 — 이름 우선 매칭, 상위경로·하위구성은 보조 참조, 적극 병합, 레벨 불일치 시에만 분리
  - `list_submission_items_for_clustering`이 각 업체 `compare_units`로 묶어 반환

### Fixed

- **제출서 소프트 삭제** (`schema.py`, `queries.py`)
  - `submissions.deleted_at` 컬럼 추가 — 하드 삭제 대신 소프트 삭제로 비교·집계에서 제외

---

## [0.9.5] — 2026-06 — LLM 연동 · SSL 설정

### Added

- **LLM 제공자 설정** (`schema.py`, `llm_provider.py`)
  - `users.llm_verify_ssl` 컬럼 추가 — 폐쇄망(사내 게이트웨이) 대응 SSL 검증 토글
  - OpenAI 직접 연동 및 사내 게이트웨이 경유 연동 지원

### Changed

- 클러스터링·추출 LLM 호출 경로를 제공자 설정 기반으로 통일

---

## [0.9.4] — 2026-06-25 — 코드 기반 추출 아키텍처

### Added

- **코드 기반 추출 엔진** — 열 매핑 → 결정론적 추출로 LLM path 꼬임 해결
- **인터랙티브 열 매핑 화면** — 열 역할 지정 + 비교 단위 선택
- **업로드 → 열 매핑 추출 흐름** (옛 자동 추출 대체)
- **추출 편집** — subtotal 제외, 매핑 설정 기억, 아이템 삭제
- **Special Nego 수기 입력** — 별도 항목으로 합계·비교 반영, 재추출 시 유지
- **비교 단위(묶음) 기반 클러스터링·비교**
- **같은 이름 항목 경로 구분** (클러스터 멤버 포함)

### Changed

- 공급가액 = 트리 = 라인 합계 일치 보장

### Fixed

- path 구분자 `/` 보존 (분류명 깨짐 방지)
- 추출 엔진과 화면의 path 생성 로직 일치

---

## [0.8.8] — 2026-06-12

### Fixed

- **클러스터링 페이지 500 에러** (`clusters.html`)
  - Jinja2 `{% if %}` 블록 안의 `\"accepted\"` 백슬래시 이스케이프 → 파서 `unexpected char '\'` 오류
  - `\"accepted\"` → `'accepted'`로 교체 (재검토·삭제 confirm 2곳)

---

## [0.8.7] — 2026-06-12

### Fixed

- **클러스터링 "API 키 없음" 오류** (`compare.py`, `catalog.py`)
  - `g.auth_data`가 `before_request`에서 미설정 → `uid = ""` → API 키 조회 실패
  - `auth_data.get("user_id")` 패턴(20여 개)을 `session.get("user_id", "")`로 일괄 교체, `compare.py`에 top-level `session, g` import 추가

### Changed

- **클러스터 선택 UI** (`clusters.html`)
  - "선택" 버튼 → 명시적 체크박스(`accent-blue-600`), 체크 시 파란 링 강조, "전체 선택" 상단 배치

---

## [0.8.6] — 2026-06-12

### Fixed

- **클러스터 삭제/재검토 오류** (`catalog_clusterer.py`)
  - `reopen_cluster`·`delete_cluster`·`reset_bid_clusters`의 `match_status = NULL`이 `NOT NULL` 제약 위반 → `match_status = 'pending'`으로 변경

### Changed

- **클러스터 선택 UI** (`clusters.html`) — 체크박스 → "선택"/"✓ 선택됨" 토글, 전체 선택/해제, 병합 confirm에 대상 이름 표시
- **클러스터 리셋 버튼** (`bid.html`) — 상단 헤더에 클러스터 리셋 노출

---

## [0.8.5] — 2026-06-12

### Added

- **확정 클러스터 수정/삭제** (`catalog_clusterer.py`, `catalog.py`, `compare.py`) — `reopen_cluster`·`delete_cluster`·`reset_bid_clusters` 및 관련 라우트
- **클러스터 단위 선택(병합용)** (`clusters.html`) — 선택 UI·전체 선택·일괄 병합/삭제 액션바
- **클러스터링 리셋** — 클러스터링 페이지·비교 페이지에 입찰 전체 초기화
- **모든 상태 클러스터 액션 노출** — 확정/거부/보류에도 재검토·삭제 버튼

---

## [0.8.1 ~ 0.8.4] — 2026-06-11

### Added

- **클러스터링 엔진** (`catalog_clusterer.py`) — `run_clustering`·`accept_cluster`·`reject_cluster`·`hold_cluster`·`merge_clusters`·`rename_cluster`
- **클러스터 라우트** (`catalog.py`, `compare.py`) — 목록/상세/수락/거부/보류/병합/이름변경
- **템플릿** (`clusters.html`, `cluster_detail.html`) — 클러스터 목록·상세 뷰
- **업체별 비교 페이지** (`bid.html`) — 클러스터 배지·품목 하이라이트·관리 링크
