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
    user_id     TEXT PRIMARY KEY,          -- UUID
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    dept        TEXT,
    role        TEXT NOT NULL DEFAULT 'viewer-summary',
                    -- admin          : 전체 관리
                    -- manager        : 업로드/편집, 전체 데이터 접근
                    -- viewer-detail  : 라인 아이템(단가/수량) 조회 가능
                    -- viewer-summary : 프로젝트/입찰 합계만 조회
    password_hash TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    last_login  TIMESTAMP,
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
    category        TEXT,                  -- 자재 | 인건비 | 출장비 | 영업이익 | 관리비
    path            TEXT,                  -- 전체 경로 (자재 > 서버 > Rack Server)
    name_raw        TEXT,                  -- 원본 품명
    name_normalized TEXT,                  -- 정규화된 품명
    spec            TEXT,                  -- 규격/사양
    quantity        REAL,
    unit            TEXT,
    unit_price      REAL,                  -- 반드시 원 단위
    unit_price_orig REAL,                  -- 원본 단가 (USD 등)
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
-- 입찰별 비교 대상 자재 목록 (Phase 2 준비)
-- ═══════════════════════════════════════════

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
    parent_id       TEXT REFERENCES catalog_categories(category_id),
    sort_order      INTEGER DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS catalog_items (
    catalog_item_id TEXT PRIMARY KEY,
    category_id     TEXT REFERENCES catalog_categories(category_id),
    name_canonical  TEXT NOT NULL,         -- 표준 품목명
    aliases         TEXT,                  -- JSON 배열: 동의어 목록
    spec_template   TEXT,                  -- 주요 스펙 항목 (JSON)
    unit_std        TEXT,                  -- 표준 단위
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
-- 초기 데이터: 카탈로그 기본 카테고리
-- ═══════════════════════════════════════════

INSERT OR IGNORE INTO catalog_categories (category_id, name, sort_order)
VALUES
    ('CAT-001', '자재',   1),
    ('CAT-002', '인건비', 2),
    ('CAT-003', '출장비', 3),
    ('CAT-004', '영업이익', 4),
    ('CAT-005', '관리비', 5);
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


if __name__ == "__main__":
    init_db(reset="--reset" in sys.argv)
    print(f"  DB: {DB_PATH}")
