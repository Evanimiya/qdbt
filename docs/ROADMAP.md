# QDBT 로드맵

> 최종 업데이트: 2026-06-09
> 현재 버전: v0.6.2-test

---

## 목적

한국 IT 입찰의 세 가지 Proposal을 통합 분석하여 입찰 적정성 판단 및 가격 예측을 지원하는 시스템.

```
Commercial Proposal  ← 현재 구현 중
Technical Proposal   ← Phase 6 예정
도면                 ← Phase 6 예정
```

---

## 전체 로드맵 한눈에 보기

```
v0.6  ████████████████████████  현재
v0.7  ░░░░░░░░                  Phase 2-C (bid_watchlist)
v0.8  ░░░░░░░░░░░░              Phase 3-A (카탈로그 자동 제안)
v0.9  ░░░░░░░░░░░░░░░░          Phase 3-B/C + Phase 4
v1.0  ░░░░░░░░░░░░░░░░░░░░      운영 전환 + Phase 5
v2.0  ░░░░░░░░░░░░░░░░░░░░░░░░  Phase 6 (3-Proposal 통합)
```

---

## Phase 0: PoC 검증 ✅ 완료 (2026-05-09 ~ 05-13)

### 목표
9개 업체 견적서의 추출·매칭 정확도 검증

### 완료 항목
- [x] XLSX 파서 — 들여쓰기 계층, 병합셀, 1차원 번호체계 처리
- [x] PDF 파서 — pdfplumber 기반, 1.1.1 번호체계 처리
- [x] DOCX 파서 — gridSpan, 도장란, 다단 헤더 처리
- [x] 추출 프롬프트 v1 (`docs/prompt_extract_v1.md`) — 9개 업체 100% 정확도
- [x] 매칭 Post-validator v1/v2/v3 — F1=1.0 달성
- [x] 9개 업체 통합 DB 적재 (335 라인 아이템)
- [x] Excel 비교 보고서 2건 생성 (98개 수식, 오류 0건)

---

## Phase 1: 웹 앱 기반 구축 ✅ 완료 (2026-05-26 ~ 06-04)

### 목표
Flask 기반 웹 UI + 데이터 관리 구조 구축

### 1-A: 프로젝트 / 입찰 관리 ✅
- [x] `Project → Bid → Submission → SubmissionItem` 계층 구조
- [x] 프로젝트 생성/수정/상태 관리 (active/closed/archived)
- [x] 입찰 생성/수정/상태 관리 (open/closed/awarded/cancelled)
- [x] 프로젝트 목록 / 입찰 목록 화면

### 1-B: 파일 업로드 및 추출 ✅
- [x] XLSX/PDF/DOCX 파일 업로드
- [x] 업로드 즉시 LLM 추출 파이프라인 실행
- [x] 백그라운드 스레드 추출 (UI 블로킹 없음)
- [x] `/status.json` 폴링 — 추출 진행 상태 실시간 확인
- [x] 청크 분할 처리 — 150행 초과 시 자동 분할 (대용량 견적서 대응)
- [x] LLM Provider 추상화 — Claude / GPT 선택 가능
- [x] 사용자별 API 키 Fernet 암호화 저장
- [x] 원본 파일 열기 (`/submissions/<id>/file`)
- [x] 추출 실패 시 재추출 트리거 (`/submissions/<id>/extract`)
- [x] 제출서 소프트 삭제 / 복원 / 영구 삭제
- [x] 제출서 개별 초기화 (파일 유지, 추출 데이터 삭제)
- [x] 데모 초기화 (프로젝트 전체 제출서 초기화)

### 1-C: 인증 / 권한 ✅
- [x] 이메일/비밀번호 로그인
- [x] URL 토큰 세션 (Replit iframe 쿠키 차단 우회)
- [x] 4단계 권한 구조 정의: admin / manager / viewer-detail / viewer-summary
- [x] `@login_required`, `@require_role()` 데코레이터
- [x] 사용자 추가/목록 관리 (admin 전용)
- [x] 프로필 화면 — LLM provider/model/API 키 설정
- [ ] viewer-detail / viewer-summary 실제 접근 제어 구현 → Phase 5

