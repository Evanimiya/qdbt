# 입찰 데이터 관리 시스템 (QDBT)

> **버전**: v0.3.0-test · 테스트 단계 (운영 시작 시 v1.0.0)

한국 IT 입찰 견적서를 LLM으로 자동 추출·정규화하고, 입찰별·품목별 비교 분석을 제공하는 Flask 기반 웹 시스템.

## 주요 기능

- **파일 업로드 + 자동 추출** — XLSX/PDF/DOCX를 Claude API로 구조화
- **입찰 내 비교** — 같은 입찰에 참여한 N개사를 카테고리별·항목별 비교
- **입찰 간 가격 검색** — 품목명으로 전체 프로젝트의 가격 이력 조회
- **권한 관리** — admin / manager / viewer 3단계
- **Excel 보고서** — 조건부 서식(최저가 녹색·최고가 빨강) 자동 생성

## 기술 스택

- **백엔드**: Python 3.11 / Flask 3.0 / SQLite
- **추출 LLM**: Anthropic Claude (Sonnet)
- **프론트엔드**: Tailwind CSS (CDN) / Pretendard 폰트
- **파서**: openpyxl / pdfplumber / python-docx
- **배포**: Replit

## 빠른 시작

```bash
# 1. 저장소 클론
git clone https://github.com/<your-username>/qdbt.git
cd qdbt

# 2. 의존성 설치
pip install -r requirements.txt

# 3. API 키 설정
export ANTHROPIC_API_KEY=sk-ant-...

# 4. 실행
python main.py
# → http://localhost:5000
# → 초기 로그인: admin@company.com / admin1234!
```

## Replit에서 실행

1. **Import from GitHub** → 이 저장소 URL 입력
2. Tools → Secrets → `ANTHROPIC_API_KEY` 추가
3. **Run** 버튼 클릭

## 프로젝트 구조

```
qdbt/
├── main.py                # 진입점 (Flask 서버 + 초기화)
├── requirements.txt
├── src/
│   ├── config.py          # 공통 설정 (경로, 환경변수)
│   ├── db/
│   │   ├── schema.py      # DB 스키마 (8개 테이블)
│   │   └── queries.py     # 비즈니스 쿼리 (비교·검색 포함)
│   ├── auth/
│   │   └── auth.py        # 세션 인증 + 권한 데코레이터
│   ├── parsers/           # 파일 형식별 파서
│   ├── extractors/        # LLM 추출 + 업로드 파이프라인
│   ├── reports/           # Excel 보고서 생성
│   └── web/
│       ├── app.py         # Flask 앱 팩토리
│       ├── blueprints/    # 라우트 (projects/bids/submissions/compare/admin)
│       └── templates/     # HTML 템플릿 (Tailwind CSS)
└── data/
    ├── uploads/           # 업로드된 파일 (gitignore)
    └── extractions/       # 추출 JSON (gitignore)
```

## 버전 관리 정책

- **v0.x.y-test**: 운영 시작 전 테스트 버전
- **v1.0.0 이후**: Semantic Versioning (MAJOR.MINOR.PATCH)

변경사항은 [CHANGELOG.md](docs/CHANGELOG.md) 참조.

## 로드맵

- [ ] Phase 2: 품목 카탈로그 + LLM 매칭 추천 + 담당자 확정 UI
- [ ] 입찰 간 가격 이력 (catalog 기반)
- [ ] 사용자 비밀번호 변경 UI
- [ ] 알림 기능 (마감일 임박 등)
- [ ] **v1.0.0**: 운영 시작
