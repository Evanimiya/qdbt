# 입찰 데이터 관리 시스템 (QDBT)

> **버전**: v0.6.0-test · 테스트 단계

한국 IT 입찰 견적서를 LLM으로 자동 추출·정규화하고, 입찰별·품목별 비교 분석 및 가격 이력을 관리하는 Flask 기반 웹 시스템.

## 주요 기능

- **파일 업로드 + 자동 추출** — XLSX/PDF/DOCX를 Claude/GPT API로 구조화
- **입찰 내 비교** — N개사 항목별 최저가/최고가 비교 + Excel 보고서
- **품목 카탈로그** — 표준 품목 등록, 별칭(동의어) 관리
- **LLM 카탈로그 매칭** — 라인 아이템 → 표준 품목 자동 매칭 + 담당자 검수
- **가격 이력** — 매칭 확정 시 자동 생성, 품목별 이력 조회
- **권한 관리** — admin / manager / viewer-detail / viewer-summary 4단계
- **LLM Provider 선택** — 사용자별 Claude 또는 GPT 선택 + 개인 API 키 관리

## 기술 스택

- **백엔드**: Python 3.11 / Flask 3.0 / SQLite
- **LLM**: Anthropic Claude 또는 OpenAI GPT (사용자별 선택)
- **프론트엔드**: Tailwind CSS (CDN) / Pretendard 폰트
- **파서**: openpyxl / pdfplumber / python-docx
- **배포**: Replit

## 빠른 시작

```bash
# 1. 저장소 클론
git clone https://github.com/Evanimiya/qdbt.git
cd qdbt

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 환경변수 설정 (선택 — 앱 내 프로필에서도 설정 가능)
export ADMIN_EMAIL=admin@yourcompany.com
export ADMIN_PASSWORD=your-secure-password

# 4. 실행
python main.py
# → http://localhost:5000
```

## Replit에서 실행

1. **Create Repl → Import from GitHub** → `Evanimiya/qdbt`
2. **Run** 버튼 클릭
3. 초기 로그인 후 **⚙ 내 프로필**에서 본인 API 키 입력

## API 키 관리

- API 키는 **각 사용자가 개별 입력** (공유하지 않음)
- 로그인 후 **⚙ 내 프로필 / API 키** 메뉴에서 설정
- Claude (`sk-ant-...`) 또는 GPT (`sk-...`) 선택 가능
- 키는 Fernet 암호화로 DB에 저장, 타인에게 노출되지 않음

## 샘플 데이터

`data/samples/`에 테스트용 더미 입찰서 9개 포함:

| 파일 | 형식 | 입찰 |
|------|------|------|
| A상사_입찰서.xlsx | XLSX | BID-001 데이터센터 |
| B제조_견적서.xlsx | XLSX | BID-001 |
| C공업_견적서.pdf | PDF | BID-001 |
| D엔지니어링_입찰서.xlsx | XLSX | BID-001 |
| E시스템즈_입찰서.pdf | PDF | BID-001 |
| F글로벌_견적서.xlsx | XLSX | BID-001 |
| G테크_견적서.xlsx | XLSX | BID-002 스마트팩토리 |
| H솔루션_견적서.pdf | PDF | BID-002 |
| I컨설팅_견적서.docx | DOCX | BID-002 |

## 프로젝트 구조

```
qdbt/
├── main.py                    # 진입점 (Flask 서버 + DB 초기화)
├── requirements.txt
├── data/
│   └── samples/               # 더미 입찰서 9개 (테스트용)
├── docs/
│   ├── CHANGELOG.md           # 버전별 변경사항
│   └── LLM_PROVIDER_DESIGN.md # LLM 추상화 설계
└── src/
    ├── config.py              # 공통 설정
    ├── auth/
    │   ├── auth.py            # 세션 인증 + 권한 데코레이터
    │   └── crypto.py          # API 키 암호화/복호화
    ├── db/
    │   ├── schema.py          # DB 스키마 (9개 테이블)
    │   └── queries.py         # 비즈니스 쿼리
    ├── extractors/
    │   ├── llm_provider.py    # LLM 추상 인터페이스
    │   ├── providers/         # Claude / GPT 구현체
    │   ├── llm_extractor.py   # 추출 실행
    │   ├── pipeline.py        # 업로드→추출→저장
    │   └── matcher.py         # 카탈로그 매칭 LLM
    ├── parsers/               # XLSX / PDF / DOCX 파서
    ├── reports/               # Excel 비교 보고서
    └── web/
        ├── app.py             # Flask 앱 팩토리
        ├── blueprints/        # auth/projects/bids/submissions
        │                      # compare/admin/profile/catalog
        └── templates/         # HTML (Tailwind CSS)
```

## 권한 체계

| 역할 | 권한 |
|------|------|
| `admin` | 전체 관리 + 사용자 추가/삭제 |
| `manager` | 프로젝트/입찰 생성, 파일 업로드, 매칭 확정 |
| `viewer-detail` | 라인 아이템(단가/수량) 전체 조회 |
| `viewer-summary` | 프로젝트/입찰 합계만 조회 |

## 버전 관리

- `v0.x.y-test` — 운영 시작 전 테스트 단계 (현재)
- `v1.0.0` 이후 — 운영 시작, Semantic Versioning 적용

변경사항은 [docs/CHANGELOG.md](docs/CHANGELOG.md) 참조.

## 향후 계획

- [ ] Phase 2-C: bid_watchlist 기반 가격 이력 검색 범위 제한
- [ ] 다중 파일 업로드 (한 번에 여러 업체 파일)
- [ ] viewer-detail / viewer-summary 접근 제어 실제 구현
- [ ] **v1.0.0**: 운영 시작