### 1-D: 라인 아이템 저장 / 조회 ✅
- [x] `submission_items` 전체 저장 (line_no, depth, category, name, spec, qty, unit_price, amount)
- [x] 카테고리 필터 드롭다운
- [x] 라인 아이템 테이블 (계층 들여쓰기 표현)
- [x] 항목별 JSON API (`/submissions/<id>/items.json`)

---

## Phase 2: 비교 분석 ✅ 완료 (2026-06-04)

### 목표
입찰 내 N개사 비교 + 입찰 간 가격 이력 조회

### 2-A: 입찰 내 N개사 비교 ✅
- [x] 카테고리별 업체 가로 비교 테이블
- [x] 공급가액 기준 업체 정렬 (최저가 강조)
- [x] 항목별 최저/최고 단가 색상 구분 (녹색/빨강)
- [x] 카테고리 소계 행
- [x] Excel 비교 보고서 다운로드 (조건부 서식 포함)
- [x] 최저가 합계 컬럼 (툴팁 주의사항 포함)

### 2-B: 카탈로그 관리 ✅
- [x] 카테고리 생성/삭제 (5개 기본 카테고리 자동 생성)
- [x] 하위 카테고리 지원 (parent_id)
- [x] 품목 수동 등록 — name_canonical, aliases(동의어), spec_template, unit_std
- [x] 품목 수정 / 소프트 삭제
- [x] 품목 목록 (카테고리 필터 + 이름/별칭 검색)
- [x] 품목 상세 — 별칭 태그 표시, 가격 이력 테이블

### 2-B: LLM 매칭 ✅
- [x] `submission_items` → `catalog_items` LLM 매칭 제안
- [x] 매칭 신뢰도(confidence) 표시
- [x] 담당자 검수 화면 — 드롭다운으로 품목 변경 가능
- [x] 전체 확정 / 개별 확정
- [x] 확정 시 `price_history` 자동 생성
- [x] 품목 상세 화면에서 가격 이력 조회

### 2-C: 입찰 간 가격 이력 검색 ⚠️ 부분 구현
- [x] 품목명 텍스트 검색 (cross_project_price)
- [x] 프로젝트 필터 옵션
- [ ] `bid_watchlist` 기반 검색 범위 제한 → **v0.7 예정**
- [ ] 품목별 가격 추이 차트 → **v0.7 예정**

---

## Phase 3: 카탈로그 자동화 ❌ 미구현 (v0.8 예정)

### 목표
견적서 분석 → 카탈로그 자동 제안 → 담당자 확정 구조로 전환
(현재: 사람이 카탈로그 먼저 등록 → LLM이 연결
 목표: LLM이 그룹 제안 → 사람이 확정 → 카탈로그 자동 축적)

### 3-A: 신규 품목 자동 감지 ❌
- [ ] 추출 완료 시 카탈로그 미등록 품목 자동 감지
- [ ] "이 품목을 카탈로그에 추가할까요?" 제안 UI
- [ ] "기존 XX 품목과 유사합니다 — 연결할까요?" 제안 UI
- [ ] 제안 수락/거부/수정 워크플로우

### 3-B: 유사 품목 클러스터링 ❌
- [ ] 여러 입찰서에 걸친 유사 품목 자동 분석
- [ ] LLM 기반 품목명 + 사양 유사도 측정
- [ ] 그룹 제안 화면 ("이 3개 품목을 하나로 묶을까요?")
- [ ] 그룹 확정 시 catalog_item 자동 생성 + aliases 자동 등록

### 3-C: 그룹 승인 권한 ❌
- [ ] 카탈로그 생성/변경 승인 권한 설계 (admin only 여부 검토 중)
- [ ] 승인 요청 / 승인 처리 흐름
- [ ] 변경 이력 로그

---

## Phase 4: 적정성 판단 ❌ 미구현 (v0.9 예정)

### 목표
신규 입찰서 업로드 시 과거 데이터 기반 단가 적정성 자동 분석

