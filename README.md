# 입찰 데이터 관리 시스템 (QDBT)

> **버전**: v0.8.5-test · 테스트 단계

한국 IT·설비·용역 입찰 견적서를 LLM으로 자동 추출·정규화하고, 입찰별·품목별 비교 분석 및 가격 이력을 관리하는 Flask 기반 웹 시스템.

## 주요 기능

- **파일 업로드 + 자동 추출** — XLSX/PDF/DOCX를 Claude/GPT API로 구조화 (백그라운드 처리 + 실시간 진행률 표시)
- **입찰 내 비교** — N개사 항목별 총액/단가 비교, 최저가/최고가 하이라이트
- **LLM 클러스터링** — 동일 품목을 LLM이 자동 그룹화 → 업체별 가격 나란히 비교
- **벤치마크 참조** — 타 프로젝트의 과거 낙찰가와 현재 입찰 가격 자동 대조 (동일 프로젝트 제외)
- **품목 카탈로그** — 표준 품목 등록, 별칭(동의어) 관리, 카탈로그 연결 배지 표시
- **LLM 카탈로그 매칭** — 라인 아이템 → 표준 품목 자동 매칭 + 담당자 검수
- **가격 이력** — 매칭 확정 시 자동 생성, 품목별 이력 조회
- **Excel 보고서** — 비교 시트 + 클러스터 시트 포함 다운로드
- **권한 관리** — admin / manager / viewer-detail / viewer-summary 4단계
- **LLM Provider 선택** — 사용자별 Claude 또는 GPT 선택 + 개인 API 키 관리 (Fernet 암호화)

## 기술 스택

- **백엔드**: Python 3.11 / Flask 3.0 / SQLite (WAL 모드)
- **LLM**: Anthropic Claude 또는 OpenAI GPT (사용자별 선택)
- **프론트엔드**: Tailwind CSS (CDN) / Pretendard 폰트 / Jinja2
- **파서**: openpyxl / pdfplumber / python-docx
- **보안**: PBKDF2 (비밀번호) / Fernet (API 키 암호화)
- **배포**: Replit

## 빠른 시작

```bash
# 1. 저장소 클론
git clone https://github.com/Evanimiya/qdbt.git
cd qdbt

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 환경변수 설정 (선택 — 앱 내 프로필에서도 설정 가능)
export ADMIN_PASSWORD=your-secure-password

# 4. 실행
PYTHONPATH=src python main.py
# → http://localhost:5000
```

## Replit에서 실행

1. **Secrets** 탭에서 `ADMIN_PASSWORD` 설정
2. **Run** 버튼 클릭 (`qdbt` workflow 실행)
3. 로그인 후 **⚙ 내 프로필**에서 LLM API 키 입력

## API 키 관리

- API 키는 **각 사용자가 개별 입력** (공유하지 않음)
- 로그인 후 **⚙ 내 프로필** → **API 키** 메뉴에서 설정
- Claude (`sk-ant-...`) 또는 GPT (`sk-...`) 선택 가능
- 키는 Fernet 암호화로 DB에 저장, 타인에게 노출되지 않음

## 전체 흐름

```
파일 업로드 (XLSX/PDF/DOCX)
    ↓ 백그라운드 LLM 추출 (진행률 폴링)
라인 아이템 저장 (품목명·규격·수량·단가·금액)
    ↓ "🔗 카탈로그 매칭" 실행
LLM이 표준 품목과 자동 매칭 → 담당자 검수·확정
    ↓ 확정 시 price_history 자동 기록
입찰 비교 페이지
    ├── 미분류 행: 정규화 품목명 기준 나열
    ├── 클러스터: LLM이 동일 품목 묶음 → 총액/단가 비교
    └── 벤치마크: 타 프로젝트 과거 가격과 자동 대조
```

## 프로젝트 구조

