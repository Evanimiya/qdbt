# Changelog

## v0.9.4 (2026-06-25) — 코드 기반 추출 아키텍처

### 새 추출 아키텍처
- 코드 기반 추출 엔진 (열 매핑 → 결정론적 추출, LLM path 꼬임 해결)
- 인터랙티브 열 매핑 화면 (열 역할 지정 + 비교 단위 선택)
- 업로드 → 열 매핑 추출 흐름 (옛 자동 추출 제거)

### 추출 편집
- subtotal 제외, 매핑 설정 기억, 아이템 삭제
- Special Nego 수기 입력 (별도 항목, 합계·비교 반영, 재추출 유지)

### 비교/클러스터링
- 비교 단위(묶음) 기반 클러스터링·비교
- 같은 이름 항목 경로 구분 (클러스터 멤버 포함)
- 공급가액 = 트리 = 라인 합계 일치

### 수정
- path 구분자 '/' 보존 (분류명 안 깨짐)
- 엔진/화면 path 생성 일치

# QDBT Changelog

---

## [0.8.8] — 2026-06-12

### Fixed
- **클러스터링 페이지 500 에러** (`clusters.html`)
  - Jinja2 `{% if %}` 블록 안에서 `\"accepted\"` 형태의 백슬래시 이스케이프 사용
    → Jinja2 파서가 `unexpected char '\\'` 오류 발생
  - `\"accepted\"` → `'accepted'` 로 교체 (2곳: 재검토·삭제 confirm 메시지)

---

## [0.8.7] — 2026-06-12

### Fixed
- **클러스터링 실행 시 "API 키 없음" 오류** (`compare.py`, `catalog.py`)
  - `g.auth_data`는 `before_request`에서 설정되지 않아 항상 `{}` → `uid = ""` → API 키 조회 실패
  - `compare.py`, `catalog.py` 전체 `auth_data.get("user_id")` 패턴 (총 20여 개)을
    `session.get("user_id", "")` 로 일괄 교체
  - `compare.py`에 top-level `session, g` import 추가

### Changed
- **클러스터 선택 UI** (`clusters.html`)
  - "선택" 버튼 방식 → 명시적 **체크박스** (`w-4 h-4 accent-blue-600`) 로 교체
  - 체크 시 카드에 파란 링 강조 (`ring-2 ring-blue-400`)
  - "전체 선택" 체크박스 + 라벨 형태로 상단 배치

---

## [0.8.6] — 2026-06-12

### Fixed
- **클러스터 삭제/재검토 오류** (`catalog_clusterer.py`)
  - `reopen_cluster`, `delete_cluster`, `reset_bid_clusters` 세 함수에서
    `match_status = NULL` 로 설정하던 코드가 `NOT NULL` 제약 위반으로 실패하던 버그 수정
  - `match_status = 'pending'` 으로 변경 (DEFAULT 값과 일치)

### Changed
- **클러스터 선택 UI 개선** (`clusters.html`)
  - 체크박스 → 명시적 **"선택" / "✓ 선택됨"** 토글 버튼으로 교체
  - 선택 시 카드 테두리 파란색 강조 (시각 피드백)
  - "전체 선택" / "선택 해제" 버튼 추가
  - 병합 confirm 메시지에 대상 클러스터 이름 표시
- **업체별 비교 페이지 클러스터 리셋 버튼 추가** (`bid.html`)
  - 클러스터가 존재할 때 상단 헤더에 "🗑 클러스터 리셋" 버튼 노출

---

## [0.8.5] — 2026-06-12

### Added
- **확정 클러스터 수정/삭제 지원**
  - `catalog_clusterer.py`: `reopen_cluster`, `delete_cluster`, `reset_bid_clusters` 함수 추가
  - `catalog.py`: `reopen_cluster_route`, `delete_cluster_route`, `reset_clusters`, `clusters_bulk_action` 라우트 추가
  - `compare.py`: `reset_clusters_from_compare`, `reopen_cluster_from_compare`, `delete_cluster_from_compare` 라우트 추가
- **클러스터 단위 선택 (병합용)**
  - `clusters.html`: 클러스터별 선택 UI, 전체 선택, 일괄 병합/삭제 액션바
- **클러스터링 리셋**
  - 유사 품목 클러스터링 페이지 및 업체별 비교 페이지에 입찰 전체 클러스터 초기화 버튼 추가
- **모든 상태 클러스터 액션 버튼 노출**
  - `clusters.html`, `cluster_detail.html`, `bid.html`: 확정/거부/보류 클러스터에도 재검토·삭제 버튼 추가

---

## [0.8.1 ~ 0.8.4] — 2026-06-11

### Added
- **클러스터링 엔진 (`catalog_clusterer.py`)**
  - `run_clustering`, `accept_cluster`, `reject_cluster`, `hold_cluster`, `merge_clusters`, `rename_cluster` 함수
- **클러스터 관련 라우트 (`catalog.py`, `compare.py`)**
  - 클러스터 목록/상세/수락/거부/보류/병합/이름변경 라우트
- **템플릿 (`clusters.html`, `cluster_detail.html`)**
  - 클러스터 목록 뷰 (상태별 필터, 입찰별 필터)
  - 클러스터 상세 뷰 (멤버 추가/제외, 이름 변경, 수락/거부/보류 액션)
- **업체별 비교 페이지 (`bid.html`)**
  - 클러스터 배지 표시, 클러스터 내 품목 하이라이트, 클러스터 관리 링크

---

> 이 파일은 Claude가 커밋 단위로 관리합니다.
> 다운로드 요청 시 `qdbt/CHANGELOG.md` 를 참조하세요.