### 4-A: 단가 분포 분석 ❌
- [ ] 품목별 과거 단가 분포 (min/avg/max/표준편차)
- [ ] 신규 단가와 과거 분포 비교 표시
- [ ] 이상치 자동 강조 (±기준 초과 시 경고)

### 4-B: 비교 테이블 연계 ❌
- [ ] 입찰 내 비교 테이블에 "과거 평균 단가" 컬럼 추가
- [ ] "과거 대비 +18%" 같은 편차율 표시
- [ ] 이상 단가 항목 필터

### 4-C: 적정성 리포트 ❌
- [ ] 입찰별 적정성 요약 리포트 생성
- [ ] 품목별 적정/주의/이상 분류

---

## Phase 5: 운영 전환 (v1.0.0) ❌ 미구현

### 5-A: 보안 강화
- [ ] 세션 인증 SameSite=None 쿠키로 교체 (현재 URL 토큰)
  → `docs/CHANGELOG.md` [v1.0.0] 섹션에 상세 교체 방법 기재
- [ ] ADMIN_PASSWORD 환경변수 강제화
- [ ] SECRET_KEY 환경변수 강제화

### 5-B: 접근 제어 실제 구현
- [ ] viewer-summary: 합계만 표시, 라인 아이템/단가 숨김
- [ ] viewer-detail: 라인 아이템 전체 조회
- [ ] 부서별 프로젝트 접근 제어

### 5-C: 가격 예측 (Phase 5 또는 별도)
- [ ] 사양 조합 입력 → 예상 가격 산출
- [ ] 과거 이력 기반 예측 (데이터 충분히 쌓인 후)
  → 최소 권장: 3개 이상 입찰, 10개 이상 업체 데이터

### 5-D: 성능 / UX
- [ ] 다중 파일 동시 업로드
- [ ] 파서 강화 → LLM 호출 최소화
  → `docs/CHANGELOG.md` [v1.0.0] 섹션에 방법 D 상세 기재
- [ ] 알림 기능 (마감일 임박 등)

---

## Phase 6: 3-Proposal 통합 (v2.0) ❌ 장기

### 목표
Commercial + Technical Proposal + 도면 세 가지를 통합 분석

### 6-A: Technical Proposal 분석
- [ ] 기술제안서 업로드 및 파싱
- [ ] 기술 사양 자동 추출 (LLM)
- [ ] Commercial ↔ Technical 크로스 검증
  ("견적서 서버 스펙 vs 기술제안서 서버 스펙 불일치")

### 6-B: 도면 분석
- [ ] 도면 이미지/PDF 업로드
- [ ] 치수, 재질, 구조 정보 추출 (Vision LLM)
- [ ] 도면 사양 ↔ 견적 품목 매핑

### 6-C: 통합 분석
- [ ] 3개 Proposal 통합 DB
- [ ] 통합 적정성 리포트
- [ ] 불일치 자동 감지 및 알림

---

## 관리 방법 제안

### 현재 문제
- 로드맵이 CHANGELOG와 여러 문서에 분산
- 구현 여부를 파악하려면 코드를 직접 봐야 함
- Replit 마이너 업데이트가 로드맵에 반영 안 됨

### 제안: 3계층 관리 구조

```
ROADMAP.md          ← 이 파일 (전략/방향/현황 한눈에)
    ↓
CHANGELOG.md        ← 버전별 상세 변경사항
    ↓
qdbt_for_claude.md  ← Claude 작업용 기술 가이드 (코드 구조, 주의사항)
```

### 업데이트 규칙

| 상황 | 업데이트 대상 |
|------|------------|
| 메이저 업데이트 (Claude) | ROADMAP.md + CHANGELOG.md |
| 마이너 업데이트 (Replit → Claude 동기화) | CHANGELOG.md |
| 기술 구조 변경 | qdbt_for_claude.md |
| 새 Phase 계획 확정 | ROADMAP.md |

### Claude 작업 시작 시 체크리스트

```
1. ROADMAP.md에서 현재 Phase 확인
2. qdbt_for_claude.md에서 기술 구조 확인
3. 최신 Replit 소스 동기화 여부 확인
4. 작업 완료 후 ROADMAP.md + CHANGELOG.md 업데이트
```
