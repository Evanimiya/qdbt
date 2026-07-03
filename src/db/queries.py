"""
핵심 비즈니스 쿼리.

Phase 1+3 기준으로 작성.
- 프로젝트/입찰/제출 CRUD
- 입찰 내 N개사 비교 (같은 bid의 모든 submission 비교)
- 입찰 간 단순 비교 (같은 name_normalized 기준, Phase 2 전 임시)
"""
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 쓰기 경합 시 즉시 'database is locked'로 실패하지 않고 최대 5초 대기
    # (백그라운드 클러스터링 worker와 요청 스레드의 동시 쓰기 대비)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def new_id():
    return str(uuid.uuid4())


# ─── 프로젝트 ──────────────────────────────────

def create_project(name, description="", owner_id=None, domain="IT"):
    pid = new_id()
    with get_conn() as c:
        c.execute("""
            INSERT INTO projects (project_id, name, description, owner_id, domain)
            VALUES (?, ?, ?, ?, ?)
        """, (pid, name, description, owner_id, domain or "IT"))
    return pid


def update_project_domain(project_id: str, domain: str):
    """프로젝트 기본 도메인 변경."""
    with get_conn() as c:
        c.execute("UPDATE projects SET domain = ?, updated_at = ? WHERE project_id = ?",
                  (domain or "IT", datetime.now().isoformat(), project_id))


def reorder_clusters(bid_id: str, ordered_cluster_ids: list) -> int:
    """클러스터 표시 순서 저장 (항목 6, 드래그&드롭).

    ordered_cluster_ids 순서대로 display_order를 0,1,2…로 부여한다.
    목록에 없는 클러스터는 뒤로 밀리도록 큰 값 유지.
    """
    if not ordered_cluster_ids:
        return 0
    n = 0
    with get_conn() as c:
        for i, cid in enumerate(ordered_cluster_ids):
            c.execute("UPDATE catalog_clusters SET display_order = ? "
                      "WHERE cluster_id = ? AND bid_id = ?", (i, cid, bid_id))
            n += 1
    return n


def list_projects(status=None):
    sql = """
        SELECT p.*,
               u.name as owner_name,
               COUNT(DISTINCT b.bid_id) as n_bids,
               COUNT(DISTINCT s.submission_id) as n_submissions
        FROM projects p
        LEFT JOIN users u ON p.owner_id = u.user_id
        LEFT JOIN bids b ON p.project_id = b.project_id
        LEFT JOIN submissions s ON b.bid_id = s.bid_id
    """
    params = []
    if status:
        sql += " WHERE p.status = ?"
        params.append(status)
    sql += " GROUP BY p.project_id ORDER BY p.created_at DESC"
    with get_conn() as c:
        return c.execute(sql, params).fetchall()


def get_project(project_id):
    with get_conn() as c:
        return c.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()


def update_project(project_id, **kwargs):
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    with get_conn() as c:
        c.execute(f"UPDATE projects SET {sets} WHERE project_id = ?",
                  list(kwargs.values()) + [project_id])


# ─── 프로젝트 기준정보 (속성 사전 + 값) ─────────────

def list_attr_defs(active_only: bool = True, domain: str = None) -> list:
    """속성 사전(기준정보 원장) 목록.

    domain 지정 시: '공통' 항목 + 해당 도메인 전용 항목만 반환.
    domain=None: 전체 반환(관리 화면용).
    """
    with get_conn() as c:
        q = "SELECT * FROM project_attr_defs"
        conds, params = [], []
        if active_only:
            conds.append("is_active = 1")
        if domain:
            conds.append("(domain = ? OR domain = '공통' OR domain = 'ALL')")
            params.append(domain)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return c.execute(q + " ORDER BY sort_order, attr_key", params).fetchall()


def get_attr_def(attr_key: str):
    with get_conn() as c:
        return c.execute("SELECT * FROM project_attr_defs WHERE attr_key = ?",
                         (attr_key,)).fetchone()


def _slug_attr_key(label: str) -> str:
    """label에서 attr_key 자동 생성. 한글이면 해시 기반 안정 키."""
    import re as _re, hashlib
    s = _re.sub(r"[^a-zA-Z0-9]+", "_", (label or "").strip().lower()).strip("_")
    if s and _re.match(r"^[a-z]", s):
        return s[:40]
    # 한글 등 → attr_ + 라벨 해시(안정적, 충돌 회피)
    h = hashlib.md5((label or "").encode("utf-8")).hexdigest()[:8]
    return f"attr_{h}"


def create_attr_def(label: str, attr_key: str = None,
                    value_type: str = "text", unit: str = None,
                    domain: str = "공통", sort_order: int = None) -> str:
    """기준정보 항목(정의) 추가."""
    label = (label or "").strip()
    if not label:
        raise ValueError("항목명을 입력하세요.")
    key = (attr_key or "").strip() or _slug_attr_key(label)
    with get_conn() as c:
        # key 충돌 시 접미 번호
        base, i = key, 2
        while c.execute("SELECT 1 FROM project_attr_defs WHERE attr_key = ?",
                        (key,)).fetchone():
            key = f"{base}_{i}"; i += 1
        if sort_order is None:
            mx = c.execute("SELECT COALESCE(MAX(sort_order),0) FROM project_attr_defs").fetchone()[0]
            sort_order = (mx or 0) + 1
        c.execute("""
            INSERT INTO project_attr_defs
                (attr_key, label, value_type, unit, domain, sort_order, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (key, label, value_type or "text", unit, domain or "공통", sort_order))
    return key


def update_attr_def(attr_key: str, **kwargs):
    allowed = {"label", "value_type", "unit", "domain", "sort_order", "is_active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as c:
        c.execute(f"UPDATE project_attr_defs SET {sets} WHERE attr_key = ?",
                  list(fields.values()) + [attr_key])


def toggle_attr_def(attr_key: str, is_active: bool):
    update_attr_def(attr_key, is_active=1 if is_active else 0)


def get_project_attrs(project_id: str) -> dict:
    """프로젝트의 기준정보 값 {attr_key: 표시값}."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT attr_key, value_text FROM project_attrs WHERE project_id = ?",
            (project_id,)).fetchall()
    return {r["attr_key"]: r["value_text"] for r in rows}


def set_project_attrs(project_id: str, values: dict):
    """기준정보 값 일괄 upsert. 빈 값은 해당 속성 삭제(미지정으로 복귀).

    number 타입은 value_num에도 파싱 저장(수치 조건 검색 대비),
    표시·드롭다운은 value_text 원문을 쓴다.
    """
    defs = {d["attr_key"]: dict(d) for d in list_attr_defs(active_only=False)}
    now = datetime.now().isoformat()
    with get_conn() as c:
        for key, raw in values.items():
            if key not in defs:
                continue  # 사전에 없는 키는 무시 (기준정보 통제)
            val = (raw or "").strip()
            if not val:
                c.execute("DELETE FROM project_attrs "
                          "WHERE project_id = ? AND attr_key = ?",
                          (project_id, key))
                continue
            vnum = None
            if defs[key].get("value_type") == "number":
                try:
                    vnum = float(val.replace(",", ""))
                except ValueError:
                    vnum = None
            c.execute("""
                INSERT INTO project_attrs
                    (project_id, attr_key, value_text, value_num, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, attr_key) DO UPDATE SET
                    value_text = excluded.value_text,
                    value_num  = excluded.value_num,
                    updated_at = excluded.updated_at
            """, (project_id, key, val, vnum, now))


def attr_value_options(attr_key: str, limit: int = 50) -> list:
    """속성별 기존 저장값 목록(중복 제거) — 드롭다운+직접입력(datalist)용.

    새 값을 직접 입력해 저장하면 다음부터 자동으로 목록에 나타난다."""
    with get_conn() as c:
        rows = c.execute("""
            SELECT DISTINCT value_text FROM project_attrs
            WHERE attr_key = ? AND value_text IS NOT NULL AND value_text != ''
            ORDER BY value_text LIMIT ?
        """, (attr_key, limit)).fetchall()
    return [r["value_text"] for r in rows]


def search_projects_by_attrs(filters: list) -> list:
    """기준정보 조건으로 프로젝트 검색 → project_id 목록.

    filters: [(attr_key, value_str), ...] — 조건 간 AND.
    text: 부분일치(LIKE), number: 수치 일치(=). 값이 빈 조건은 무시.
    """
    ids = None
    defs = {d["attr_key"]: dict(d) for d in list_attr_defs(active_only=False)}
    with get_conn() as c:
        for key, raw in filters:
            val = (raw or "").strip()
            if not val or key not in defs:
                continue
            if defs[key].get("value_type") == "number":
                try:
                    vnum = float(val.replace(",", ""))
                except ValueError:
                    continue
                rows = c.execute(
                    "SELECT project_id FROM project_attrs "
                    "WHERE attr_key = ? AND value_num = ?",
                    (key, vnum)).fetchall()
            else:
                rows = c.execute(
                    "SELECT project_id FROM project_attrs "
                    "WHERE attr_key = ? AND value_text LIKE ?",
                    (key, f"%{val}%")).fetchall()
            found = {r["project_id"] for r in rows}
            ids = found if ids is None else (ids & found)
    return sorted(ids) if ids is not None else None  # None = 조건 없음(전체)


# ─── 입찰 ──────────────────────────────────────

def update_bid(bid_id, **kwargs):
    """입찰 필드 갱신 (범용). 예: update_bid(bid_id, name='재입찰')."""
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    with get_conn() as c:
        c.execute(f"UPDATE bids SET {sets} WHERE bid_id = ?",
                  list(kwargs.values()) + [bid_id])


def _hard_delete_bid_in_conn(c, bid_id: str) -> dict:
    """입찰 완전 삭제 — 자식→부모 순 cascade (호출측 트랜잭션 안에서 실행).

    업로드 물리 파일은 감사·재현 근거로 보존한다(수동 관리 정책).
    반환: 삭제 건수 요약.
    """
    sids = [r["submission_id"] for r in c.execute(
        "SELECT submission_id FROM submissions WHERE bid_id = ?", (bid_id,))]
    n = {"submissions": len(sids), "items": 0, "clusters": 0}
    if sids:
        ph = ",".join("?" * len(sids))
        n["items"] = c.execute(
            f"SELECT COUNT(*) FROM submission_items WHERE submission_id IN ({ph})",
            sids).fetchone()[0]
        c.execute(f"""DELETE FROM price_history WHERE item_id IN (
            SELECT item_id FROM submission_items WHERE submission_id IN ({ph}))""", sids)
        c.execute(f"""DELETE FROM catalog_suggestions WHERE item_id IN (
            SELECT item_id FROM submission_items WHERE submission_id IN ({ph}))""", sids)
    n["clusters"] = c.execute(
        "SELECT COUNT(*) FROM catalog_clusters WHERE bid_id = ?", (bid_id,)).fetchone()[0]
    c.execute("""DELETE FROM catalog_cluster_members WHERE cluster_id IN (
        SELECT cluster_id FROM catalog_clusters WHERE bid_id = ?)""", (bid_id,))
    c.execute("DELETE FROM catalog_clusters WHERE bid_id = ?", (bid_id,))
    if sids:
        c.execute(f"DELETE FROM submission_items WHERE submission_id IN ({ph})", sids)
    c.execute("DELETE FROM submissions WHERE bid_id = ?", (bid_id,))
    c.execute("DELETE FROM bid_watchlist WHERE bid_id = ?", (bid_id,))
    c.execute("DELETE FROM bids WHERE bid_id = ?", (bid_id,))
    return n


def hard_delete_bid(bid_id: str) -> dict:
    """입찰 완전 삭제 (admin 전용 경로에서만 호출). 단일 트랜잭션."""
    with get_conn() as c:
        return _hard_delete_bid_in_conn(c, bid_id)


def hard_delete_project(project_id: str) -> dict:
    """프로젝트 완전 삭제 — 하위 입찰 전부 cascade 후 기준정보·프로젝트 삭제.

    단일 트랜잭션: 중간 실패 시 전체 롤백(get_conn이 예외 시 rollback)."""
    with get_conn() as c:
        bid_ids = [r["bid_id"] for r in c.execute(
            "SELECT bid_id FROM bids WHERE project_id = ?", (project_id,))]
        total = {"bids": len(bid_ids), "submissions": 0, "items": 0, "clusters": 0}
        for bid in bid_ids:
            n = _hard_delete_bid_in_conn(c, bid)
            for k in ("submissions", "items", "clusters"):
                total[k] += n[k]
        c.execute("DELETE FROM project_attrs WHERE project_id = ?", (project_id,))
        c.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        return total


def vendor_name_exists(bid_id, vendor_name, exclude_submission_id=None):
    """같은 입찰 내 동일 업체명이 이미 있는지 검사.

    submissions에 UNIQUE(bid_id, vendor_name) 제약이 있어, 업체명 변경 전
    사전 검사로 사용자에게 명확한 안내를 주기 위한 헬퍼.
    exclude_submission_id: 자기 자신은 검사에서 제외(동일 이름 재저장 허용).
    """
    with get_conn() as c:
        q = ("SELECT 1 FROM submissions WHERE bid_id = ? AND vendor_name = ?"
             " AND deleted_at IS NULL")
        params = [bid_id, vendor_name]
        if exclude_submission_id:
            q += " AND submission_id != ?"
            params.append(exclude_submission_id)
        return c.execute(q + " LIMIT 1", params).fetchone() is not None

