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

def create_project(name, description="", owner_id=None):
    pid = new_id()
    with get_conn() as c:
        c.execute("""
            INSERT INTO projects (project_id, name, description, owner_id)
            VALUES (?, ?, ?, ?)
        """, (pid, name, description, owner_id))
    return pid


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


# ─── 입찰 ──────────────────────────────────────

def create_bid(project_id, name, due_date=None, description="", created_by=None):
    bid_id = new_id()
    with get_conn() as c:
        c.execute("""
            INSERT INTO bids (bid_id, project_id, name, due_date, description, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (bid_id, project_id, name, due_date, description, created_by))
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


def get_submission(submission_id):
    with get_conn() as c:
        return c.execute("""
            SELECT s.*, b.name as bid_name, p.name as project_name,
                   b.project_id
            FROM submissions s
            JOIN bids b USING (bid_id)
            JOIN projects p USING (project_id)
            WHERE s.submission_id = ?
        """, (submission_id,)).fetchone()


def list_submissions(bid_id):
    with get_conn() as c:
        return c.execute("""
            SELECT s.*,
                   COUNT(CASE WHEN i.is_header = 0 THEN 1 END) as n_items,
                   u.name as uploaded_by_name
            FROM submissions s
            LEFT JOIN submission_items i USING (submission_id)
            LEFT JOIN users u ON s.uploaded_by = u.user_id
            WHERE s.bid_id = ?
            GROUP BY s.submission_id
            ORDER BY s.subtotal_excl_vat NULLS LAST
        """, (bid_id,)).fetchall()


# ─── 라인 아이템 ────────────────────────────────

def insert_items_bulk(submission_id, items: list[dict]):
    """추출된 아이템 일괄 삽입"""
    with get_conn() as c:
        for i, it in enumerate(items):
            iid = new_id()
            c.execute("""
                INSERT INTO submission_items
                    (item_id, submission_id, line_no, sort_order, depth, is_header,
                     category, path, name_raw, name_normalized, spec,
                     quantity, unit, unit_price, unit_price_orig,
                     unit_price_currency, amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                iid, submission_id,
                it.get("line_no"), i, it.get("depth", 0),
                1 if it.get("is_category_header") else 0,
                it.get("category"),
                it.get("path") or it.get("parent_path", ""),
                it.get("name_raw"), it.get("name_normalized"),
                it.get("spec"),
                it.get("quantity"), it.get("unit"),
                it.get("unit_price"), it.get("unit_price_orig"),
                it.get("unit_price_currency_in_source", "KRW"),
                it.get("amount"),
            ))


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
          '인건비': [...],
          ...
      },
      'subtotals': {'A업체': 2289400000, ...},
      'category_totals': {'A업체': {'자재': ..., '인건비': ...}, ...}
    }
    """
    with get_conn() as c:
        # 제출된 업체 목록 (완료된 것만)
        vendors_rows = c.execute("""
            SELECT submission_id, vendor_name, subtotal_excl_vat
            FROM submissions
            WHERE bid_id = ? AND extraction_status = 'done'
            ORDER BY subtotal_excl_vat NULLS LAST
        """, (bid_id,)).fetchall()

        if not vendors_rows:
            return {"vendors": [], "categories": {}, "subtotals": {}}

        vendors = [r["vendor_name"] for r in vendors_rows]
        sub_map = {r["vendor_name"]: r["submission_id"] for r in vendors_rows}
        subtotals = {r["vendor_name"]: r["subtotal_excl_vat"] for r in vendors_rows}

        # 모든 라인 아이템 수집
        all_items = c.execute("""
            SELECT i.*, s.vendor_name
            FROM submission_items i
            JOIN submissions s USING (submission_id)
            WHERE s.bid_id = ? AND i.is_header = 0
            ORDER BY i.category, i.name_normalized, i.sort_order
        """, (bid_id,)).fetchall()

        # 카테고리별, 품목별 피벗
        cat_order = ["자재", "인건비", "출장비", "영업이익", "관리비"]
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
            ORDER BY b.due_date DESC, s.vendor_name
        """, (project_id, f"%{name_query}%", f"%{name_query}%")).fetchall()


def cross_project_price(name_query):
    """
    전체 프로젝트에 걸쳐 품목 가격 이력 조회 (참조자료 활용).
    Phase 2에서는 catalog 기반으로 교체됨.
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
            WHERE i.is_header = 0
              AND (i.name_normalized LIKE ? OR i.name_raw LIKE ?)
              AND s.extraction_status = 'done'
            ORDER BY b.due_date DESC, s.vendor_name
        """, (f"%{name_query}%", f"%{name_query}%")).fetchall()


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
                            model: str = None, plain_key: str = None):
    """사용자의 LLM provider + model + API 키를 저장"""
    from auth.crypto import encrypt_api_key
    updates = {
        "llm_provider": provider,
        "llm_model":    model or None,
        "updated_at":   datetime.now().isoformat(),
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
            SELECT llm_provider, llm_model, llm_api_key_enc
            FROM users WHERE user_id = ?
        """, (user_id,)).fetchone()
    if not row:
        return {"provider": "claude", "model": None, "api_key": ""}
    return {
        "provider": row[0] or "claude",
        "model":    row[1],
        "api_key":  decrypt_api_key(row[2]) if row[2] else "",
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
