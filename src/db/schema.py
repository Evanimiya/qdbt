"""
입찰 데이터 관리 시스템 v2 — DB 스키마

설계 원칙:
1. project → bid → submission → item 계층으로 실제 업무 흐름 반영
2. catalog_items가 입찰 간 가격 비교의 핵심 연결고리 (Phase 2에서 활성화)
3. 권한 관리: users + roles (4단계)
   - admin          : 전체 관리 + 사용자 추가/삭제
   - manager        : 프로젝트/입찰 생성, 파일 업로드, 전체 데이터 접근
   - viewer-detail  : 라인 아이템 전체 조회 가능 (단가/수량 포함)
   - viewer-summary : 프로젝트/입찰 합계만 조회 (라인 아이템 접근 불가)
4. 모든 테이블에 created_at / updated_at 감사 컬럼
5. bid_watchlist: 입찰별 비교 대상 자재 목록 (가격 이력 검색 범위 제한용)
   - 전체 카탈로그 검색이 아닌, 이번 입찰에서 비교할 품목만 지정하여 검색

Phase 1+3 (현재):
  - projects, bids, submissions, submission_items
  - users (세션 기반 인증, 4단계 role)
  - bid_watchlist (구조만, UI는 Phase 2에서)
  - 입찰 내 N개사 비교 쿼리

Phase 2 (이후):
  - catalog_categories, catalog_items
  - submission_items.catalog_item_id 연결 활성화
  - price_history 자동 생성
  - bid_watchlist 기반 가격 이력 검색 (범위 제한)
"""

import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ═══════════════════════════════════════════
-- 사용자 / 권한
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    dept          TEXT,
    role          TEXT NOT NULL DEFAULT 'viewer-summary',
                      -- admin | manager | viewer-detail | viewer-summary
    password_hash TEXT NOT NULL,
    -- LLM 설정: provider + 모델 + API 키 (사용자별 독립)
    llm_provider      TEXT NOT NULL DEFAULT 'claude',
                          -- 'claude' | 'gpt' | (향후 추가 가능)
    llm_model         TEXT,
                          -- NULL이면 provider 기본 모델 사용
    llm_api_key_enc   TEXT,
                          -- Fernet 암호화 저장, NULL이면 미설정
    llm_base_url      TEXT,
                          -- 커스텀 API 엔드포인트 (VDI/프록시 환경용, NULL이면 공식 엔드포인트)
    is_active     INTEGER NOT NULL DEFAULT 1,
    last_login    TIMESTAMP,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════