def create_bid(project_id, name, due_date=None, description="",
               created_by=None, domain="IT"):
    bid_id = new_id()
    with get_conn() as c:
        c.execute("""
            INSERT INTO bids
                (bid_id, project_id, name, due_date, description, domain, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (bid_id, project_id, name, due_date, description, domain, created_by))
    return bid_id


def list_bids(project_id):
    with get_conn() as c:
        return c.execute("""
            SELECT b.*,
                   COUNT(DISTINCT s.submission_id) as n_submissions,
                   MIN(s.subtotal_excl_vat) as min_price,
                   MAX(s.subtotal_excl_vat) as max_price
            FROM bids b
            LEFT JOIN submissions s ON b.bid_id = s.bid_id
                AND s.extraction_status = 'done'
            WHERE b.project_id = ?
            GROUP BY b.bid_id
            ORDER BY b.created_at DESC
        """, (project_id,)).fetchall()


def get_bid(bid_id):
    with get_conn() as c:
        return c.execute("""
            SELECT b.*, p.name as project_name, p.project_id
            FROM bids b JOIN projects p USING (project_id)
            WHERE b.bid_id = ?
        """, (bid_id,)).fetchone()


# ─── 제출 (Submission) ─────────────────────────

def create_submission(bid_id, vendor_name, file_name, file_path,
                      file_format, uploaded_by=None):
    sid = new_id()
    with get_conn() as c:
        c.execute("""
            INSERT INTO submissions
                (submission_id, bid_id, vendor_name, file_name, file_path,
                 file_format, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (sid, bid_id, vendor_name, file_name, file_path,
              file_format, uploaded_by))
    return sid


def update_submission(submission_id, **kwargs):
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    with get_conn() as c:
        c.execute(f"UPDATE submissions SET {sets} WHERE submission_id = ?",
                  list(kwargs.values()) + [submission_id])


def reset_project_submissions(project_id: str) -> int:
    """데모용: 프로젝트의 모든 제출서를 pending 상태로 초기화 (추출 데이터 삭제)"""
    with get_conn() as c:
        sub_ids = [r[0] for r in c.execute(
            "SELECT s.submission_id FROM submissions s JOIN bids b USING (bid_id) WHERE b.project_id = ?",
            (project_id,)
        ).fetchall()]
        if not sub_ids:
            return 0
        placeholders = ",".join("?" * len(sub_ids))
        c.execute(f"DELETE FROM submission_items WHERE submission_id IN ({placeholders})", sub_ids)
        c.execute(f"""UPDATE submissions SET
            extraction_status='pending',
            subtotal_excl_vat=NULL, vat=NULL, grand_total=NULL,
            updated_at=?
            WHERE submission_id IN ({placeholders})""",
            [datetime.now().isoformat()] + sub_ids)
    return len(sub_ids)


def get_submission(submission_id):
    with get_conn() as c:
        return c.execute("""
            SELECT s.*, b.name as bid_name, p.name as project_name,
                   b.project_id, b.domain as bid_domain
            FROM submissions s
            JOIN bids b USING (bid_id)
            JOIN projects p USING (project_id)
            WHERE s.submission_id = ?
        """, (submission_id,)).fetchone()


def list_submissions(bid_id):
    """활성(삭제되지 않은) 제출서 목록"""
    with get_conn() as c:
        return c.execute("""
            SELECT s.*,
                   COUNT(CASE WHEN i.is_header = 0 THEN 1 END) as n_items,
                   u.name as uploaded_by_name
            FROM submissions s
            LEFT JOIN submission_items i USING (submission_id)
            LEFT JOIN users u ON s.uploaded_by = u.user_id
            WHERE s.bid_id = ? AND s.deleted_at IS NULL
            GROUP BY s.submission_id
            ORDER BY s.subtotal_excl_vat NULLS LAST
        """, (bid_id,)).fetchall()


def list_deleted_submissions(bid_id):
    """소프트 삭제된 제출서 목록"""
    with get_conn() as c:
        return c.execute("""
            SELECT s.*,
                   COUNT(CASE WHEN i.is_header = 0 THEN 1 END) as n_items,
                   u.name as uploaded_by_name
            FROM submissions s
            LEFT JOIN submission_items i USING (submission_id)
            LEFT JOIN users u ON s.uploaded_by = u.user_id
            WHERE s.bid_id = ? AND s.deleted_at IS NOT NULL
            GROUP BY s.submission_id
            ORDER BY s.deleted_at DESC
        """, (bid_id,)).fetchall()


def reset_submission(submission_id: str):
    """추출 데이터만 초기화 — 제출서 레코드(파일 포함)는 유지"""
    with get_conn() as c:
        # 자식 테이블 먼저 삭제 (FK 제약 위반 방지)
        c.execute("""
            DELETE FROM price_history
            WHERE item_id IN (
                SELECT item_id FROM submission_items WHERE submission_id = ?
            )
        """, (submission_id,))
        c.execute("""
            DELETE FROM catalog_suggestions
            WHERE item_id IN (
                SELECT item_id FROM submission_items WHERE submission_id = ?
            )
        """, (submission_id,))
        c.execute("DELETE FROM submission_items WHERE submission_id = ?", (submission_id,))
        c.execute("""
            UPDATE submissions SET
                extraction_status = 'pending',
                subtotal_excl_vat = NULL,
                vat = NULL,
                grand_total = NULL,
                extraction_error = NULL,
                updated_at = ?
            WHERE submission_id = ?
        """, (datetime.now().isoformat(), submission_id))


def soft_delete_submission(submission_id: str):
    """소프트 삭제 — deleted_at 타임스탬프 설정"""
    with get_conn() as c:
        c.execute("""
            UPDATE submissions SET deleted_at = ?, updated_at = ?
            WHERE submission_id = ?
        """, (datetime.now().isoformat(), datetime.now().isoformat(), submission_id))


def restore_submission(submission_id: str):
    """소프트 삭제 취소 — deleted_at 초기화"""
    with get_conn() as c:
        c.execute("""
            UPDATE submissions SET deleted_at = NULL, updated_at = ?
            WHERE submission_id = ?
        """, (datetime.now().isoformat(), submission_id))


# ─── 라인 아이템 ────────────────────────────────

def recompute_subtotal(submission_id: str):
    """제출서의 공급가액(subtotal_excl_vat)을 잎 합계로 재계산·저장.

    트리/라인 합계와 동일하게 헤더(소계) 제외한 잎의 amount 합.
    special nego는 음수로 저장돼 있어 자동 차감됨.
    아이템 삭제·추출 후 호출해 합계 일치를 유지한다.
    또한 항목별 통화·환율에서 제출서 대표 환율(fx_rate_used)과
    외화 포함 여부(has_usd_items)를 집계해 비교 화면 표시에 사용한다.
    """
    leaf_items = get_items(submission_id, headers=False)
    subtotal = sum((dict(it).get("amount") or 0) for it in leaf_items)

    # 제출서 대표 환율·외화 플래그 집계
    fx_vals, has_fx = [], 0
    for it in leaf_items:
        d = dict(it)
        cur = (d.get("unit_price_currency") or "KRW").upper()
        fx = d.get("fx_rate_used")
        if cur and cur != "KRW":
            has_fx = 1
        if fx:
            fx_vals.append(fx)
    # 대표 환율: 최빈값(같은 견적서는 보통 단일 환율), 없으면 None
    rep_fx = None
    if fx_vals:
        from collections import Counter
        rep_fx = Counter(round(f, 4) for f in fx_vals).most_common(1)[0][0]

    update_submission(submission_id, subtotal_excl_vat=subtotal,
                      has_usd_items=has_fx, fx_rate_used=rep_fx)
    return subtotal


def add_nego_item(submission_id, label, amount, category="조정"):
    """special nego 항목 추가 (사람 수기 입력).

    submission_items에 is_nego=1 항목으로 저장.
    amount는 음수로 강제(차감). path/name은 label로.
    반환: 생성된 item_id.
    """
    iid = new_id()
    amt = -abs(_to_number(amount) or 0)
    with get_conn() as c:
        # sort_order는 큰 값으로 (맨 뒤). depth=1, category 지정.
        c.execute("""
            INSERT INTO submission_items
                (item_id, submission_id, line_no, sort_order, depth, is_header,
                 category, path, name_raw, name_normalized, spec,
                 quantity, unit, unit_price, amount, is_nego)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        """, (
            iid, submission_id, "NEGO", 99999, 1, 0,
            category, label, label, label, None,
            None, None, amt, amt,
        ))
    return iid


def update_nego_item(item_id, label=None, amount=None):
    """special nego 항목 수정 (label/금액)."""
    sets, vals = [], []
    if label is not None:
        sets += ["name_raw = ?", "name_normalized = ?", "path = ?"]
        vals += [label, label, label]
    if amount is not None:
        amt = -abs(_to_number(amount) or 0)
        sets += ["amount = ?", "unit_price = ?"]
        vals += [amt, amt]
    if not sets:
        return
    vals.append(item_id)
    with get_conn() as c:
        c.execute(f"UPDATE submission_items SET {', '.join(sets)} WHERE item_id = ? AND is_nego = 1", vals)


def list_nego_items(submission_id):
    """제출서의 nego 항목 목록."""
    with get_conn() as c:
        rows = c.execute("""
            SELECT item_id, name_normalized, amount, category
            FROM submission_items
            WHERE submission_id = ? AND is_nego = 1
            ORDER BY sort_order
        """, (submission_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_single_item(item_id: str):
    """단일 라인 아이템 삭제 (잘못 추출된 항목 제거용).

    자식 테이블(price_history, catalog_suggestions, cluster_members)을
    먼저 정리한 뒤 항목을 삭제한다.
    반환: 삭제된 행 수 (0이면 없던 항목).
    """
    with get_conn() as c:
        c.execute("DELETE FROM price_history WHERE item_id = ?", (item_id,))
        c.execute("DELETE FROM catalog_suggestions WHERE item_id = ?", (item_id,))
        try:
            c.execute("DELETE FROM catalog_cluster_members WHERE catalog_item_id = ?", (item_id,))
        except Exception:
            pass
        cur = c.execute("DELETE FROM submission_items WHERE item_id = ?", (item_id,))
        return cur.rowcount


def delete_submission_items(submission_id: str, keep_nego: bool = False):
    """제출서의 라인 아이템 삭제 (재추출 전 호출).

    keep_nego=True면 is_nego 항목(사람이 수기 입력한 special nego)은 보존.
    재추출해도 수기 nego가 날아가지 않도록 한다.
    """
    nego_filter = " AND (is_nego = 0 OR is_nego IS NULL)" if keep_nego else ""
    with get_conn() as c:
        # 자식 테이블 먼저 삭제 (FK 제약 위반 방지)
        c.execute(f"""
            DELETE FROM price_history
            WHERE item_id IN (
                SELECT item_id FROM submission_items
                WHERE submission_id = ?{nego_filter}
            )
        """, (submission_id,))
        c.execute(f"""
            DELETE FROM catalog_suggestions
            WHERE item_id IN (
                SELECT item_id FROM submission_items
                WHERE submission_id = ?{nego_filter}
            )
        """, (submission_id,))
        c.execute(
            f"DELETE FROM submission_items WHERE submission_id = ?{nego_filter}",
            (submission_id,))


def _strip_indent_prefix(s: str) -> str:
    """'[indent=N]' 접두사 제거 (LLM이 가끔 삽입하는 내부 태그)"""
    import re
    if s and s.startswith("[indent="):
        return re.sub(r"^\[indent=\d+\]\s*", "", s)
    return s


def _to_number(v):
    """수량/단가/금액을 안전하게 숫자로 변환.

    LLM이 "1,000", "1,000원", "3.5", "" 등 다양한 형태로 줄 수 있어,
    문자열이면 숫자만 추출해 float로. 변환 불가면 None.
    (DB의 REAL 컬럼에 문자열이 들어가 INSERT가 멈추는 것 방지)
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        import re
        s = v.strip()
        if not s:
            return None
        # 숫자/마이너스/소수점만 남기기 (천단위 콤마, '원' 등 제거)
        cleaned = re.sub(r"[^\d.\-]", "", s)
        if cleaned in ("", "-", ".", "-."):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def insert_items_bulk(submission_id, items: list[dict]):
    """추출된 아이템 일괄 삽입.

    각 항목 삽입을 개별 try로 감싸, 어느 항목/어느 값에서 실패하는지
    콘솔에 출력한다 (대용량/신규 양식에서 특정 값이 DB 타입과 안 맞을 때 진단).
    """
    with get_conn() as c:
        for i, it in enumerate(items):
            iid = new_id()
            try:
                c.execute("""
                    INSERT INTO submission_items
                        (item_id, submission_id, line_no, sort_order, depth, is_header,
                         category, path, name_raw, name_normalized, spec,
                         quantity, unit, unit_price, unit_price_orig,
                         unit_price_currency, fx_rate_used, amount, is_nego)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    iid, submission_id,
                    it.get("line_no"), i, it.get("depth", 0),
                    1 if it.get("is_category_header") else 0,
                    it.get("category"),
                    it.get("path") or it.get("parent_path", ""),
                    _strip_indent_prefix(it.get("name_raw") or ""),
                    it.get("name_normalized"),
                    it.get("spec"),
                    _to_number(it.get("quantity")),
                    it.get("unit"),
                    _to_number(it.get("unit_price")),
                    _to_number(it.get("unit_price_orig")),
                    it.get("unit_price_currency_in_source", "KRW"),
                    _to_number(it.get("fx_rate_used")),
                    _to_number(it.get("amount")),
                    1 if it.get("is_nego") else 0,
                ))
            except Exception as e:
                # 어느 항목에서 터졌는지 명확히 출력
                import sys
                print(f"[insert_items_bulk] 항목 #{i} 삽입 실패: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
                print(f"  name_raw={it.get('name_raw')!r}, "
                      f"quantity={it.get('quantity')!r}, "
                      f"unit_price={it.get('unit_price')!r}, "
                      f"amount={it.get('amount')!r}", file=sys.stderr)
                raise


def _split_path(path, sep_candidates=None):
    """path 문자열을 레벨 리스트로 분리.

    구분자는 ' > '(꺾쇠)만 사용한다. 코드 기반 추출이 path를 ' > '로
    통일하므로, 분류명 안에 들어있는 '/'(예: "전장/제어부")는
    데이터로 보존해야 트리가 깨지지 않는다.
    (구형 데이터의 '|', 역슬래시도 함께 구분자로 인정하되 '/'는 제외.)
    """
    if not path:
        return []
    import re as _re
    # '>' 와 (구형 호환) '|', 역슬래시만 구분자. '/'는 데이터로 보존.
    parts = _re.split(r"\s*[>|\\]\s*", path)
    return [p.strip() for p in parts if p.strip()]


def build_items_tree(submission_id):
    """제출서 항목을 path 기반 계층 트리로 구성.

    각 잎(품목)의 path를 따라 트리를 만들고, 가지마다 금액 합계.
    트리 UI(+/− 펼침, 그룹별 비교 단위)에서 사용.

    반환: {"tree": [노드...], "total": 합계, "max_depth": 최대깊이}
    각 노드: {name, path, amount, n_items, depth, is_leaf, leaf_data, children}
    """
    items = get_items(submission_id, headers=False)
    root = {}
    max_depth = 1
    order_counter = [0]

    for it in items:
        d = dict(it)
        path = d.get("path") or ""
        parts = _split_path(path)
        nm = d.get("name_normalized") or d.get("name_raw") or "(미분류)"
        if not parts:
            cat = d.get("category") or ""
            parts = [cat] if cat else []
        # path는 분류(대/중/소)까지만 담고 품목명은 별도 → 품목명을 잎으로 추가.
        # 단, path 끝이 이미 품목명과 같으면(중복) 추가하지 않음.
        if not parts or parts[-1] != nm:
            parts = parts + [nm]
        max_depth = max(max_depth, len(parts))
        amt = d.get("amount") or 0

        cur = root
        acc = []
        for i, part in enumerate(parts):
            acc.append(part)
            if part not in cur:
                cur[part] = {
                    "name": part, "path": " > ".join(acc),
                    "amount": 0.0, "n_items": 0, "depth": i + 1,
                    "_children": {}, "leaf_data": None,
                    "_order": order_counter[0],
                }
                order_counter[0] += 1
            cur[part]["amount"] += amt
            cur[part]["n_items"] += 1
            if i == len(parts) - 1:
                cur[part]["leaf_data"] = {
                    "item_id": d.get("item_id"),
                    "line_no": d.get("line_no"),
                    "name": d.get("name_normalized") or d.get("name_raw") or "",
                    "spec": d.get("spec"),
                    "qty": d.get("quantity"), "unit": d.get("unit"),
                    "unit_price": d.get("unit_price"), "amount": amt,
                }
            cur = cur[part]["_children"]

    def to_list(nd):
        out = []
        for node in sorted(nd.values(), key=lambda n: n["_order"]):
            children = to_list(node["_children"])
            out.append({
                "name": node["name"], "path": node["path"],
                "amount": node["amount"], "n_items": node["n_items"],
                "depth": node["depth"], "is_leaf": len(children) == 0,
                "leaf_data": node["leaf_data"], "children": children,
            })
        return out

    tree = to_list(root)
    total = sum(n["amount"] for n in tree)
    return {"tree": tree, "total": total, "max_depth": max_depth}


def group_items_by_level(submission_id, level=2):
    """제출서 항목을 지정한 분류 레벨로 group by 하여 금액 합계 반환.

    level: 묶을 깊이 (1=대분류, 2=중분류, 3=소분류...).
           path를 level 깊이까지 잘라 같은 경로끼리 묶는다.

    파워피벗 group by처럼: 데이터(잎)는 보존, 표시만 묶음.
    path가 없는 항목은 category 또는 name으로 폴백.

    반환: {
      "level": level,
      "groups": [
        {"key": "재료비 > 기구부", "label": "기구부",
         "amount": 합계, "n_items": 항목수, "max_depth": 최대깊이,
         "members": [item dict, ...]},  # 트리 펼침용
        ...
      ],
      "total": 전체합계,
      "max_available_level": 데이터에 존재하는 최대 깊이,
    }
    """
    items = get_items(submission_id, headers=False)  # 소계/헤더 제외
    groups = {}  # key -> aggregate
    max_avail = 1

    for it in items:
        d = dict(it)
        path = d.get("path") or ""
        parts = _split_path(path)
        if parts:
            max_avail = max(max_avail, len(parts))

        # 묶음 키: path를 level 깊이까지 (부족하면 있는 데까지)
        if parts:
            key_parts = parts[:level]
            key = " > ".join(key_parts)
            label = key_parts[-1] if key_parts else (d.get("name_normalized") or d.get("name_raw") or "")
        else:
            # path 없으면 category > name 폴백
            cat = d.get("category") or ""
            nm = d.get("name_normalized") or d.get("name_raw") or "(미분류)"
            key = f"{cat} > {nm}" if cat else nm
            label = nm

        g = groups.setdefault(key, {
            "key": key, "label": label, "amount": 0.0,
            "n_items": 0, "max_depth": 0, "members": [],
        })
        amt = d.get("amount") or 0
        g["amount"] += amt
        g["n_items"] += 1
        g["max_depth"] = max(g["max_depth"], len(parts))
        g["members"].append(d)

    group_list = list(groups.values())
    # 원본 순서 유지: 첫 멤버의 sort_order 기준
    group_list.sort(key=lambda g: g["members"][0].get("sort_order", 0) if g["members"] else 0)
    total = sum(g["amount"] for g in group_list)

    return {
        "level": level,
        "groups": group_list,
        "total": total,
        "max_available_level": max_avail,
    }


def get_items(submission_id, headers=False):
    with get_conn() as c:
        sql = """
            SELECT * FROM submission_items
            WHERE submission_id = ?
        """
        if not headers:
            sql += " AND is_header = 0"
        sql += " ORDER BY sort_order"
        return c.execute(sql, (submission_id,)).fetchall()


# ─── 입찰 내 비교 (Phase 1+3 핵심) ────────────

def compare_bid_by_units(bid_id):
    """입찰 내 업체 비교 — 각 업체의 저장된 비교 단위(compare_units)로 묶어 비교.

    각 업체가 추출 화면에서 정한 비교 단위(그룹별 깊이)로 묶고,
    묶인 명칭으로 업체 간 매칭. 레벨이 달라도 명칭으로 만남.
    (설계서 4단계: "A의 소분류 = B의 중분류" — 사람이 정한 단위로 비교)

    반환: {
      'vendors': [업체명...],
      'rows': [  # 비교 단위별 행
        {'label': '기구부', 'unit_paths': {업체: 경로},
         'prices': {업체: 합산금액}, 'min_vendor':, 'min_price':},
      ],
      'subtotals': {업체: 총액},
      'vendor_levels': {업체: 비교단위_개수},  # 각 업체가 몇 개 단위로 봤나
    }
    """
    import json as _json
    with get_conn() as c:
        subs = c.execute("""
            SELECT submission_id, vendor_name, compare_units, compare_level
            FROM submissions
            WHERE bid_id = ? AND extraction_status = 'done'
            ORDER BY vendor_name
        """, (bid_id,)).fetchall()

    vendors = []
    vendor_units = {}   # 업체 -> {비교단위 label: {amount, path}}
    subtotals = {}
    vendor_levels = {}

    for s in subs:
        sd = dict(s)
        vendor = sd["vendor_name"]
        vendors.append(vendor)
        sid = sd["submission_id"]

        # 저장된 비교 단위 경로 집합
        try:
            units = _json.loads(sd.get("compare_units") or "[]")
        except Exception:
            units = []

        # 비교 단위가 없으면 → compare_level로 폴백 (전체 한 레벨)
        if units:
            grouped = _group_by_unit_paths(sid, units)
        else:
            level = sd.get("compare_level") or 2
            g = group_items_by_level(sid, level=level)
            grouped = {x["label"]: {"amount": x["amount"], "path": x["key"]}
                       for x in g["groups"]}

        vendor_units[vendor] = grouped
        vendor_levels[vendor] = len(grouped)
        subtotals[vendor] = sum(u["amount"] for u in grouped.values())

    # 모든 업체의 비교 단위 명칭을 모아 행 구성 (명칭 기준 매칭)
    all_labels = []
    seen = set()
    for vendor in vendors:
        for label in vendor_units[vendor]:
            if label not in seen:
                seen.add(label)
                all_labels.append(label)

    rows = []
    for label in all_labels:
        prices = {}
        unit_paths = {}
        for vendor in vendors:
            u = vendor_units[vendor].get(label)
            if u:
                prices[vendor] = u["amount"]
                unit_paths[vendor] = u["path"]
        # 최저가
        min_vendor, min_price = None, None
        if prices:
            min_vendor = min(prices, key=prices.get)
            min_price = prices[min_vendor]
        rows.append({
            "label": label, "unit_paths": unit_paths,
            "prices": prices, "min_vendor": min_vendor, "min_price": min_price,
        })

    return {
        "vendors": vendors, "rows": rows,
        "subtotals": subtotals, "vendor_levels": vendor_levels,
    }


def _group_by_unit_paths(submission_id, unit_paths):
    """주어진 비교 단위 경로들로 항목을 묶어 금액 합계.

    각 항목의 path가 어느 비교 단위 경로의 하위인지 판정해서 합산.
    unit_paths: ["재료비 > 기구부", "이윤 및 관리비", ...]
    반환: {label: {amount, path}}
    """
    items = get_items(submission_id, headers=False)
    # 긴 경로 우선 매칭 (더 구체적인 단위 먼저)
    sorted_units = sorted(unit_paths, key=lambda p: -len(p))
    result = {}
    for it in items:
        d = dict(it)
        path = d.get("path") or ""
        amt = d.get("amount") or 0
        # 이 항목이 속한 비교 단위 찾기 (path가 unit으로 시작)
        matched = None
        for unit in sorted_units:
            if path == unit or path.startswith(unit + " > ") or path.startswith(unit + ">"):
                matched = unit
                break
        if matched is None:
            # 어느 단위에도 안 맞으면 자기 path를 단위로 (또는 미분류)
            matched = path or (d.get("category") or "(미분류)")
        label = _split_path(matched)[-1] if _split_path(matched) else matched
        g = result.setdefault(label, {"amount": 0.0, "path": matched})
        g["amount"] += amt
    return result


def _apply_compare_units(all_items, units_by_sub):
    """잎 품목들을 각 제출서의 비교 단위로 묶는다.

    비교 단위에 속한 잎들을 하나의 묶음 항목으로 합산.
    묶음은 dict로, 비교 화면이 잎처럼 다룰 수 있게 동일 필드를 채운다.
    비교 단위가 없는 제출서(units_by_sub에 없음)의 잎은 그대로 통과.

    all_items: sqlite3.Row 리스트 (submission_items + vendor_name)
    units_by_sub: {submission_id: [unit_path...] (긴 경로 우선 정렬)}
    반환: dict 리스트 (잎 또는 묶음)
    """
    out = []
    # 제출서별 묶음 누적: (submission_id, unit_path) -> 묶음 dict
    groups = {}
    for row in all_items:
        it = dict(row)
        sid = it.get("submission_id")
        units = units_by_sub.get(sid)
        if not units:
            out.append(it)  # 비교 단위 없는 제출서 → 잎 그대로
            continue
        path = it.get("path") or ""
        matched = None
        # 구분자 정규화 후 비교 (>, / 등 혼용·공백 차이에 견고)
        norm_path = " > ".join(_split_path(path))
        for unit in units:
            norm_unit = " > ".join(_split_path(unit))
            if norm_path == norm_unit or norm_path.startswith(norm_unit + " > "):
                matched = unit
                break
        if matched is None:
            # 어느 단위에도 안 맞으면 잎 그대로 (개별 항목)
            out.append(it)
            continue
        label = _split_path(matched)[-1] if _split_path(matched) else matched
        key = (sid, matched)
        g = groups.get(key)
        if g is None:
            g = {
                "item_id": it.get("item_id"),   # 대표 (묶음 식별)
                "submission_id": sid,
                "vendor_name": it.get("vendor_name"),
                "name_raw": label,
                "name_normalized": label,        # 묶음명으로 비교
                "spec": None,
                "unit": None,
                "quantity": None,
                "unit_price": None,
                "amount": 0.0,
                "category": it.get("category"),
                "path": matched,
                "_members": [],
                "is_unit_group": True,
            }
            groups[key] = g
            out.append(g)   # 첫 등장 위치에 묶음 삽입 (순서 유지)
        g["amount"] = (g["amount"] or 0) + (it.get("amount") or 0)
        nm = it.get("name_normalized") or it.get("name_raw") or ""
        if nm:
            g["_members"].append(nm)

    # 묶음 spec에 하위 멤버 요약 + 단가(unit_price)=합산금액 (화면 prices 표시용)
    for g in out:
        if isinstance(g, dict) and g.get("is_unit_group"):
            mem = g.pop("_members", [])
            if mem:
                g["spec"] = f"({len(mem)}개: " + ", ".join(mem[:5]) + (" ..." if len(mem) > 5 else "") + ")"
            # 비교 화면은 prices(=unit_price)로 금액 표시·최저가 비교.
            # 묶음은 단가가 없으므로 합산 금액을 단가 자리에 넣어 표시되게 한다.
            g["unit_price"] = g["amount"]
            g["quantity"] = 1
    return out


def category_order_for_bid(bid_id: str, present_cats=None) -> list:
    """입찰의 카테고리 표시 순서를 결정.

    우선순위:
    1. 입찰에 저장된 사용자 지정 순서(bids.category_order)가 있으면 그것을 우선
    2. 없으면 도메인 사전(catalog_categories)의 기본 sort_order
    3. 사전·저장순서에 없지만 실제 데이터에 있는 분류(present_cats)는 뒤에 덧붙여 유실 방지
    """
    import json as _json
    with get_conn() as c:
        row = c.execute("SELECT domain, category_order FROM bids WHERE bid_id = ?",
                        (bid_id,)).fetchone()
        rd = dict(row) if row else {}
        domain = rd.get("domain") or "IT"
        cats = c.execute("""
            SELECT name FROM catalog_categories
            WHERE is_active = 1 AND (domain = ? OR domain = 'ALL')
            ORDER BY sort_order, name
        """, (domain,)).fetchall()

    # 기본순서 (분류관리)
    default_order = []
    for r in cats:
        nm = r["name"]
        if nm not in default_order:
            default_order.append(nm)

    # 사용자 지정 순서가 있으면 그것을 기준으로, 나머지는 기본순서 뒤에 덧붙임
    saved = []
    if rd.get("category_order"):
        try:
            saved = [x for x in _json.loads(rd["category_order"]) if x]
        except Exception:
            saved = []

    if saved:
        order = [x for x in saved]                       # 저장 순서 우선
        for nm in default_order:                          # 저장에 없는 기본분류 뒤에
            if nm not in order:
                order.append(nm)
    else:
        order = list(default_order)

    for nm in (present_cats or []):                       # 데이터에만 있는 분류 보존
        if nm and nm not in order:
            order.append(nm)
    return order


def set_bid_category_order(bid_id: str, ordered_cats: list):
    """입찰별 분류 표시 순서 저장(사용자 재정렬). 빈 리스트/None이면 기본순서로 되돌림."""
    import json as _json
    val = _json.dumps([c for c in (ordered_cats or []) if c], ensure_ascii=False) \
          if ordered_cats else None
    with get_conn() as c:
        c.execute("UPDATE bids SET category_order = ? WHERE bid_id = ?", (val, bid_id))


def reset_bid_category_order(bid_id: str):
    """분류 순서를 분류관리 기본순서로 되돌림."""
    with get_conn() as c:
        c.execute("UPDATE bids SET category_order = NULL WHERE bid_id = ?", (bid_id,))


def compare_bid_submissions(bid_id):
    """
    같은 입찰의 모든 제출서를 업체별로 피벗.

    반환: {
      'vendors': ['A업체', 'B업체', ...],
      'categories': {
          '자재': [
              {
                'name_normalized': '랙 서버 (2U)',
                'spec': 'Xeon Gold 6448Y, 256GB',
                'unit': '대',
                'prices': {'A업체': 12500000, 'B업체': 11800000, ...},
                'quantities': {'A업체': 30, ...},
              },
              ...
          ],
          ...
      },
      'subtotals': {'A업체': 2289400000, ...},
      'category_totals': {'A업체': {'자재': ..., '인건비': ...}, ...},
      'clusters': [   ← 확정된 클러스터 목록 (없으면 빈 배열)
          {
            'cluster_id': ...,
            'representative_name': '랙 서버 (2U)',
            'items': [  ← 클러스터에 속한 item_id 목록
              {'item_id': ..., 'vendor_name': ..., 'name_raw': ...,
               'unit_price': ..., 'quantity': ..., 'amount': ...}
            ],
            'min_vendor': '최저가 업체명',
            'min_price': 최저 단가,
          }
      ]
    }
    """
    with get_conn() as c:
        vendors_rows = c.execute("""
            SELECT submission_id, vendor_name, subtotal_excl_vat,
                   has_usd_items, fx_rate_used
            FROM submissions
            WHERE bid_id = ? AND extraction_status = 'done'
              AND deleted_at IS NULL
            ORDER BY subtotal_excl_vat NULLS LAST
        """, (bid_id,)).fetchall()

        if not vendors_rows:
            return {"vendors": [], "categories": {}, "subtotals": {},
                    "clusters": []}

        vendors   = [r["vendor_name"] for r in vendors_rows]
        subtotals = {r["vendor_name"]: r["subtotal_excl_vat"] for r in vendors_rows}
        fx_rates  = {r["vendor_name"]: r["fx_rate_used"]
                     for r in vendors_rows if r["fx_rate_used"]}

        all_items = c.execute("""
            SELECT i.*, s.vendor_name
            FROM submission_items i
            JOIN submissions s USING (submission_id)
            WHERE s.bid_id = ? AND i.is_header = 0
              AND s.deleted_at IS NULL
            ORDER BY i.category, i.name_normalized, i.sort_order
        """, (bid_id,)).fetchall()

        # ── 비교 단위(compare_units) 묶음 적용 ──────────────────
        # 각 제출서가 정한 비교 단위로 잎을 묶어 묶음을 하나의 "항목"으로.
        # 비교 단위 없는 제출서는 잎 그대로. (데이터만 묶고 이하 로직 동일)
        import json as _json
        unit_rows = c.execute("""
            SELECT submission_id, compare_units FROM submissions
            WHERE bid_id = ? AND deleted_at IS NULL
        """, (bid_id,)).fetchall()
        units_by_sub = {}
        for ur in unit_rows:
            try:
                u = _json.loads(dict(ur).get("compare_units") or "[]")
            except Exception:
                u = []
            if u:
                units_by_sub[ur["submission_id"]] = sorted(u, key=lambda p: -len(p))
        if units_by_sub:
            all_items = _apply_compare_units(all_items, units_by_sub)

        # 확정된 클러스터 조회
        # accepted + pending 클러스터 모두 표시
        bid_clusters = c.execute("""
            SELECT cl.cluster_id, cl.representative_name, cl.status,
                   COALESCE(cl.display_order, 0) as display_order
            FROM catalog_clusters cl
            WHERE cl.bid_id = ? AND cl.status IN ('accepted', 'pending', 'held')
            ORDER BY cl.display_order, cl.status DESC, cl.created_at
        """, (bid_id,)).fetchall()

        # 클러스터 멤버 (item_id 기준)
        clustered_item_ids = set()
        clusters_data = []

        for cl in bid_clusters:
            member_rows = c.execute("""
                SELECT cm.catalog_item_id as item_id
                FROM catalog_cluster_members cm
                WHERE cm.cluster_id = ?
            """, (cl["cluster_id"],)).fetchall()

            member_ids = {r["item_id"] for r in member_rows}
            clustered_item_ids |= member_ids

            # 해당 item_id의 실제 submission_items 조회
            cluster_items = []
            for it in all_items:
                if it["item_id"] in member_ids:
                    # it은 sqlite Row 또는 dict (compare_units 묶음 거친 경우)
                    try:
                        _path = it["path"]
                    except (KeyError, IndexError):
                        _path = it.get("path") if hasattr(it, "get") else None
                    cluster_items.append({
                        "item_id":         it["item_id"],
                        "vendor_name":     it["vendor_name"],
                        "name_raw":        it["name_raw"],
                        "name_normalized": it["name_normalized"],
                        "spec":            it["spec"],
                        "unit":            it["unit"],
                        "quantity":        it["quantity"],
                        "unit_price":      it["unit_price"],
                        "amount":          it["amount"],
                        "category":        it["category"],
                        "path":            _path,
                    })

            if not cluster_items:
                continue

            # 같은 클러스터 안에서 이름 충돌 검사 (같은 이름·다른 경로)
            _name_paths = {}
            for ci in cluster_items:
                _nm = (ci["name_normalized"] or ci["name_raw"] or "").strip()
                _name_paths.setdefault(_nm, set()).add(ci["path"] or "")
            _conflict_names = {nm for nm, ps in _name_paths.items() if len(ps) > 1}

            # 경로(path) 기준으로 그룹화 → 같은 이름이라도 경로 다르면 별도 멤버 행.
            # (클러스터명은 그대로, 안의 품목은 경로로 구분)
            group_map = {}  # group_key(path) → {vendor → {...}}
            group_disp = {}  # group_key → 표시명
            for ci in cluster_items:
                name = (ci["name_normalized"] or ci["name_raw"] or "").strip() or "미명명"
                path = ci["path"] or ""
                # 이름이 충돌하면 경로로 구분, 아니면 이름으로 묶음
                if name in _conflict_names and path:
                    gkey = path
                    # 표시: 이름 + 상위 경로(꼬리표). 단, 상위가 자기 이름과 같으면
                    # (재료비 > 재료비) 중복이므로 꼬리표 생략.
                    _pparts = [x for x in _split_path(path)[:-1] if x and x != name]
                    parent = " > ".join(_pparts)
                    disp = name + (f"  ({parent})" if parent else "")
                else:
                    gkey = name
                    disp = name
                if gkey not in group_map:
                    group_map[gkey] = {}
                    group_disp[gkey] = disp
                group_map[gkey][ci["vendor_name"]] = {
                    "item_id":         ci["item_id"],
                    "name_raw":        ci["name_raw"],
                    "name_normalized": ci["name_normalized"],
                    "path":            ci["path"],
                    "unit_price":      ci["unit_price"],
                    "amount":          ci["amount"],
                    "quantity":        ci["quantity"],
                }

            # groups 리스트 생성 (각 그룹별 최저/최고 계산)
            groups = []
            for gkey, vcells in group_map.items():
                gname = group_disp.get(gkey, gkey)
                gprices = [(v, d["unit_price"])
                           for v, d in vcells.items() if d["unit_price"]]
                g_min_v, g_min_p = (
                    min(gprices, key=lambda x: x[1]) if gprices else (None, None)
                )
                g_max_v, g_max_p = (
                    max(gprices, key=lambda x: x[1]) if len(gprices) > 1 else (None, None)
                )
                groups.append({
                    "group_name": gname,
                    "cells":      vcells,   # vendor → {item_id, name_raw, unit_price, ...}
                    "min_vendor": g_min_v,
                    "min_price":  g_min_p,
                    "max_vendor": g_max_v,
                    "max_price":  g_max_p,
                })

            # 클러스터 전체 최저/최고 — 업체별 amount 합계 기준
            from collections import defaultdict as _dd
            vendor_totals = _dd(float)
            for ci in cluster_items:
                vendor_totals[ci["vendor_name"]] += (ci["amount"] or 0)
            if vendor_totals:
                min_vendor = min(vendor_totals, key=lambda v: vendor_totals[v])
                min_price  = vendor_totals[min_vendor]   # amount 합계
                if len(vendor_totals) > 1:
                    max_vendor = max(vendor_totals, key=lambda v: vendor_totals[v])
                    max_price  = vendor_totals[max_vendor]
                else:
                    max_vendor = max_price = None
            else:
                min_vendor = min_price = max_vendor = max_price = None

            # 클러스터 카테고리 — 멤버 다수결
            from collections import Counter
            cat_cnt = Counter(ci["category"] for ci in cluster_items if ci["category"])
            cl_cat  = cat_cnt.most_common(1)[0][0] if cat_cnt else "기타"

            # 최저 업체의 대표 단가 (벤치마크 비교용 — amount와 단위 통일)
            min_unit_price = min(
                (ci["unit_price"] for ci in cluster_items
                 if ci["vendor_name"] == min_vendor and ci["unit_price"]),
                default=None
            ) if min_vendor else None

            clusters_data.append({
                "cluster_id":          cl["cluster_id"],
                "representative_name": cl["representative_name"],
                "status":              cl["status"],
                "cat":                 cl_cat,       # 카테고리 (정렬/배지용)
                "members":             cluster_items,
                "groups":              groups,
                "min_vendor":          min_vendor,
                "min_price":           min_price,       # amount 합계
                "min_unit_price":      min_unit_price,  # 대표 단가 (벤치마크 비교용)
                "max_vendor":          max_vendor,
                "max_price":           max_price,
                "vendors":             vendors,
            })

        # 카테고리 순서: 사전(catalog_categories) 도메인 기준 + 데이터 존재 분류 보존
        _present = {c["cat"] for c in clusters_data if c.get("cat")}
        _dict_order = category_order_for_bid(bid_id, present_cats=_present)
        _cat_rank = {name: i for i, name in enumerate(_dict_order)}
        _rank_max = len(_dict_order)
        clusters_data.sort(key=lambda c: (
            _cat_rank.get(c["cat"], _rank_max),
            (c["representative_name"] or "").lower()
        ))

        # 카테고리별 피벗 (클러스터 미포함 항목만) — 사전 순서
        cat_order = list(_dict_order)
        categories = {cat: {} for cat in cat_order}
        cat_totals = {v: {cat: 0 for cat in cat_order} for v in vendors}

        # ── 1-pass: 같은 이름 충돌 검사 ──
        # 같은 카테고리 안에서 한 이름이 서로 다른 path의 항목들에 쓰이면 "충돌".
        # 충돌하는 이름은 전체 path로 구분 표시한다.
        from collections import defaultdict as _dd
        name_paths = _dd(lambda: _dd(set))  # cat -> name -> {path...}
        for it in all_items:
            if it["item_id"] in clustered_item_ids:
                continue
            cat = it["category"] or "기타"
            nm = (it["name_normalized"] or it["name_raw"] or "").strip()
            if not nm:
                continue
            name_paths[cat][nm].add(it["path"] or "")
        # 충돌 이름: path가 2개 이상인 (cat, name)
        conflicted = {(cat, nm) for cat, nmap in name_paths.items()
                      for nm, paths in nmap.items() if len(paths) > 1}

        for it in all_items:
            if it["item_id"] in clustered_item_ids:
                continue  # 클러스터에 포함된 항목은 제외
            cat = it["category"] or "기타"
            vendor = it["vendor_name"]
            nm = (it["name_normalized"] or it["name_raw"] or "").strip()
            if not nm:
                continue
            # 충돌하는 이름은 전체 path를 키·표시명으로 (같은 이름 구분)
            if (cat, nm) in conflicted and it["path"]:
                name_key = it["path"]
                disp_name = it["path"]
            else:
                name_key = nm
                disp_name = nm
            if cat not in categories:
                categories[cat] = {}
            for _v in vendors:
                cat_totals[_v].setdefault(cat, 0)
            if name_key not in categories[cat]:
                categories[cat][name_key] = {
                    "name":      disp_name,
                    "spec":      it["spec"],
                    "unit":      it["unit"],
                    "path":      it["path"],
                    "prices":    {},
                    "quantities":{},
                    "amounts":   {},
                    "item_ids":  {},  # vendor → item_id (셀 선택용)
                }
            row = categories[cat][name_key]
            row["prices"][vendor]     = it["unit_price"]
            row["quantities"][vendor] = it["quantity"]
            row["amounts"][vendor]    = it["amount"]
            row["item_ids"][vendor]   = it["item_id"]
            if cat in cat_totals[vendor]:
                cat_totals[vendor][cat] += (it["amount"] or 0)

        # 클러스터 항목도 카테고리 소계에 포함 (클러스터 이동 후에도 소계가 유지되도록)
        for cl in clusters_data:
            for ci in cl["members"]:
                cat    = ci["category"] or "기타"
                vendor = ci["vendor_name"]
                if vendor in cat_totals and cat in cat_totals[vendor]:
                    cat_totals[vendor][cat] += (ci["amount"] or 0)

        result_cats = {}
        for cat in cat_order:
            if categories.get(cat):
                result_cats[cat] = list(categories[cat].values())
        # 기타 카테고리
        for cat, items_dict in categories.items():
            if cat not in cat_order and items_dict:
                result_cats[cat] = list(items_dict.values())

        # ── 과거 가격 벤치마크 조회 ───────────────────
        # 현재 입찰의 submission_items에 catalog_item_id가 연결된 것들에 대해
        # 과거 price_history(이전 입찰)의 평균/최저/최고 계산
        benchmarks = {}  # catalog_item_id → {avg, min_p, max_p, count, prev_bid_count}
        try:
            # 현재 입찰의 project_id 조회 (동일 프로젝트 입찰 전체 제외)
            cur_project = c.execute(
                "SELECT project_id FROM bids WHERE bid_id = ?", (bid_id,)
            ).fetchone()
            cur_project_id = cur_project["project_id"] if cur_project else None

            # 현재 입찰에 속한 submission_ids
            current_sids = tuple(r["submission_id"] for r in vendors_rows)
            if current_sids and cur_project_id:
                ph_rows = c.execute(f"""
                    SELECT ph.catalog_item_id,
                           AVG(ph.unit_price)  as avg_price,
                           MIN(ph.unit_price)  as min_price,
                           MAX(ph.unit_price)  as max_price,
                           AVG(ph.amount)      as avg_amount,
                           MIN(ph.amount)      as min_amount,
                           MAX(ph.amount)      as max_amount,
                           COUNT(*)            as total_count,
                           COUNT(DISTINCT b.bid_id) as bid_count
                    FROM price_history ph
                    JOIN submissions s ON ph.submission_id = s.submission_id
                    JOIN bids b ON s.bid_id = b.bid_id
                    JOIN projects pp ON b.project_id = pp.project_id
                    WHERE ph.catalog_item_id IS NOT NULL
                      AND ph.unit_price > 0
                      AND b.project_id != ?
                      AND s.deleted_at IS NULL
                      AND b.status != 'cancelled'
                      AND pp.status != 'archived'
                    GROUP BY ph.catalog_item_id
                    HAVING total_count >= 1
                """, (cur_project_id,)).fetchall()
                for row in ph_rows:
                    benchmarks[row["catalog_item_id"]] = {
                        "avg":        row["avg_price"],
                        "min_p":      row["min_price"],
                        "max_p":      row["max_price"],
                        "avg_amount": row["avg_amount"],
                        "min_amount": row["min_amount"],
                        "max_amount": row["max_amount"],
                        "count":      row["total_count"],
                        "bid_count":  row["bid_count"],
                        "details":    [],
                    }
                # 마우스오버용 업체별 상세 내역
                if benchmarks:
                    bm_cids = list(benchmarks.keys())
                    det_rows = c.execute(f"""
                        SELECT ph.catalog_item_id, b.name as bid_name,
                               p.name as project_name,
                               s.vendor_name, ph.unit_price, ph.amount, ph.quantity
                        FROM price_history ph
                        JOIN submissions s ON ph.submission_id = s.submission_id
                        JOIN bids b ON s.bid_id = b.bid_id
                        JOIN projects p ON b.project_id = p.project_id
                        WHERE ph.catalog_item_id IN ({','.join('?'*len(bm_cids))})
                          AND b.project_id != ?
                          AND ph.unit_price > 0
                          AND s.deleted_at IS NULL
                          AND b.status != 'cancelled'
                          AND p.status != 'archived'
                        ORDER BY ph.unit_price
                    """, bm_cids + [cur_project_id]).fetchall()
                    for dr in det_rows:
                        cid = dr["catalog_item_id"]
                        if cid in benchmarks and not benchmarks[cid]["details"]:
                            benchmarks[cid]["details"].append({
                                "bid_name":     dr["bid_name"],
                                "project_name": dr["project_name"],
                                "vendor_name":  dr["vendor_name"],
                                "unit_price":   dr["unit_price"],
                                "amount":       dr["amount"],
                                "quantity":     dr["quantity"],
                            })
        except Exception:
            pass  # 벤치마크 실패해도 비교 페이지는 정상 표시

        # 각 미분류 row에 benchmark 주입
        for cat_rows_list in result_cats.values():
            for row in cat_rows_list:
                # 해당 row의 item_id들에 연결된 catalog_item_id 조회
                row_item_ids = list(row["item_ids"].values())
                if not row_item_ids:
                    continue
                placeholders = ','.join('?' * len(row_item_ids))
                cat_links = c.execute(f"""
                    SELECT DISTINCT catalog_item_id FROM submission_items
                    WHERE item_id IN ({placeholders})
                      AND catalog_item_id IS NOT NULL
                      AND match_status = 'confirmed'
                """, row_item_ids).fetchall()
                for link in cat_links:
                    cid_key = link["catalog_item_id"]
                    if cid_key in benchmarks:
                        row["benchmark"] = benchmarks[cid_key]
                        break

        # 클러스터에도 benchmark 주입
        # 이름/별칭 기반 매칭을 위해 전체 catalog_items 미리 로드
        import json as _json
        all_ci_for_bm = c.execute("""
            SELECT catalog_item_id, name_canonical, aliases
            FROM catalog_items WHERE is_active = 1
        """).fetchall()

        def _find_bm_cid(name: str) -> str | None:
            """이름 또는 별칭으로 benchmark가 있는 catalog_item_id 반환"""
            if not name:
                return None
            nl = name.lower()
            for ci in all_ci_for_bm:
                if ci["name_canonical"].lower() == nl and ci["catalog_item_id"] in benchmarks:
                    return ci["catalog_item_id"]
            for ci in all_ci_for_bm:
                try:
                    al = _json.loads(ci["aliases"] or "[]")
                except Exception:
                    al = []
                if any(a.lower() == nl for a in al) and ci["catalog_item_id"] in benchmarks:
                    return ci["catalog_item_id"]
            return None

        def _catalog_match(name: str) -> bool:
            """이름이 기존 catalog_items에 존재하는지 여부 (benchmark 무관)"""
            if not name:
                return False
            nl = name.lower()
            for ci in all_ci_for_bm:
                if ci["name_canonical"].lower() == nl:
                    return True
                try:
                    al = _json.loads(ci["aliases"] or "[]")
                except Exception:
                    al = []
                if any(a.lower() == nl for a in al):
                    return True
            return False

        for cl in clusters_data:
            cl_row = c.execute("""
                SELECT representative_item_id, status, representative_name
                FROM catalog_clusters WHERE cluster_id = ?
            """, (cl["cluster_id"],)).fetchone()
            if not cl_row:
                continue

            found_cid = None

            # ① accepted 클러스터: representative_item_id → catalog_item_id 직접 매칭
            if cl_row["status"] == "accepted" and cl_row["representative_item_id"]:
                cat_item = c.execute("""
                    SELECT catalog_item_id FROM catalog_items
                    WHERE catalog_item_id = ?
                """, (cl_row["representative_item_id"],)).fetchone()
                if cat_item and cat_item["catalog_item_id"] in benchmarks:
                    found_cid = cat_item["catalog_item_id"]

            # ② 이름 기반 fallback (pending/held 포함, accepted UUID 불일치 대비)
            if not found_cid:
                found_cid = _find_bm_cid(cl_row["representative_name"] or "")

            if found_cid:
                cl["benchmark"] = benchmarks[found_cid]

            # ③ 카탈로그 참조 여부 (pending 포함, benchmark 없어도 표시)
            cl["catalog_match"] = _catalog_match(cl_row["representative_name"] or "")

        return {
            "vendors":         vendors,
            "categories":      result_cats,
            "subtotals":       subtotals,
            "category_totals": cat_totals,
            "clusters":        clusters_data,
            "fx_rates":        fx_rates,
            "benchmarks":      benchmarks,
        }
    with get_conn() as c:
        # 제출된 업체 목록 (완료된 것만)
        vendors_rows = c.execute("""
            SELECT submission_id, vendor_name, subtotal_excl_vat
            FROM submissions
            WHERE bid_id = ? AND extraction_status = 'done'
              AND deleted_at IS NULL
            ORDER BY subtotal_excl_vat NULLS LAST
        """, (bid_id,)).fetchall()

        if not vendors_rows:
            return {"vendors": [], "categories": {}, "subtotals": {}}

        vendors = [r["vendor_name"] for r in vendors_rows]
        sub_map = {r["vendor_name"]: r["submission_id"] for r in vendors_rows}
        subtotals = {r["vendor_name"]: r["subtotal_excl_vat"] for r in vendors_rows}

        # 모든 라인 아이템 수집 (삭제된 업체 제외)
        all_items = c.execute("""
            SELECT i.*, s.vendor_name
            FROM submission_items i
            JOIN submissions s USING (submission_id)
            WHERE s.bid_id = ? AND i.is_header = 0
              AND s.deleted_at IS NULL
            ORDER BY i.category, i.name_normalized, i.sort_order
        """, (bid_id,)).fetchall()

        # 카테고리별, 품목별 피벗 — 사전(도메인) 순서 + 데이터 존재 분류 보존
        _present2 = {(it["category"] or "기타") for it in all_items}
        cat_order = category_order_for_bid(bid_id, present_cats=_present2)
        categories = {cat: {} for cat in cat_order}  # cat -> {name_key -> row_data}
        cat_totals = {v: {cat: 0 for cat in cat_order} for v in vendors}

        for it in all_items:
            cat = it["category"] or "기타"
            vendor = it["vendor_name"]
            # 품목 식별 키: name_normalized 우선, 없으면 name_raw
            name_key = (it["name_normalized"] or it["name_raw"] or "").strip()
            if not name_key:
                continue

            if cat not in categories:
                categories[cat] = {}

            if name_key not in categories[cat]:
                categories[cat][name_key] = {
                    "name": name_key,
                    "spec": it["spec"],
                    "unit": it["unit"],
                    "path": it["path"],
                    "prices": {},
                    "quantities": {},
                    "amounts": {},
                }

            row = categories[cat][name_key]
            row["prices"][vendor]    = it["unit_price"]
            row["quantities"][vendor] = it["quantity"]
            row["amounts"][vendor]   = it["amount"]

            # 카테고리별 합계
            if cat in cat_totals[vendor]:
                cat_totals[vendor][cat] += (it["amount"] or 0)

        # dict → list 변환 (정렬 유지)
        result_cats = {}
        for cat in cat_order:
            if categories.get(cat):
                result_cats[cat] = list(categories[cat].values())

        return {
            "vendors":         vendors,
            "categories":      result_cats,
            "subtotals":       subtotals,
            "category_totals": cat_totals,
        }


# ─── 입찰 간 단순 비교 (Phase 2 전 임시) ────────

def cross_bid_price(project_id, name_query):
    """
    같은 프로젝트 내, 품목명으로 모든 입찰의 가격 이력 조회.
    Phase 2에서는 catalog_item_id 기반으로 교체됨.
    """
    with get_conn() as c:
        return c.execute("""
            SELECT
                p.name as project_name,
                b.name as bid_name,
                b.due_date,
                s.vendor_name,
                i.name_normalized,
                i.spec,
                i.quantity,
                i.unit,
                i.unit_price,
                i.amount
            FROM submission_items i
            JOIN submissions s USING (submission_id)
            JOIN bids b USING (bid_id)
            JOIN projects p USING (project_id)
            WHERE p.project_id = ?
              AND i.is_header = 0
              AND (i.name_normalized LIKE ? OR i.name_raw LIKE ?)
              AND s.extraction_status = 'done'
              AND b.status != 'cancelled'          -- 철회 입찰 통계 제외
            ORDER BY b.due_date DESC, s.vendor_name
        """, (project_id, f"%{name_query}%", f"%{name_query}%")).fetchall()


def cross_project_price(name_query, project_ids=None):
    """
    전체 프로젝트에 걸쳐 품목 가격 이력 조회 (참조자료 활용).
    project_ids: 기준정보 검색 결과 등 프로젝트 집합으로 범위 제한.
                 None이면 전체, 빈 리스트면 결과 없음.
    Phase 2에서는 catalog 기반으로 교체됨.
    """
    if project_ids is not None and len(project_ids) == 0:
        return []
    pid_filter = ""
    params = [f"%{name_query}%", f"%{name_query}%"]
    if project_ids is not None:
        pid_filter = (" AND p.project_id IN ("
                      + ",".join("?" * len(project_ids)) + ")")
        params += list(project_ids)
    with get_conn() as c:
        return c.execute(f"""
            SELECT
                p.name as project_name,
                b.name as bid_name,
                b.due_date,
                s.vendor_name,
                i.name_normalized,
                i.spec,
                i.quantity,
                i.unit,
                i.unit_price,
                i.amount
            FROM submission_items i
            JOIN submissions s USING (submission_id)
            JOIN bids b USING (bid_id)
            JOIN projects p USING (project_id)
            WHERE i.is_header = 0
              AND (i.name_normalized LIKE ? OR i.name_raw LIKE ?)
              AND s.extraction_status = 'done'
              AND b.status != 'cancelled'          -- 철회 입찰 통계 제외
              AND p.status != 'archived'           -- 보관 프로젝트 통계 제외
              {pid_filter}
            ORDER BY b.due_date DESC, s.vendor_name
        """, params).fetchall()


# ─── 사용자 ────────────────────────────────────

def create_user(email, name, role="viewer-summary", dept=None, password_hash=""):
    uid = new_id()
    with get_conn() as c:
        c.execute("""
            INSERT INTO users (user_id, email, name, dept, role, password_hash)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (uid, email, name, dept, role, password_hash))
    return uid


def save_user_api_key(user_id: str, plain_key: str):
    """사용자의 LLM API 키를 암호화하여 저장"""
    from auth.crypto import encrypt_api_key
    enc = encrypt_api_key(plain_key.strip()) if plain_key.strip() else ""
    with get_conn() as c:
        c.execute("""
            UPDATE users SET llm_api_key_enc = ?, updated_at = ?
            WHERE user_id = ?
        """, (enc or None, datetime.now().isoformat(), user_id))


def get_user_api_key(user_id: str) -> str:
    """사용자의 LLM API 키를 복호화하여 반환 (없으면 '')"""
    from auth.crypto import decrypt_api_key
    with get_conn() as c:
        row = c.execute(
            "SELECT llm_api_key_enc FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row or not row[0]:
        return ""
    return decrypt_api_key(row[0])


def save_user_llm_settings(user_id: str, provider: str,
                            model: str = None, plain_key: str = None,
                            base_url: str = None, verify_ssl: bool = True):
    """사용자의 LLM provider + model + API 키 + base_url + verify_ssl을 저장"""
    from auth.crypto import encrypt_api_key
    updates = {
        "llm_provider":    provider,
        "llm_model":       model or None,
        "llm_base_url":    base_url.strip() if base_url and base_url.strip() else None,
        "llm_verify_ssl":  1 if verify_ssl else 0,
        "updated_at":      datetime.now().isoformat(),
    }
    if plain_key is not None:
        updates["llm_api_key_enc"] = (
            encrypt_api_key(plain_key.strip()) if plain_key.strip() else None
        )
    sets = ", ".join(f"{k} = ?" for k in updates)
    with get_conn() as c:
        c.execute(f"UPDATE users SET {sets} WHERE user_id = ?",
                  list(updates.values()) + [user_id])


def get_user_llm_settings(user_id: str) -> dict:
    """사용자의 LLM 설정 전체 반환"""
    from auth.crypto import decrypt_api_key
    with get_conn() as c:
        row = c.execute("""
            SELECT llm_provider, llm_model, llm_api_key_enc, llm_base_url, llm_verify_ssl
            FROM users WHERE user_id = ?
        """, (user_id,)).fetchone()
    if not row:
        return {"provider": "claude", "model": None, "api_key": "", "base_url": "", "verify_ssl": True}
    return {
        "provider":   row[0] or "claude",
        "model":      row[1],
        "api_key":    decrypt_api_key(row[2]) if row[2] else "",
        "base_url":   row[3] or "",
        "verify_ssl": bool(row[4]) if row[4] is not None else True,
    }


def get_user_by_email(email):
    with get_conn() as c:
        return c.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1", (email,)
        ).fetchone()


def get_user(user_id):
    with get_conn() as c:
        return c.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def list_users():
    with get_conn() as c:
        return c.execute(
            "SELECT * FROM users ORDER BY role, name"
        ).fetchall()


def update_user_login(user_id):
    with get_conn() as c:
        c.execute("UPDATE users SET last_login = ? WHERE user_id = ?",
                  (datetime.now().isoformat(), user_id))


# ─── 카탈로그 카테고리 ────────────────────────────

def list_catalog_categories(domain: str = None, active_only: bool = True):
    """카테고리 목록 (도메인 필터 + 활성 필터)"""
    sql = """
        SELECT cc.*,
               p.name as parent_name,
               COUNT(ci.catalog_item_id) as n_items
        FROM catalog_categories cc
        LEFT JOIN catalog_categories p ON cc.parent_id = p.category_id
        LEFT JOIN catalog_items ci ON cc.category_id = ci.category_id
            AND ci.is_active = 1
        WHERE 1=1
    """
    params = []
    if active_only:
        sql += " AND cc.is_active = 1"
    if domain:
        sql += " AND (cc.domain = ? OR cc.domain = 'ALL')"
        params.append(domain)
    sql += " GROUP BY cc.category_id ORDER BY cc.domain, cc.sort_order, cc.name"
    with get_conn() as c:
        return c.execute(sql, params).fetchall()


def get_catalog_category(category_id):
    with get_conn() as c:
        return c.execute(
            "SELECT * FROM catalog_categories WHERE category_id = ?",
            (category_id,)
        ).fetchone()


def _norm_cat_key(s: str) -> str:
    """분류명 비교용 정규화 키: 공백·구분기호 제거 + 소문자.
    '자재 비', '자재-비', '자재비'를 같게 본다."""
    import re as _re
    if s is None:
        return ""
    return _re.sub(r"[\s\-_/()·.]", "", str(s)).strip().lower()


def get_domain_category_binding(domain: str) -> dict:
    """도메인의 분류 체계를 정규화 조회용 구조로 반환.

    반환:
      {
        "domain": domain,
        "standard": [표준 분류명, ...],           # 활성 카테고리 name (정렬순)
        "lookup": { 정규화키: 표준명 },            # 정확·별칭 모두 → 표준명
        "alias_map": { 표준명: [별칭, ...] },      # 표시용
      }
    별칭(aliases)까지 lookup에 포함하여, 표기 편차를 표준명으로 자동 귀속할 수 있게 한다.
    """
    import json as _json
    domain = domain or "IT"
    standard, lookup, alias_map = [], {}, {}
    with get_conn() as c:
        rows = c.execute("""
            SELECT name, aliases FROM catalog_categories
            WHERE is_active = 1 AND (domain = ? OR domain = 'ALL')
            ORDER BY sort_order, name
        """, (domain,)).fetchall()
    for r in rows:
        d = dict(r)
        name = d["name"]
        standard.append(name)
        lookup[_norm_cat_key(name)] = name
        aliases = []
        if d.get("aliases"):
            try:
                aliases = [a for a in _json.loads(d["aliases"]) if a and a.strip()]
            except Exception:
                aliases = []
        for a in aliases:
            lookup[_norm_cat_key(a)] = name  # 별칭 → 표준명
        alias_map[name] = aliases
    return {"domain": domain, "standard": standard,
            "lookup": lookup, "alias_map": alias_map}


def resolve_category_binding(raw_category: str, binding: dict) -> dict:
    """단일 대분류 문자열을 도메인 표준 분류에 해석.

    반환:
      {
        "raw": 입력값,
        "resolved": 표준명 또는 None,
        "status": "exact" | "alias" | "unmatched",
      }
    - exact:  표준명과 (정규화 후) 정확히 일치
    - alias:  별칭 매핑으로 표준명에 귀속
    - unmatched: 도메인 분류 체계에 없음 → 경고·재지정 대상
    """
    raw = (raw_category or "").strip()
    if not raw:
        return {"raw": raw, "resolved": None, "status": "unmatched"}
    key = _norm_cat_key(raw)
    lookup = binding.get("lookup", {})
    if key in lookup:
        std = lookup[key]
        # 원문이 표준명과 (정규화 무시하고) 동일하면 exact, 아니면 alias 귀속
        status = "exact" if _norm_cat_key(std) == key and raw == std else \
                 ("exact" if raw == std else "alias")
        return {"raw": raw, "resolved": std, "status": status}
    return {"raw": raw, "resolved": None, "status": "unmatched"}


def validate_submission_categories(submission_id: str) -> dict:
    """제출서의 모든 항목 대분류가 도메인 분류 체계에 부합하는지 검증.

    반환:
      {
        "domain": ...,
        "standard": [표준 분류...],
        "summary": { 원본대분류: {"count":N, "resolved":표준명|None, "status":...} },
        "unmatched": [ 원본대분류(미매칭)... ],
        "n_items": 총 항목,
        "n_unmatched_items": 미매칭 항목 수,
      }
    추출 후 검증 게이트(층위 2)에서 사용.
    """
    with get_conn() as c:
        srow = c.execute("""
            SELECT s.submission_id, b.domain
            FROM submissions s JOIN bids b USING (bid_id)
            WHERE s.submission_id = ?
        """, (submission_id,)).fetchone()
        if not srow:
            raise ValueError(f"제출서를 찾을 수 없습니다: {submission_id}")
        domain = (dict(srow).get("domain")) or "IT"
        items = c.execute("""
            SELECT category, COUNT(*) as cnt
            FROM submission_items
            WHERE submission_id = ? AND is_header = 0
            GROUP BY category
        """, (submission_id,)).fetchall()

    binding = get_domain_category_binding(domain)
    summary, unmatched = {}, []
    alias_normalize = {}          # {원본표기: 표준명} — 별칭 해석되나 저장값이 표준과 다름
    n_items = n_unmatched = n_alias = 0
    for it in items:
        d = dict(it)
        raw = d.get("category") or "(미지정)"
        cnt = d["cnt"]
        n_items += cnt
        res = resolve_category_binding(d.get("category"), binding)
        summary[raw] = {"count": cnt, "resolved": res["resolved"],
                        "status": res["status"]}
        if res["status"] == "unmatched":
            unmatched.append(raw)
            n_unmatched += cnt
        elif res["status"] == "alias" and res["resolved"] and res["resolved"] != raw:
            # 별칭으로 표준에 귀속되나 저장값(raw)이 아직 표준명이 아님 → 정규화 대상
            alias_normalize[raw] = res["resolved"]
            n_alias += cnt
    return {
        "domain": domain, "standard": binding["standard"],
        "summary": summary,
        "unmatched": unmatched,
        "alias_normalize": alias_normalize,
        "n_items": n_items,
        "n_unmatched_items": n_unmatched,
        "n_alias_items": n_alias,
    }


def apply_category_binding(submission_id: str, mapping: dict,
                           add_as_alias: bool = False) -> dict:
    """미매칭·편차 대분류를 표준 분류로 일괄 재지정.

    - mapping: { 원본대분류: 표준분류명 }
    - add_as_alias: True면 원본대분류를 해당 표준 분류의 별칭으로 등록
      (다음부터 자동 귀속되게 학습)

    submission_items.category와 path의 첫 세그먼트를 표준명으로 갱신한다.
    반환: {"items_updated": N, "aliases_added": M}
    """
    import json as _json
    PATH_SEP = " > "
    if not mapping:
        return {"items_updated": 0, "aliases_added": 0}
    with get_conn() as c:
        brow = c.execute("""
            SELECT b.domain FROM submissions s JOIN bids b USING (bid_id)
            WHERE s.submission_id = ?
        """, (submission_id,)).fetchone()
        domain = (dict(brow).get("domain") if brow else None) or "IT"

        items_updated = 0
        for raw_cat, std_cat in mapping.items():
            std_cat = (std_cat or "").strip()
            if not std_cat:
                continue
            rows = c.execute("""
                SELECT item_id, path FROM submission_items
                WHERE submission_id = ? AND category = ?
            """, (submission_id, raw_cat)).fetchall()
            for r in rows:
                d = dict(r)
                # path 첫 세그먼트를 표준명으로 치환
                new_path = d.get("path")
                if new_path:
                    parts = [p.strip() for p in new_path.split(PATH_SEP)]
                    if parts:
                        parts[0] = std_cat
                        new_path = PATH_SEP.join(parts)
                c.execute("""
                    UPDATE submission_items SET category = ?, path = ?
                    WHERE item_id = ?
                """, (std_cat, new_path, d["item_id"]))
                items_updated += 1

        aliases_added = 0
        if add_as_alias:
            for raw_cat, std_cat in mapping.items():
                std_cat = (std_cat or "").strip()
                if not std_cat or raw_cat == std_cat:
                    continue
                crow = c.execute("""
                    SELECT category_id, aliases FROM catalog_categories
                    WHERE name = ? AND (domain = ? OR domain = 'ALL')
                          AND is_active = 1 LIMIT 1
                """, (std_cat, domain)).fetchone()
                if not crow:
                    continue
                cd = dict(crow)
                try:
                    cur = _json.loads(cd["aliases"]) if cd.get("aliases") else []
                except Exception:
                    cur = []
                if raw_cat not in cur:
                    cur.append(raw_cat)
                    c.execute("""
                        UPDATE catalog_categories SET aliases = ?, updated_at = ?
                        WHERE category_id = ?
                    """, (_json.dumps(cur, ensure_ascii=False),
                          datetime.now().isoformat(), cd["category_id"]))
                    aliases_added += 1
    return {"items_updated": items_updated, "aliases_added": aliases_added}


def create_catalog_category(name, domain='IT', parent_id=None,
                             sort_order=0, description=None):
    cid = new_id()
    with get_conn() as c:
        c.execute("""
            INSERT INTO catalog_categories
                (category_id, name, domain, parent_id, sort_order,
                 description, is_active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        """, (cid, name, domain, parent_id or None,
              sort_order, description, datetime.now().isoformat()))
    return cid


def update_catalog_category(category_id, **kwargs):
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    with get_conn() as c:
        c.execute(f"UPDATE catalog_categories SET {sets} WHERE category_id = ?",
                  list(kwargs.values()) + [category_id])


def toggle_catalog_category(category_id, is_active: bool):
    """카테고리 활성/비활성화 (소프트 삭제 대신)"""
    with get_conn() as c:
        c.execute("""
            UPDATE catalog_categories
            SET is_active = ?, updated_at = ?
            WHERE category_id = ?
        """, (1 if is_active else 0, datetime.now().isoformat(), category_id))


def delete_catalog_category(category_id):
    """카테고리 삭제 (소속 활성 품목이 없을 때만 — 비활성화 권장)"""
    with get_conn() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM catalog_items WHERE category_id = ? AND is_active = 1",
            (category_id,)
        ).fetchone()[0]
        if n > 0:
            raise ValueError(f"소속 활성 품목 {n}개가 있어 삭제할 수 없습니다. 비활성화를 권장합니다.")
        c.execute("DELETE FROM catalog_categories WHERE category_id = ?",
                  (category_id,))


# ─── 도메인 관련 ─────────────────────────────────

# 폴백 기본 도메인 (domains 테이블이 비었을 때만 사용)
DOMAIN_LIST = ['IT', '설비', '용역', '기타']


def list_domains(active_only: bool = True):
    """도메인 목록(행) 반환 — domains 테이블 기반."""
    sql = "SELECT * FROM domains"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY sort_order, name"
    with get_conn() as c:
        return c.execute(sql).fetchall()


def list_domain_names(active_only: bool = True) -> list:
    """도메인 이름 목록. 테이블이 비어 있으면 DOMAIN_LIST 폴백.

    코드 전반이 이 함수를 호출하도록 하여, 도메인을 동적으로 추가·관리할 수 있게 한다.
    """
    try:
        rows = list_domains(active_only=active_only)
        names = [dict(r)["name"] for r in rows]
        return names or list(DOMAIN_LIST)
    except Exception:
        return list(DOMAIN_LIST)


def get_domain(domain_id: str):
    with get_conn() as c:
        return c.execute("SELECT * FROM domains WHERE domain_id = ?",
                         (domain_id,)).fetchone()


def create_domain(name: str, description: str = None, sort_order: int = None) -> str:
    """새 도메인 추가. 이름 중복(UNIQUE) 시 예외."""
    name = (name or "").strip()
    if not name:
        raise ValueError("도메인명을 입력하세요.")
    did = new_id()
    with get_conn() as c:
        if sort_order is None:
            mx = c.execute("SELECT COALESCE(MAX(sort_order),0) FROM domains").fetchone()[0]
            sort_order = (mx or 0) + 1
        c.execute("""
            INSERT INTO domains (domain_id, name, description, sort_order, is_active, updated_at)
            VALUES (?, ?, ?, ?, 1, ?)
        """, (did, name, description, sort_order, datetime.now().isoformat()))
    return did


def update_domain(domain_id: str, **kwargs):
    allowed = {"name", "description", "sort_order", "is_active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as c:
        c.execute(f"UPDATE domains SET {sets} WHERE domain_id = ?",
                  list(fields.values()) + [domain_id])


def toggle_domain(domain_id: str, is_active: bool):
    update_domain(domain_id, is_active=1 if is_active else 0)


def delete_domain(domain_id: str) -> bool:
    """도메인 삭제. 사용 중(입찰·카테고리 참조)이면 삭제 불가 → False.
    안전을 위해 하드 삭제 대신 사용처가 없을 때만 허용."""
    with get_conn() as c:
        d = c.execute("SELECT name FROM domains WHERE domain_id = ?",
                      (domain_id,)).fetchone()
        if not d:
            return False
        dname = dict(d)["name"]
        # 입찰·카테고리에서 사용 중인지 확인
        n_bids = c.execute("SELECT COUNT(*) FROM bids WHERE domain = ?", (dname,)).fetchone()[0]
        n_cats = c.execute("SELECT COUNT(*) FROM catalog_categories WHERE domain = ?",
                           (dname,)).fetchone()[0]
        if n_bids or n_cats:
            return False
        c.execute("DELETE FROM domains WHERE domain_id = ?", (domain_id,))
        return True


def get_bid_domain(bid_id: str) -> str:
    """입찰의 도메인 반환 (없으면 'IT')"""
    with get_conn() as c:
        row = c.execute(
            "SELECT domain FROM bids WHERE bid_id = ?", (bid_id,)
        ).fetchone()
    return (row["domain"] if row and row["domain"] else "IT")


# ─── 도메인 관리 ─────────────────────────────────

def list_domains(active_only: bool = False) -> list:
    """도메인 목록"""
    sql = "SELECT * FROM domains"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY sort_order, name"
    with get_conn() as c:
        return c.execute(sql).fetchall()


def get_domain(domain_id: str):
    with get_conn() as c:
        return c.execute(
            "SELECT * FROM domains WHERE domain_id = ?", (domain_id,)
        ).fetchone()


def get_domain_by_name(name: str):
    with get_conn() as c:
        return c.execute(
            "SELECT * FROM domains WHERE name = ?", (name,)
        ).fetchone()


def create_domain(name: str, description: str = None, sort_order: int = 0) -> str:
    """도메인 생성"""
    did = new_id()
    with get_conn() as c:
        c.execute("""
            INSERT INTO domains (domain_id, name, description, sort_order, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (did, name, description, sort_order, datetime.now().isoformat()))
    return did


def update_domain(domain_id: str, old_name: str = None, **kwargs) -> dict:
    """
    도메인 수정. 이름 변경 시 연쇄 업데이트 처리.

    Returns:
        {'cascaded': {'bids': N, 'categories': M}} — 연쇄 업데이트 건수
    """
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    cascaded = {"bids": 0, "categories": 0}

    with get_conn() as c:
        c.execute(f"UPDATE domains SET {sets} WHERE domain_id = ?",
                  list(kwargs.values()) + [domain_id])

        # 이름 변경 시 연쇄 업데이트
        new_name = kwargs.get("name")
        if new_name and old_name and old_name != new_name:
            r1 = c.execute(
                "UPDATE bids SET domain = ? WHERE domain = ?",
                (new_name, old_name)
            )
            r2 = c.execute(
                "UPDATE catalog_categories SET domain = ?, updated_at = ? WHERE domain = ?",
                (new_name, datetime.now().isoformat(), old_name)
            )
            cascaded["bids"]       = r1.rowcount
            cascaded["categories"] = r2.rowcount

    return cascaded


def toggle_domain(domain_id: str, is_active: bool):
    """도메인 활성/비활성화 (기존 데이터 유지, 신규 입찰 생성만 차단)"""
    with get_conn() as c:
        c.execute("""
            UPDATE domains SET is_active = ?, updated_at = ?
            WHERE domain_id = ?
        """, (1 if is_active else 0, datetime.now().isoformat(), domain_id))


def delete_domain(domain_id: str) -> dict:
    """
    도메인 삭제. 연결된 입찰/카테고리가 있으면 삭제 불가.
    삭제 불가 시 ValueError 발생.

    Returns:
        {'deleted': True} 또는 ValueError
    """
    with get_conn() as c:
        domain = c.execute(
            "SELECT name FROM domains WHERE domain_id = ?", (domain_id,)
        ).fetchone()
        if not domain:
            raise ValueError("도메인을 찾을 수 없습니다.")

        name = domain["name"]
        n_bids = c.execute(
            "SELECT COUNT(*) FROM bids WHERE domain = ?", (name,)
        ).fetchone()[0]
        n_cats = c.execute(
            "SELECT COUNT(*) FROM catalog_categories WHERE domain = ?", (name,)
        ).fetchone()[0]

        if n_bids > 0 or n_cats > 0:
            raise ValueError(
                f"'{name}' 도메인에 연결된 입찰 {n_bids}개, "
                f"카테고리 {n_cats}개가 있어 삭제할 수 없습니다. "
                f"비활성화를 권장합니다."
            )
        c.execute("DELETE FROM domains WHERE domain_id = ?", (domain_id,))
    return {"deleted": True}


def get_domain_impact(domain_id: str) -> dict:
    """도메인 변경/삭제 시 영향도 미리 조회"""
    domain = get_domain(domain_id)
    if not domain:
        return {}
    name = domain["name"]
    with get_conn() as c:
        n_bids = c.execute(
            "SELECT COUNT(*) FROM bids WHERE domain = ?", (name,)
        ).fetchone()[0]
        n_cats = c.execute(
            "SELECT COUNT(*) FROM catalog_categories WHERE domain = ? AND is_active = 1",
            (name,)
        ).fetchone()[0]
        n_items = c.execute("""
            SELECT COUNT(*) FROM catalog_items ci
            JOIN catalog_categories cc USING (category_id)
            WHERE cc.domain = ? AND ci.is_active = 1
        """, (name,)).fetchone()[0]
        n_submissions = c.execute("""
            SELECT COUNT(*) FROM submissions s
            JOIN bids b USING (bid_id)
            WHERE b.domain = ?
        """, (name,)).fetchone()[0]
    return {
        "domain_name":   name,
        "n_bids":        n_bids,
        "n_categories":  n_cats,
        "n_items":       n_items,
        "n_submissions": n_submissions,
    }


# ─── 카탈로그 품목 ───────────────────────────────

def list_catalog_items(category_id=None, search=None, active_only=True):
    """품목 목록 (카테고리 필터 + 검색)"""
    sql = """
        SELECT ci.*,
               cc.name as category_name,
               cc.parent_id
        FROM catalog_items ci
        LEFT JOIN catalog_categories cc USING (category_id)
        WHERE 1=1
    """
    params = []
    if active_only:
        sql += " AND ci.is_active = 1"
    if category_id:
        sql += " AND ci.category_id = ?"
        params.append(category_id)
    if search:
        sql += " AND (ci.name_canonical LIKE ? OR ci.aliases LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY cc.sort_order, ci.name_canonical"
    with get_conn() as c:
        return c.execute(sql, params).fetchall()


def get_catalog_item(catalog_item_id):
    with get_conn() as c:
        return c.execute("""
            SELECT ci.*, cc.name as category_name
            FROM catalog_items ci
            LEFT JOIN catalog_categories cc USING (category_id)
            WHERE ci.catalog_item_id = ?
        """, (catalog_item_id,)).fetchone()


def create_catalog_item(name_canonical, category_id=None,
                         aliases=None, spec_template=None,
                         unit_std=None, created_by=None):
    import json
    cid = new_id()
    with get_conn() as c:
        c.execute("""
            INSERT INTO catalog_items
                (catalog_item_id, category_id, name_canonical,
                 aliases, spec_template, unit_std, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (cid, category_id or None,
              name_canonical,
              json.dumps(aliases or [], ensure_ascii=False),
              spec_template, unit_std, created_by))
    return cid


def update_catalog_item(catalog_item_id, **kwargs):
    import json
    if "aliases" in kwargs and isinstance(kwargs["aliases"], list):
        kwargs["aliases"] = json.dumps(kwargs["aliases"], ensure_ascii=False)
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    with get_conn() as c:
        c.execute(f"UPDATE catalog_items SET {sets} WHERE catalog_item_id = ?",
                  list(kwargs.values()) + [catalog_item_id])


def delete_catalog_item(catalog_item_id):
    """소프트 삭제 (is_active = 0)"""
    with get_conn() as c:
        c.execute("""
            UPDATE catalog_items SET is_active = 0, updated_at = ?
            WHERE catalog_item_id = ?
        """, (datetime.now().isoformat(), catalog_item_id))


def catalog_stats():
    """카탈로그 전체 통계"""
    with get_conn() as c:
        n_items = c.execute(
            "SELECT COUNT(*) FROM catalog_items WHERE is_active = 1"
        ).fetchone()[0]
        n_cats = c.execute(
            "SELECT COUNT(*) FROM catalog_categories"
        ).fetchone()[0]
        n_matched = c.execute(
            "SELECT COUNT(DISTINCT catalog_item_id) FROM submission_items "
            "WHERE catalog_item_id IS NOT NULL AND match_status = 'confirmed'"
        ).fetchone()[0]
        return {"n_items": n_items, "n_categories": n_cats, "n_matched": n_matched}


# ─── 매칭 관련 ───────────────────────────────────

def get_match_summary(submission_id: str) -> dict:
    """제출서의 매칭 현황 요약"""
    with get_conn() as c:
        rows = c.execute("""
            SELECT match_status, COUNT(*) as cnt
            FROM submission_items
            WHERE submission_id = ? AND is_header = 0
            GROUP BY match_status
        """, (submission_id,)).fetchall()
    total = sum(r["cnt"] for r in rows)
    status_map = {r["match_status"]: r["cnt"] for r in rows}
    return {
        "total":      total,
        "pending":    status_map.get("pending", 0),
        "suggested":  status_map.get("suggested", 0),
        "confirmed":  status_map.get("confirmed", 0),
        "unmatched":  status_map.get("unmatched", 0),
    }


def get_items_with_match(submission_id: str) -> list:
    """매칭 정보 포함된 라인 아이템 목록"""
    with get_conn() as c:
        return c.execute("""
            SELECT si.*,
                   ci.name_canonical as catalog_name,
                   ci.unit_std as catalog_unit,
                   cc.name as catalog_category
            FROM submission_items si
            LEFT JOIN catalog_items ci ON si.catalog_item_id = ci.catalog_item_id
            LEFT JOIN catalog_categories cc ON ci.category_id = cc.category_id
            WHERE si.submission_id = ? AND si.is_header = 0
            ORDER BY si.sort_order
        """, (submission_id,)).fetchall()


def get_price_history(catalog_item_id: str) -> list:
    """품목의 가격 이력 조회"""
    with get_conn() as c:
        return c.execute("""
            SELECT ph.*, s.vendor_name
            FROM price_history ph
            JOIN submissions s USING (submission_id)
            WHERE ph.catalog_item_id = ?
            ORDER BY ph.bid_date DESC, s.vendor_name
        """, (catalog_item_id,)).fetchall()


# ─── 카탈로그 제안 (Phase 3-A) ──────────────────

def get_suggestions(submission_id: str, status: str = None) -> list:
    """제출서의 카탈로그 제안 목록 조회"""
    sql = """
        SELECT cs.*,
               si.name_raw, si.name_normalized, si.spec,
               si.category, si.unit, si.quantity, si.unit_price,
               ci.name_canonical as matched_catalog_name
        FROM catalog_suggestions cs
        JOIN submission_items si ON cs.item_id = si.item_id
        LEFT JOIN catalog_items ci ON cs.matched_catalog_item_id = ci.catalog_item_id
        WHERE cs.submission_id = ?
    """
    params = [submission_id]
    if status:
        sql += " AND cs.status = ?"
        params.append(status)
    sql += " ORDER BY cs.suggestion_type DESC, cs.similarity_score DESC"
    with get_conn() as c:
        return c.execute(sql, params).fetchall()


def get_suggestion_summary(submission_id: str) -> dict:
    """제출서의 제안 현황 요약"""
    with get_conn() as c:
        rows = c.execute("""
            SELECT status, COUNT(*) as cnt
            FROM catalog_suggestions
            WHERE submission_id = ?
            GROUP BY status
        """, (submission_id,)).fetchall()
    total = sum(r["cnt"] for r in rows)
    status_map = {r["status"]: r["cnt"] for r in rows}
    return {
        "total":    total,
        "pending":  status_map.get("pending", 0),
        "accepted": status_map.get("accepted", 0),
        "rejected": status_map.get("rejected", 0),
        "modified": status_map.get("modified", 0),
    }

# ─── 카탈로그 클러스터링 (Phase 3-B) ─────────────

def list_clusters(status: str = None, bid_id: str = None) -> list:
    """클러스터 목록 (멤버 수 포함)"""
    sql = """
        SELECT cl.*,
               COUNT(cm.catalog_item_id) as member_count
        FROM catalog_clusters cl
        JOIN catalog_cluster_members cm ON cl.cluster_id = cm.cluster_id
        WHERE 1=1
    """
    params = []
    if status:
        sql += " AND cl.status = ?"
        params.append(status)
    if bid_id:
        sql += " AND cl.bid_id = ?"
        params.append(bid_id)
    sql += " GROUP BY cl.cluster_id ORDER BY cl.created_at DESC"
    with get_conn() as c:
        return c.execute(sql, params).fetchall()


def get_cluster(cluster_id: str):
    """클러스터 상세 — 멤버는 submission_items 기반"""
    with get_conn() as c:
        cluster = c.execute(
            "SELECT * FROM catalog_clusters WHERE cluster_id = ?",
            (cluster_id,)
        ).fetchone()
        if not cluster:
            return None, []
        members = c.execute("""
            SELECT cm.*,
                   si.name_raw, si.name_normalized, si.spec,
                   si.category, si.unit, si.quantity, si.unit_price,
                   si.item_id as si_item_id,
                   s.vendor_name
            FROM catalog_cluster_members cm
            JOIN submission_items si ON cm.catalog_item_id = si.item_id
            JOIN submissions s ON si.submission_id = s.submission_id
            WHERE cm.cluster_id = ?
            ORDER BY cm.role DESC, cm.similarity_score DESC
        """, (cluster_id,)).fetchall()
        return cluster, members


def get_cluster_summary(bid_id: str = None) -> dict:
    """클러스터 현황 요약"""
    sql = """
        SELECT status, COUNT(*) as cnt
        FROM catalog_clusters WHERE 1=1
    """
    params = []
    if bid_id:
        sql += " AND bid_id = ?"
        params.append(bid_id)
    sql += " GROUP BY status"
    with get_conn() as c:
        rows = c.execute(sql, params).fetchall()
    status_map = {r["status"]: r["cnt"] for r in rows}
    return {
        "total":    sum(status_map.values()),
        "pending":  status_map.get("pending", 0),
        "accepted": status_map.get("accepted", 0),
        "rejected": status_map.get("rejected", 0),
        "held":     status_map.get("held", 0),
    }


def list_bids_with_done_submissions() -> list:
    """추출 완료 제출서가 2개 이상인 입찰 목록 (클러스터링 대상)"""
    with get_conn() as c:
        return c.execute("""
            SELECT b.bid_id, b.name as bid_name,
                   p.name as project_name,
                   b.domain,
                   COUNT(s.submission_id) as n_done
            FROM bids b
            JOIN projects p USING (project_id)
            JOIN submissions s USING (bid_id)
            WHERE s.extraction_status = 'done'
            GROUP BY b.bid_id
            HAVING n_done >= 2
            ORDER BY b.created_at DESC
        """).fetchall()


def list_submission_items_for_clustering(bid_id: str) -> list:
    """클러스터링 대상 항목.

    각 업체의 저장된 비교 단위(compare_units)로 묶어서 반환한다.
    비교 단위가 "기구부"면 기구부 안의 잎들을 하나로 합쳐 "기구부" 항목 1개로.
    → 클러스터링이 비교 단위끼리 매칭 (잎까지 펼치지 않음).
    비교 단위가 없는 업체는 잎(품목) 그대로.
    (카테고리 헤더 제외)
    """
    import json as _json
    with get_conn() as c:
        subs = c.execute("""
            SELECT submission_id, vendor_name, compare_units
            FROM submissions
            WHERE bid_id = ? AND extraction_status = 'done'
            ORDER BY vendor_name
        """, (bid_id,)).fetchall()

    result = []
    for s in subs:
        sd = dict(s)
        sid = sd["submission_id"]
        vendor = sd["vendor_name"]
        try:
            units = _json.loads(sd.get("compare_units") or "[]")
        except Exception:
            units = []

        items = [dict(i) for i in get_items(sid, headers=False)]
        items = [it for it in items if (it.get("name_raw") or it.get("name_normalized"))]

        if not units:
            # 비교 단위 없음 → 잎 그대로 (기존 동작)
            for it in items:
                _path = it.get("path") or ""
                result.append({
                    "item_id": it.get("item_id"),
                    "name_raw": it.get("name_raw"),
                    "name_normalized": it.get("name_normalized"),
                    "spec": it.get("spec"), "category": it.get("category"),
                    "unit": it.get("unit"), "quantity": it.get("quantity"),
                    "unit_price": it.get("unit_price"), "amount": it.get("amount"),
                    "vendor_name": vendor, "submission_id": sid,
                    # 경로 보존: 클러스터링 입력의 (맥락)상위·트리 표시에 사용
                    "path": _path,
                    "full_label": _path or it.get("name_normalized") or it.get("name_raw"),
                })
            continue

        # 비교 단위로 묶기 (긴 경로 우선 매칭)
        sorted_units = sorted(units, key=lambda p: -len(p))
        groups = {}  # unit_path -> 묶음 정보
        for it in items:
            path = it.get("path") or ""
            matched = None
            for unit in sorted_units:
                if path == unit or path.startswith(unit + " > ") or path.startswith(unit + ">"):
                    matched = unit
                    break
            if matched is None:
                matched = path or (it.get("category") or "(미분류)")
            label = _split_path(matched)[-1] if _split_path(matched) else matched
            g = groups.setdefault(matched, {
                "label": label, "path": matched, "amount": 0.0,
                "members": [], "n": 0, "first_item_id": it.get("item_id"),
                "category": it.get("category"),
            })
            g["amount"] += (it.get("amount") or 0)
            g["n"] += 1
            # 참조용 세부: 하위 품목명(+규격)
            nm = it.get("name_normalized") or it.get("name_raw") or ""
            sp = it.get("spec")
            if nm:
                g["members"].append(nm + (f"({sp})" if sp else ""))

        # 묶음을 클러스터링 항목으로.
        # 묶음명(label)이 비교 기준(주). 하위 세부는 참조 정보(보조).
        for upath, g in groups.items():
            is_single = (g["n"] == 1)  # 잎 1개 = 항목 단위, 여러 개 = 분류 묶음
            ref_members = g["members"][:16]  # 참조용 하위 세부 (빌더 노출 상한 16과 정합)
            # 비교 기준명: 세분류(끝부분)가 주 기준.
            # 단, 상위 레벨이 다른 동일 세분류를 구분할 수 있게 전체 경로도 보존.
            # compare_label = 세분류명(주), compare_path = 전체 경로(맥락)
            _parts = _split_path(upath)
            leaf_label = g["label"]  # 세분류명 (끝부분)
            full_label = " > ".join(_parts) if len(_parts) > 1 else leaf_label
            # 동일명 반복 경로(대분류=중분류=… 같은 이름) 정리:
            # 단일 항목이고 하위 참조가 잎 이름과 같으면 무의미한 중복이므로 제거.
            # (예: 비교단위=재료비인데 사양=재료비로 중복 노출되는 노이즈 방지)
            if is_single and ref_members and all(
                m == leaf_label or m.startswith(leaf_label + "(") for m in ref_members
            ):
                ref_members = []
            spec_val = (" / ".join(ref_members) if ref_members else None)
            result.append({
                "item_id": g["first_item_id"],
                # 비교 기준(주): 세분류명. 잎 1개면 그 품목명이 곧 묶음명.
                "name_raw": leaf_label,
                "name_normalized": leaf_label,
                # 상위 경로 포함 전체명 (상위 다른 동일 세분류 구분용).
                # 단, 모든 세그먼트가 같은 이름이면(재료비>재료비) 잎만 남겨 중복 제거.
                "full_label": (leaf_label if _parts and len(set(_parts)) == 1 else full_label),
                # 참조(보조): 묶음 안의 세부 항목·규격. LLM이 애매할 때 참조.
                "spec": spec_val,
                "category": g.get("category"),
                "unit": None, "quantity": None, "unit_price": None,
                "amount": g["amount"],
                "vendor_name": vendor, "submission_id": sid,
                # 메타: 이 항목이 묶음인지 단일 항목인지 + 참조 세부
                "compare_unit_path": upath,
                "n_items": g["n"],
                "is_group": (not is_single),
                "ref_members": ref_members,
            })

    return result