```
qdbt/
├── main.py                      # 진입점 (Flask 서버 + DB 초기화/마이그레이션)
├── requirements.txt
├── data/
│   ├── qdbt.db                  # SQLite DB (WAL 모드)
│   ├── uploads/                 # 업로드된 원본 파일
│   ├── extractions/             # LLM 추출 원본 JSON
│   ├── token_sessions/          # 세션 토큰 파일 (gitignore)
│   └── samples/                 # 더미 입찰서 (테스트용)
├── docs/
│   ├── CHANGELOG.md             # 버전별 변경사항
│   ├── ROADMAP.md               # 향후 계획
│   └── LLM_PROVIDER_DESIGN.md  # LLM 추상화 설계
└── src/
    ├── config.py                # 공통 설정 (VERSION 등)
    ├── auth/
    │   ├── auth.py              # 세션 인증 + 권한 데코레이터
    │   ├── crypto.py            # API 키 암호화/복호화
    │   └── token_session.py     # URL 토큰 세션 (Replit iframe 우회)
    ├── db/
    │   ├── schema.py            # DB 스키마 + 마이그레이션
    │   └── queries.py           # 비즈니스 쿼리 전체
    ├── extractors/
    │   ├── llm_provider.py      # LLM 추상 인터페이스
    │   ├── providers/           # Claude / GPT 구현체
    │   ├── llm_extractor.py     # 청크 분할 추출 (150행 단위)
    │   ├── catalog_clusterer.py # LLM 클러스터링 (동일 품목 자동 묶음)
    │   ├── matcher.py           # 카탈로그 매칭 LLM
    │   └── pipeline.py          # 업로드 → 추출 → 저장 파이프라인
    ├── parsers/                 # XLSX / PDF / DOCX 파서
    ├── reports/
    │   └── excel_report.py      # 비교 + 클러스터 Excel 보고서
    └── web/
        ├── app.py               # Flask 앱 팩토리
        ├── blueprints/          # auth / projects / bids / submissions
        │                        # compare / admin / profile / catalog
        └── templates/           # HTML (Tailwind CSS)
```

## 권한 체계

| 역할 | 권한 |
|------|------|
| `admin` | 전체 관리 + 사용자 추가/삭제 |
| `manager` | 프로젝트/입찰 생성, 파일 업로드, 매칭 확정, 클러스터링 |
| `viewer-detail` | 라인 아이템(단가/수량) 전체 조회 |
| `viewer-summary` | 프로젝트/입찰 합계만 조회 |

## DB 주요 테이블

| 테이블 | 역할 |
|--------|------|
| `projects` | 프로젝트 |
| `bids` | 입찰 회차 |
| `submissions` | 업체별 제출서 (소프트 삭제 지원) |
| `submission_items` | 추출된 라인 아이템 |
| `catalog_items` | 표준 품목 |
| `catalog_clusters` | LLM 클러스터 (pending / accepted / rejected / held) |
| `catalog_cluster_members` | 클러스터 ↔ 라인 아이템 연결 |
| `price_history` | 확정 매칭 기반 가격 이력 |
| `users` | 사용자 (역할 + 암호화 API 키) |

## 비교 페이지 주요 기능

- **총액 + 단가 이중 표시** — 각 셀에 총액(주) + `단가 × 수량`(부) 표시
- **최저가 초록 / 최고가 빨강** 하이라이트
- **📋 카탈로그 배지** — 클러스터 품목이 카탈로그에 등록된 경우 표시
- **벤치마크** — 동일 프로젝트 제외, 타 프로젝트 과거 최저가 자동 참조
- **클러스터 상태 관리** — 확정(accepted) / 보류(held) / 거부(rejected) / 대기(pending)
- **Excel 다운로드** — 업체별 비교 시트 + 클러스터 요약 시트

## 샘플 데이터

`data/samples/`에 테스트용 더미 입찰서 포함:

| 파일 | 형식 | 입찰 |
|------|------|------|
| A상사_입찰서.xlsx | XLSX | BID-001 |
| B제조_견적서.xlsx | XLSX | BID-001 |
| C공업_견적서.pdf | PDF | BID-001 |
| D엔지니어링_입찰서.xlsx | XLSX | BID-001 |
| E시스템즈_입찰서.pdf | PDF | BID-001 |
| F글로벌_견적서.xlsx | XLSX | BID-001 |
| G테크_견적서.xlsx | XLSX | BID-002 |
| H솔루션_견적서.pdf | PDF | BID-002 |
| I컨설팅_견적서.docx | DOCX | BID-002 |

## 버전 관리

- `v0.x.y-test` — 운영 시작 전 테스트 단계 (현재: **v0.8.5-test**)
- `v1.0.0` 이후 — 운영 시작, Semantic Versioning 적용

변경사항은 [docs/CHANGELOG.md](docs/CHANGELOG.md) 참조.

## v1.0.0 운영 전환 시 주요 체크리스트

- [ ] 세션 방식 교체: URL 토큰(`?_t=`) → `SameSite=None` 쿠키
- [ ] `ADMIN_PASSWORD`, `SECRET_KEY` 환경변수 강제화 (기본값 제거)
- [ ] viewer-detail / viewer-summary 접근 제어 실제 구현
- [ ] Phase 2-C: 가격 이력 검색 범위 제한 (bid_watchlist 기반)
- [ ] LLM 추출 고도화: 방법 D (파서 강화 + LLM 정규화만)