-- 도메인 관리 (추가/수정/비활성화 가능)
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS domains (
    domain_id   TEXT PRIMARY KEY,          -- UUID
    name        TEXT NOT NULL UNIQUE,      -- 'IT', '설비', '용역', '기타'
    description TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
                    -- 0이면 신규 입찰 생성 차단 (기존 데이터는 유지)
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════
-- 프로젝트 → 입찰 → 제출 계층
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS projects (
    project_id      TEXT PRIMARY KEY,      -- UUID
    name            TEXT NOT NULL,
    description     TEXT,
    owner_id        TEXT REFERENCES users(user_id),
    status          TEXT NOT NULL DEFAULT 'active',
                        -- active | closed | archived
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 입찰 회차 (한 프로젝트에 여러 번 입찰 가능)
CREATE TABLE IF NOT EXISTS bids (
    bid_id          TEXT PRIMARY KEY,      -- UUID
    project_id      TEXT NOT NULL REFERENCES projects(project_id),
    name            TEXT NOT NULL,         -- 예: "1차 입찰", "재입찰"
    due_date        DATE,
    description     TEXT,
    domain          TEXT NOT NULL DEFAULT 'IT',
                        -- 'IT' | '설비' | '용역' | '기타'
                        -- 도메인에 따라 기본 카테고리 세트 자동 적용
    status          TEXT NOT NULL DEFAULT 'open',
                        -- open | closed | awarded | cancelled
    created_by      TEXT REFERENCES users(user_id),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 업체 제출 (한 입찰에 여러 업체가 제출)
CREATE TABLE IF NOT EXISTS submissions (
    submission_id   TEXT PRIMARY KEY,      -- UUID
    bid_id          TEXT NOT NULL REFERENCES bids(bid_id),
    vendor_name     TEXT NOT NULL,
    vendor_contact  TEXT,                  -- 담당자 연락처 (선택)
    file_name       TEXT,                  -- 원본 파일명
    file_path       TEXT,                  -- 저장 경로 (data/uploads/)
    file_format     TEXT,                  -- xlsx | pdf | docx
    currency        TEXT NOT NULL DEFAULT 'KRW',
    currency_unit   TEXT NOT NULL DEFAULT '원',
    has_usd_items   INTEGER NOT NULL DEFAULT 0,
    fx_rate_used    REAL,
    subtotal_excl_vat   REAL,
    vat             REAL,
    grand_total     REAL,
    extraction_status TEXT NOT NULL DEFAULT 'pending',
                        -- pending | processing | done | failed
    extraction_error  TEXT,               -- 실패 시 오류 메시지
    review_status   TEXT NOT NULL DEFAULT 'unreviewed',
                        -- unreviewed | reviewed | approved
    reviewed_by     TEXT REFERENCES users(user_id),
    reviewed_at     TIMESTAMP,
    uploaded_by     TEXT REFERENCES users(user_id),
    compare_level   INTEGER NOT NULL DEFAULT 2,
                        -- 비교 단위 group by 레벨 (1=대,2=중,3=소...) [하위호환]
    compare_units   TEXT,
                        -- JSON: 비교 단위 경로 집합 (그룹별 다른 깊이)
                        -- 예: ["재료비 > 기구부 > 차폐", "이윤 및 관리비"]
    extracted_sheets TEXT,
                        -- JSON: 추출 시 읽은 시트 목록 (재추출 시 그것만 기본 선택)
    map_config      TEXT,
                        -- JSON: 열 매핑 설정 (헤더행/열역할/비교단위/제외/nego)
                        -- 재진입 시 마지막 작업 복원용
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (bid_id, vendor_name)           -- 같은 입찰에 같은 업체 중복 방지
);

-- 라인 아이템 (제출서의 개별 항목)
CREATE TABLE IF NOT EXISTS submission_items (
    item_id         TEXT PRIMARY KEY,      -- UUID
    submission_id   TEXT NOT NULL REFERENCES submissions(submission_id),
    line_no         TEXT,                  -- 원본 표기 (예: 1.1, 2.3.1)
    sort_order      INTEGER NOT NULL,
    depth           INTEGER NOT NULL DEFAULT 0,
    is_header       INTEGER NOT NULL DEFAULT 0,
    is_nego         INTEGER NOT NULL DEFAULT 0,
                        -- special nego(수기 조정) 항목 여부. 재추출 시 보존.
    category        TEXT,                  -- 자재 | 인건비 | 출장비 | 영업이익 | 관리비
    path            TEXT,                  -- 전체 경로 (자재 > 서버 > Rack Server)
    name_raw        TEXT,                  -- 원본 품명
    name_normalized TEXT,                  -- 정규화된 품명
    spec            TEXT,                  -- 규격/사양
    quantity        REAL,
    unit            TEXT,
    unit_price      REAL,                  -- 반드시 원 단위
    unit_price_orig REAL,                  -- 원본 단가 (USD 등)
    amount_orig     REAL,                   -- 원본 금액 (단가 없는 외화 행 대비, T2)
    unit_price_currency TEXT DEFAULT 'KRW',
    amount          REAL,                  -- 원 단위 금액
    -- Phase 2: 카탈로그 연결
    catalog_item_id TEXT,                  -- NULL = 미연결
                        -- REFERENCES catalog_items(catalog_item_id) (Phase 2)
    match_confidence REAL,                 -- LLM 매칭 신뢰도 (0~1)
    match_status    TEXT NOT NULL DEFAULT 'pending',
                        -- pending | suggested | confirmed | unmatched
    match_note      TEXT,                  -- 매칭 불일치 사유 등
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════
-- 항목-카탈로그 매칭 (비교 WS 소유)
-- ═══════════════════════════════════════════
-- 견적 원본(submission_items)과 비교 결과(매칭)를 물리적으로 분리.
--   · submission_items = 견적 원본(품명·단가·수량·금액). 병합 WS 소유, 확정 시 동결.
--   · item_match       = 비교 결과(어느 카탈로그 품목에 연결·매칭 상태). 비교 WS 소유, 자유 갱신.
-- 이 분리로: 확정 동결이 원본 테이블 통째로 단순하고, 되먹임(클러스터 확정)·잎 재매칭이
-- 원본을 건드리지 않는다. version 차원은 버전 관리(D3) 대비 — 초기엔 NULL(현재 매칭만).
CREATE TABLE IF NOT EXISTS item_match (
    item_id          TEXT NOT NULL REFERENCES submission_items(item_id),
    catalog_item_id  TEXT,                  -- 연결된 카탈로그 품목 (NULL = 미연결)
    match_status     TEXT NOT NULL DEFAULT 'pending',
                        -- pending | suggested | confirmed | unmatched
    match_confidence REAL,                  -- 매칭 신뢰도 (0~1)
    match_note       TEXT,                  -- 매칭 불일치 사유 등
    version          INTEGER NOT NULL DEFAULT 0,
                        -- 0 = 현재 매칭(기본). 버전 관리(D3) 도입 시 1,2,… 로 버전별 매칭.
                        -- NOT NULL DEFAULT 0 으로 PK 무결성 보장(SQLite는 NULL PK 중복 허용하므로).
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (item_id, version)          -- 버전별 매칭 허용 (0=현재)
);
CREATE INDEX IF NOT EXISTS idx_item_match_catalog ON item_match(catalog_item_id);
CREATE INDEX IF NOT EXISTS idx_item_match_status  ON item_match(match_status);

-- ═══════════════════════════════════════════
-- 스키마 메타 (1회성 보정·플래그 추적)
-- ═══════════════════════════════════════════
-- 매 기동 반복 실행되면 안 되는 1회성 데이터 보정의 적용 여부를 기록.
-- key = 보정 식별자, applied_at = 적용 시각.
CREATE TABLE IF NOT EXISTS schema_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    applied_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 이번 입찰에서 가격 이력을 비교할 품목을 명시적으로 지정.
-- 전체 카탈로그를 검색하는 게 아니라 담당자가 "이 입찰에서
-- 비교가 필요한 품목"을 먼저 등록해두면, 가격 이력 검색이
-- 이 목록 안에서만 동작함 → 불필요한 데이터 노출 방지.
CREATE TABLE IF NOT EXISTS bid_watchlist (
    watchlist_id    TEXT PRIMARY KEY,      -- UUID
    bid_id          TEXT NOT NULL REFERENCES bids(bid_id),
    catalog_item_id TEXT NOT NULL,         -- REFERENCES catalog_items (Phase 2)
    note            TEXT,                  -- 지정 사유 (예: "이번 입찰 주요 자재")
    added_by        TEXT REFERENCES users(user_id),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (bid_id, catalog_item_id)       -- 같은 입찰에 같은 품목 중복 방지
);

-- ═══════════════════════════════════════════
-- 품목 카탈로그 (Phase 2 - 지금은 구조만 생성)
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS catalog_categories (
    category_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,                  -- 카테고리 설명
    domain          TEXT NOT NULL DEFAULT 'IT',
                        -- 'IT' | '설비' | '용역' | '기타' | 'ALL' (전 도메인 공통)
    parent_id       TEXT REFERENCES catalog_categories(category_id),
    sort_order      INTEGER DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1,  -- 0이면 비활성화 (숨김)
    aliases         TEXT,                  -- 별칭(표기 편차) JSON 배열. 예: ["자재비","材料費"]
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS catalog_items (
    catalog_item_id TEXT PRIMARY KEY,
    category_id     TEXT REFERENCES catalog_categories(category_id),
    name_canonical  TEXT NOT NULL,         -- 표준 품목명
    aliases         TEXT,                  -- JSON 배열: 동의어 목록
    spec_template   TEXT,                  -- 주요 스펙 항목 (JSON)
    unit_std        TEXT,                  -- 표준 단위
    canonical_path  TEXT,                  -- 대표 분류 경로 "재료비 > 기구부 > 차폐"
    depth           INTEGER DEFAULT 0,     -- 계층 깊이
    observed_paths  TEXT,                  -- JSON: 입찰마다 관측된 여러 경로
                        -- (같은 품목이 A=재료비>센서, B=전장부>센서일 수 있음)
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_by      TEXT REFERENCES users(user_id),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 가격 이력 (submission_item이 catalog_item에 confirmed 연결될 때 자동 생성)
CREATE TABLE IF NOT EXISTS price_history (
    record_id       TEXT PRIMARY KEY,
    catalog_item_id TEXT NOT NULL,
                        -- REFERENCES catalog_items (Phase 2 활성화)
    submission_id   TEXT NOT NULL REFERENCES submissions(submission_id),
    item_id         TEXT NOT NULL REFERENCES submission_items(item_id),
    vendor_name     TEXT NOT NULL,
    bid_date        DATE,
    project_name    TEXT,
    quantity        REAL,
    unit            TEXT,
    unit_price      REAL,
    amount          REAL,
    item_path       TEXT,                  -- ★ 당시 분류 경로 스냅샷 (레벨별 리콜용)
    compare_unit    TEXT,                  -- 어느 비교 단위로 기록됐나
    spec_snapshot   TEXT,                  -- 당시 사양 스냅샷
    recorded_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════
-- 인덱스
-- ═══════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_bids_project    ON bids(project_id);
CREATE INDEX IF NOT EXISTS idx_submissions_bid ON submissions(bid_id);
CREATE INDEX IF NOT EXISTS idx_items_submission ON submission_items(submission_id);
CREATE INDEX IF NOT EXISTS idx_items_category  ON submission_items(category);
CREATE INDEX IF NOT EXISTS idx_items_catalog   ON submission_items(catalog_item_id);
CREATE INDEX IF NOT EXISTS idx_items_match     ON submission_items(match_status);
CREATE INDEX IF NOT EXISTS idx_price_catalog   ON price_history(catalog_item_id);
CREATE INDEX IF NOT EXISTS idx_price_vendor    ON price_history(vendor_name);
CREATE INDEX IF NOT EXISTS idx_watchlist_bid   ON bid_watchlist(bid_id);
CREATE INDEX IF NOT EXISTS idx_users_role      ON users(role);

-- ═══════════════════════════════════════════
-- 카탈로그 제안 (Phase 3-A)
-- 추출 완료 시 LLM이 신규/유사 품목을 자동 감지하여 제안
-- 담당자가 수락/거부/수정 후 확정
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS catalog_suggestions (
    suggestion_id   TEXT PRIMARY KEY,           -- UUID
    submission_id   TEXT NOT NULL REFERENCES submissions(submission_id),
    item_id         TEXT NOT NULL REFERENCES submission_items(item_id),

    -- 제안 유형
    suggestion_type TEXT NOT NULL,
                        -- 'new_item'   : 카탈로그에 없는 신규 품목 → 추가 제안
                        -- 'similar'    : 기존 카탈로그 품목과 유사 → 연결 제안

    -- 신규 품목 제안 시
    suggested_name  TEXT,                       -- LLM이 제안하는 표준 품목명
    suggested_category_id TEXT,                 -- 제안 카테고리
    suggested_aliases TEXT,                     -- 제안 별칭 (JSON 배열)
    suggested_spec  TEXT,                       -- 제안 규격 템플릿

    -- 유사 품목 연결 제안 시
    matched_catalog_item_id TEXT,               -- 연결 제안할 기존 카탈로그 품목
    similarity_score REAL,                      -- 유사도 (0~1)
    similarity_reason TEXT,                     -- 유사 판단 근거

    -- 처리 상태
    status          TEXT NOT NULL DEFAULT 'pending',
                        -- pending  : 담당자 검토 대기
                        -- accepted : 수락 (카탈로그 생성/연결됨)
                        -- rejected : 거부
                        -- modified : 수정 후 수락

    reviewed_by     TEXT REFERENCES users(user_id),
    reviewed_at     TIMESTAMP,
    review_note     TEXT,                       -- 거부/수정 사유

    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_suggestions_submission ON catalog_suggestions(submission_id);
CREATE INDEX IF NOT EXISTS idx_suggestions_status     ON catalog_suggestions(status);
CREATE INDEX IF NOT EXISTS idx_suggestions_item       ON catalog_suggestions(item_id);

-- ═══════════════════════════════════════════
-- 유사 품목 클러스터링 (Phase 3-B)
-- 여러 입찰서에서 등록된 유사 품목들을 하나로 묶는 제안
-- ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS catalog_clusters (
    cluster_id              TEXT PRIMARY KEY,
    bid_id                  TEXT,               -- 어느 입찰에서 생성된 클러스터
    representative_item_id  TEXT,               -- submission_items.item_id
    representative_name     TEXT,               -- LLM 제안 표준 품목명
    compare_unit_path       TEXT,               -- 이 클러스터의 비교 단위 경로
    compare_level           INTEGER,            -- 어느 레벨로 클러스터링했나
    status                  TEXT NOT NULL DEFAULT 'pending',
                                -- pending | accepted | rejected | held
    similarity_summary      TEXT,
    reviewed_by             TEXT REFERENCES users(user_id),
    reviewed_at             TIMESTAMP,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS catalog_cluster_members (
    cluster_id      TEXT NOT NULL REFERENCES catalog_clusters(cluster_id),
    catalog_item_id TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'duplicate',
                        -- 'representative' | 'duplicate'
    similarity_score REAL,
    PRIMARY KEY (cluster_id, catalog_item_id)
);

CREATE INDEX IF NOT EXISTS idx_cluster_status  ON catalog_clusters(status);
CREATE INDEX IF NOT EXISTS idx_cluster_members ON catalog_cluster_members(catalog_item_id);

-- ═══════════════════════════════════════════
-- 초기 데이터: 카탈로그 기본 카테고리
-- ═══════════════════════════════════════════

-- 기본 도메인 (INSERT OR IGNORE — 중복 무시)
-- '공통'은 미분류/도메인 미상 표지이자 폴백 기준점 (sort_order 0 = 최상단)
INSERT OR IGNORE INTO domains (domain_id, name, description, sort_order)
VALUES
    ('DOM-000', '공통', '도메인 미상·공통 (폴백 기준)',       0),
    ('DOM-001', 'IT',   'IT 시스템, 서버, 네트워크, SW 등',    1),
    ('DOM-002', '설비', '기계설비, 전기설비, 배관, 공조 등',  2),
    ('DOM-003', '용역', '컨설팅, 감리, 유지관리 서비스 등',  3),
    ('DOM-004', '기타', '복합 도메인 또는 미분류',            4);

-- 도메인별 기본 카테고리 (INSERT OR IGNORE — 중복 무시)
INSERT OR IGNORE INTO catalog_categories
    (category_id, name, domain, sort_order, is_active, description)
VALUES
    -- IT
    ('CAT-IT-001', '자재',     'IT', 1, 1, '서버, 네트워크 장비, SW 라이선스 등'),
    ('CAT-IT-002', '인건비',   'IT', 2, 1, 'PM, SE, 개발자 등 투입 인력'),
    ('CAT-IT-003', '출장비',   'IT', 3, 1, '현장 출장, 교통, 숙박'),
    ('CAT-IT-004', '영업이익', 'IT', 4, 1, '업체 마진'),
    ('CAT-IT-005', '관리비',   'IT', 5, 1, '간접 관리 비용'),
    -- 설비
    ('CAT-FA-001', '자재',     '설비', 1, 1, '기계, 전기, 배관 자재 등'),
    ('CAT-FA-002', '설치비',   '설비', 2, 1, '설비 설치 및 공사비'),
    ('CAT-FA-003', '시운전',   '설비', 3, 1, '시운전 및 테스트'),
    ('CAT-FA-004', '감리비',   '설비', 4, 1, '감리 및 감독'),
    ('CAT-FA-005', '유지보수', '설비', 5, 1, '유지보수 및 A/S'),
    ('CAT-FA-006', '철거',     '설비', 6, 1, '기존 설비 철거'),
    -- 용역
    ('CAT-SV-001', '직접인건비', '용역', 1, 1, '직접 투입 인력 인건비'),
    ('CAT-SV-002', '제경비',     '용역', 2, 1, '4대보험, 퇴직금 등'),
    ('CAT-SV-003', '기술료',     '용역', 3, 1, '기술 사용료 및 지식재산'),
    ('CAT-SV-004', '기타경비',   '용역', 4, 1, '여비, 인쇄, 소모품 등'),
    -- 기타 (도메인 미정 또는 복합)
    ('CAT-ETC-001', '자재',   '기타', 1, 1, NULL),
    ('CAT-ETC-002', '인건비', '기타', 2, 1, NULL),
    ('CAT-ETC-003', '기타경비', '기타', 3, 1, NULL);
"""


def init_db(db_path=None, reset=False):
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    if reset and path.exists():
        path.unlink()
        print(f"  [초기화] 기존 DB 삭제: {path}")

    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()

    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"  [완료] 테이블 {len(tables)}개: {[t[0] for t in tables]}")
    conn.close()
    return path


def migrate_db(db_path=None):
    """
    기존 DB를 새 스키마로 마이그레이션.
    anthropic_api_key_enc → llm_api_key_enc 컬럼명 변경 등.
    이미 최신이면 무시.
    """
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        return

    conn = sqlite3.connect(path)
    cols = [c[1] for c in conn.execute("PRAGMA table_info(users)").fetchall()]

    migrations = []

    # anthropic_api_key_enc → llm_api_key_enc 마이그레이션
    if "anthropic_api_key_enc" in cols and "llm_api_key_enc" not in cols:
        migrations.append("ALTER TABLE users ADD COLUMN llm_api_key_enc TEXT")
        migrations.append(
            "UPDATE users SET llm_api_key_enc = anthropic_api_key_enc "
            "WHERE anthropic_api_key_enc IS NOT NULL"
        )

    # llm_provider, llm_model 컬럼 추가
    if "llm_provider" not in cols:
        migrations.append("ALTER TABLE users ADD COLUMN llm_provider TEXT NOT NULL DEFAULT 'claude'")
    if "llm_model" not in cols:
        migrations.append("ALTER TABLE users ADD COLUMN llm_model TEXT")
    if "llm_base_url" not in cols:
        migrations.append("ALTER TABLE users ADD COLUMN llm_base_url TEXT")
    if "llm_verify_ssl" not in cols:
        migrations.append("ALTER TABLE users ADD COLUMN llm_verify_ssl INTEGER NOT NULL DEFAULT 1")

    # domains 테이블 추가 (도메인 관리)
    tables = [t[0] for t in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if "domains" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS domains (
                domain_id   TEXT PRIMARY KEY,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                is_active   INTEGER NOT NULL DEFAULT 1,
                sort_order  INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO domains (domain_id, name, description, sort_order)
            VALUES
                ('DOM-000', '공통', '도메인 미상·공통 (폴백 기준)',      0),
                ('DOM-001', 'IT',   'IT 시스템, 서버, 네트워크, SW 등',   1),
                ('DOM-002', '설비', '기계설비, 전기설비, 배관, 공조 등', 2),
                ('DOM-003', '용역', '컨설팅, 감리, 유지관리 서비스 등', 3),
                ('DOM-004', '기타', '복합 도메인 또는 미분류',           4)
        """)
        migrations.append("-- domains 테이블 생성 완료")

    # ── 프로젝트 기준정보 (속성 사전 + 프로젝트별 값) ──
    # 정의 테이블 = 기준정보 원장(governed registry). 새 속성 추가는 행 1개로,
    # 스키마 변경 없이 도메인 확장(IT/설비/용역)에 대응한다.
    if "project_attr_defs" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_attr_defs (
                attr_key    TEXT PRIMARY KEY,
                label       TEXT NOT NULL,
                value_type  TEXT NOT NULL DEFAULT 'text',   -- text | number
                unit        TEXT,
                domain      TEXT NOT NULL DEFAULT '공통',
                sort_order  INTEGER NOT NULL DEFAULT 0,
                is_active   INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO project_attr_defs
                (attr_key, label, value_type, unit, domain, sort_order)
            VALUES
                ('corp_name',    '법인명',    'text',   NULL, '공통', 1),
                ('factory',      '공장명',    'text',   NULL, '공통', 2),
                ('line_count',   '라인 수',   'number', '개', '공통', 3),
                ('product_size', '제품 크기', 'text',   NULL, '공통', 4),
                ('speed',        '속도',      'text',   NULL, '공통', 5),
                ('spec_etc',     '기타 규격', 'text',   NULL, '공통', 6)
        """)
        migrations.append("-- project_attr_defs 테이블 생성 완료")
    if "project_attrs" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_attrs (
                project_id  TEXT NOT NULL REFERENCES projects(project_id),
                attr_key    TEXT NOT NULL REFERENCES project_attr_defs(attr_key),
                value_text  TEXT,
                value_num   REAL,
                updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, attr_key)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pattr_key_val "
                     "ON project_attrs(attr_key, value_text)")
        migrations.append("-- project_attrs 테이블 생성 완료")

    # bids.domain 컬럼 추가 (도메인별 카테고리 분리)
    bid_cols = [c[1] for c in conn.execute("PRAGMA table_info(bids)").fetchall()]
    if "domain" not in bid_cols:
        migrations.append("ALTER TABLE bids ADD COLUMN domain TEXT NOT NULL DEFAULT 'IT'")
    if "category_order" not in bid_cols:
        # 입찰별 분류(카테고리) 표시 순서. JSON 배열. 비어있으면 분류관리 기본순서 사용.
        migrations.append("ALTER TABLE bids ADD COLUMN category_order TEXT")

    # projects.domain 컬럼 추가 (프로젝트 기본 도메인 → 입찰이 승계)
    proj_cols = [c[1] for c in conn.execute("PRAGMA table_info(projects)").fetchall()]
    if "domain" not in proj_cols:
        migrations.append("ALTER TABLE projects ADD COLUMN domain TEXT NOT NULL DEFAULT 'IT'")

    # catalog_clusters.display_order (사용자 지정 정렬 순서, 항목 6)
    clu_cols = [c[1] for c in conn.execute("PRAGMA table_info(catalog_clusters)").fetchall()]
    if "display_order" not in clu_cols:
        migrations.append("ALTER TABLE catalog_clusters ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0")

    # catalog_categories 컬럼 추가
    cat_cols = [c[1] for c in conn.execute("PRAGMA table_info(catalog_categories)").fetchall()]
    if "domain" not in cat_cols:
        migrations.append("ALTER TABLE catalog_categories ADD COLUMN domain TEXT NOT NULL DEFAULT 'IT'")
    if "is_active" not in cat_cols:
        migrations.append("ALTER TABLE catalog_categories ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    if "description" not in cat_cols:
        migrations.append("ALTER TABLE catalog_categories ADD COLUMN description TEXT")
    if "updated_at" not in cat_cols:
        migrations.append("ALTER TABLE catalog_categories ADD COLUMN updated_at TIMESTAMP")
    if "aliases" not in cat_cols:
        # 별칭(표기 편차) 목록. JSON 배열 문자열로 저장. 예: ["자재비","材料費"]
        # 추출된 대분류가 name과 다르더라도 이 목록에 있으면 표준 name으로 자동 귀속.
        migrations.append("ALTER TABLE catalog_categories ADD COLUMN aliases TEXT")
    sub_cols = [c[1] for c in conn.execute("PRAGMA table_info(submissions)").fetchall()]
    if "deleted_at" not in sub_cols:
        migrations.append("ALTER TABLE submissions ADD COLUMN deleted_at TIMESTAMP")
    if "compare_level" not in sub_cols:
        migrations.append("ALTER TABLE submissions ADD COLUMN compare_level INTEGER NOT NULL DEFAULT 2")
    if "compare_units" not in sub_cols:
        migrations.append("ALTER TABLE submissions ADD COLUMN compare_units TEXT")
    if "extracted_sheets" not in sub_cols:
        migrations.append("ALTER TABLE submissions ADD COLUMN extracted_sheets TEXT")
    if "map_config" not in sub_cols:
        migrations.append("ALTER TABLE submissions ADD COLUMN map_config TEXT")

    # submission_items 컬럼 추가
    si_cols = [c[1] for c in conn.execute("PRAGMA table_info(submission_items)").fetchall()]
    if "is_nego" not in si_cols:
        migrations.append("ALTER TABLE submission_items ADD COLUMN is_nego INTEGER NOT NULL DEFAULT 0")
    if "fx_rate_used" not in si_cols:
        # 항목별 적용 환율(원화÷통화). 통화 비교·표시용.
        migrations.append("ALTER TABLE submission_items ADD COLUMN fx_rate_used REAL")
    if "amount_orig" not in si_cols:
        # 원본 통화 금액 (단가 없는 외화 행의 원통화 보존, T2)
        migrations.append("ALTER TABLE submission_items ADD COLUMN amount_orig REAL")
    if "maker" not in si_cols:
        # 메이커(제조사/브랜드) — 추출 시 열 매핑으로 지정. 품목 비교 시 참고.
        migrations.append("ALTER TABLE submission_items ADD COLUMN maker TEXT")
    if "merge_status" not in si_cols:
        # [중복 병합] 요약↔상세 중복 정리 표시. NULL=정상 잎,
        #   'rolled_up'=상위 요약행(상세 잎 합으로 대체·총액 제외),
        #   'duplicate'=다른 시트의 완전중복 잎(총액 제외).
        #   플래그 행은 is_header=1로 저장돼 모든 합계 쿼리에서 제외되며,
        #   삭제하지 않고 보존(되돌리기 가능: is_header=0·merge_status=NULL).
        migrations.append("ALTER TABLE submission_items ADD COLUMN merge_status TEXT")

    # submissions.fx_rates: 통화별 환율 맵 JSON — {"USD":{"rate":1380,"base":"KRW","source":"extracted|manual"}}
    sub_cols = [c[1] for c in conn.execute("PRAGMA table_info(submissions)").fetchall()]
    if "fx_rates" not in sub_cols:
        migrations.append("ALTER TABLE submissions ADD COLUMN fx_rates TEXT")

    # bids.base_currency: 최종 비교 기준통화 (기본 KRW, 입찰별 선택 가능)
    bid_cols2 = [c[1] for c in conn.execute("PRAGMA table_info(bids)").fetchall()]
    if "base_currency" not in bid_cols2:
        migrations.append("ALTER TABLE bids ADD COLUMN base_currency TEXT NOT NULL DEFAULT 'KRW'")

    # item_match: 견적 원본(submission_items)과 비교 결과(매칭) 물리적 분리 (비교 WS 소유)
    if "item_match" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS item_match (
                item_id          TEXT NOT NULL REFERENCES submission_items(item_id),
                catalog_item_id  TEXT,
                match_status     TEXT NOT NULL DEFAULT 'pending',
                match_confidence REAL,
                match_note       TEXT,
                version          INTEGER NOT NULL DEFAULT 0,
                updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (item_id, version)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_item_match_catalog ON item_match(catalog_item_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_item_match_status  ON item_match(match_status)")
        conn.commit()
        migrations.append("-- item_match 테이블 생성 완료 (견적 원본/비교 결과 분리)")

    # submission_snapshots: [T7 연계] 확정 데이터 스냅샷 (버전 v1/v2…).
    #   연계 캔버스 4단계 '확정' 시 통합 트리를 동결 보존. 수정 후 재확정 → 다음 버전.
    if "submission_snapshots" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS submission_snapshots (
                snapshot_id  TEXT PRIMARY KEY,
                submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
                version      INTEGER NOT NULL,
                total        REAL,
                n_residual   INTEGER NOT NULL DEFAULT 0,
                tree_json    TEXT,
                note         TEXT,
                created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_sub "
                     "ON submission_snapshots(submission_id, version)")
        conn.commit()
        migrations.append("-- submission_snapshots 테이블 생성 완료 (연계 확정 버전 스냅샷)")

    # schema_meta: 1회성 보정 추적 (매 기동 반복 방지)
    if "schema_meta" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_meta (
                key         TEXT PRIMARY KEY,
                value       TEXT,
                applied_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        migrations.append("-- schema_meta 테이블 생성 완료 (1회성 보정 추적)")

    # 계층 전파: catalog_items, catalog_clusters, price_history
    ci_cols = [c[1] for c in conn.execute("PRAGMA table_info(catalog_items)").fetchall()]
    if "canonical_path" not in ci_cols:
        migrations.append("ALTER TABLE catalog_items ADD COLUMN canonical_path TEXT")
    if "depth" not in ci_cols:
        migrations.append("ALTER TABLE catalog_items ADD COLUMN depth INTEGER DEFAULT 0")
    if "observed_paths" not in ci_cols:
        migrations.append("ALTER TABLE catalog_items ADD COLUMN observed_paths TEXT")

    cl_cols = [c[1] for c in conn.execute("PRAGMA table_info(catalog_clusters)").fetchall()]
    if "compare_unit_path" not in cl_cols:
        migrations.append("ALTER TABLE catalog_clusters ADD COLUMN compare_unit_path TEXT")
    if "compare_level" not in cl_cols:
        migrations.append("ALTER TABLE catalog_clusters ADD COLUMN compare_level INTEGER")

    ph_cols = [c[1] for c in conn.execute("PRAGMA table_info(price_history)").fetchall()]
    if "item_path" not in ph_cols:
        migrations.append("ALTER TABLE price_history ADD COLUMN item_path TEXT")
    if "compare_unit" not in ph_cols:
        migrations.append("ALTER TABLE price_history ADD COLUMN compare_unit TEXT")

    # catalog_suggestions 테이블 추가 (Phase 3-A)
    tables = [t[0] for t in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if "catalog_suggestions" not in tables:
        migrations.append("""
CREATE TABLE IF NOT EXISTS catalog_suggestions (
    suggestion_id   TEXT PRIMARY KEY,
    submission_id   TEXT NOT NULL REFERENCES submissions(submission_id),
    item_id         TEXT NOT NULL REFERENCES submission_items(item_id),
    suggestion_type TEXT NOT NULL,
    suggested_name  TEXT,
    suggested_category_id TEXT,
    suggested_aliases TEXT,
    suggested_spec  TEXT,
    matched_catalog_item_id TEXT,
    similarity_score REAL,
    similarity_reason TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    reviewed_by     TEXT REFERENCES users(user_id),
    reviewed_at     TIMESTAMP,
    review_note     TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)""")
        migrations.append(
            "CREATE INDEX IF NOT EXISTS idx_suggestions_submission "
            "ON catalog_suggestions(submission_id)"
        )

    # catalog_clusters 테이블 추가 (Phase 3-B)
    if "catalog_clusters" not in tables:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS catalog_clusters (
                cluster_id             TEXT PRIMARY KEY,
                bid_id                 TEXT,
                representative_item_id TEXT,
                representative_name    TEXT,
                status                 TEXT NOT NULL DEFAULT 'pending',
                similarity_summary     TEXT,
                reviewed_by            TEXT REFERENCES users(user_id),
                reviewed_at            TIMESTAMP,
                created_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS catalog_cluster_members (
                cluster_id       TEXT NOT NULL,
                catalog_item_id  TEXT NOT NULL,
                role             TEXT NOT NULL DEFAULT 'duplicate',
                similarity_score REAL,
                PRIMARY KEY (cluster_id, catalog_item_id)
            )""")
        conn.commit()
        migrations.append("-- catalog_clusters 테이블 생성 완료")
    else:
        # 기존 테이블에 컬럼 추가
        cl_cols = [c[1] for c in conn.execute("PRAGMA table_info(catalog_clusters)").fetchall()]
        if "representative_name" not in cl_cols:
            migrations.append("ALTER TABLE catalog_clusters ADD COLUMN representative_name TEXT")
        if "bid_id" not in cl_cols:
            migrations.append("ALTER TABLE catalog_clusters ADD COLUMN bid_id TEXT")

    for sql in migrations:
        conn.execute(sql)
        print(f"  [마이그레이션] {sql[:60]}...")

    # ── 데이터 보정 (idempotent) ──
    # 1) '공통' 도메인 보강: 기존 DB에 없으면 추가 (미분류·폴백 기준점)
    try:
        conn.execute("""
            INSERT OR IGNORE INTO domains (domain_id, name, description, sort_order)
            VALUES ('DOM-000', '공통', '도메인 미상·공통 (폴백 기준)', 0)
        """)
        # 2) 입찰 도메인 전파 보정 (1회성): 과거 데이터의 도메인 불일치 교정.
        #    schema_meta로 적용 여부를 추적해 매 기동 반복을 방지한다.
        #    (이후 프로젝트 도메인 변경 전파는 update_project_domain이 담당)
        already = conn.execute(
            "SELECT 1 FROM schema_meta WHERE key = 'domain_propagation_fix_v1'"
        ).fetchone()
        if not already:
            fixed = conn.execute("""
                UPDATE bids
                   SET domain = (SELECT p.domain FROM projects p WHERE p.project_id = bids.project_id)
                 WHERE EXISTS (
                         SELECT 1 FROM projects p
                          WHERE p.project_id = bids.project_id
                            AND p.domain IS NOT NULL AND p.domain <> ''
                            AND p.domain <> bids.domain
                       )
            """).rowcount
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('domain_propagation_fix_v1', ?)",
                (str(fixed),))
            if fixed:
                print(f"  [보정·1회성] 입찰 도메인 전파: {fixed}건 (프로젝트 도메인으로 정정)")
    except Exception as _e:
        print(f"  [보정 경고] 도메인 보정 건너뜀: {_e}")

    conn.commit()
    conn.close()
    if migrations:
        print(f"  [완료] {len(migrations)}개 마이그레이션 적용")
    else:
        print("  [완료] 마이그레이션 불필요 (이미 최신)")


if __name__ == "__main__":
    if "--migrate" in sys.argv:
        migrate_db()
    else:
        init_db(reset="--reset" in sys.argv)
    print(f"  DB: {DB_PATH}")
