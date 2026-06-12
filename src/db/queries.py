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
                   b.project_id
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

def delete_submission_items(submission_id: str):
    """제출서의 모든 라인 아이템 삭제 (재추출 전 호출)"""
    with get_conn() as c:
        c.execute("DELETE FROM submission_items WHERE submission_id = ?", (submission_id,))


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
            SELECT submission_id, vendor_name, subtotal_excl_vat
            FROM submissions
            WHERE bid_id = ? AND extraction_status = 'done'
            ORDER BY subtotal_excl_vat NULLS LAST
        """, (bid_id,)).fetchall()

        if not vendors_rows:
            return {"vendors": [], "categories": {}, "subtotals": {},
                    "clusters": []}

        vendors = [r["vendor_name"] for r in vendors_rows]
        subtotals = {r["vendor_name"]: r["subtotal_excl_vat"] for r in vendors_rows}

        all_items = c.execute("""
            SELECT i.*, s.vendor_name
            FROM submission_items i
            JOIN submissions s USING (submission_id)
            WHERE s.bid_id = ? AND i.is_header = 0
            ORDER BY i.category, i.name_normalized, i.sort_order
        """, (bid_id,)).fetchall()

        # 확정된 클러스터 조회
        # accepted + pending 클러스터 모두 표시
        bid_clusters = c.execute("""
            SELECT cl.cluster_id, cl.representative_name, cl.status
            FROM catalog_clusters cl
            WHERE cl.bid_id = ? AND cl.status IN ('accepted', 'pending', 'held')
            ORDER BY cl.status DESC, cl.created_at
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
                    })

            if not cluster_items:
                continue

            # 업체별 단가/품목명 맵 (안 A 렌더링용)
            vendor_map = {}  # vendor_name → {name_raw, unit_price, amount}
            for ci in cluster_items:
                vendor_map[ci["vendor_name"]] = {
                    "name_raw":   ci["name_raw"],
                    "unit_price": ci["unit_price"],
                    "amount":     ci["amount"],
                }

            # 최저/최고가 업체
            priced = [(ci["vendor_name"], ci["unit_price"])
                      for ci in cluster_items if ci["unit_price"]]
            min_vendor, min_price = (
                min(priced, key=lambda x: x[1]) if priced else (None, None)
            )
            max_vendor, max_price = (
                max(priced, key=lambda x: x[1]) if len(priced) > 1 else (None, None)
            )

            clusters_data.append({
                "cluster_id":          cl["cluster_id"],
                "representative_name": cl["representative_name"],
                "status":              cl["status"],
                "members":             cluster_items,
                "vendor_map":          vendor_map,   # 업체별 단가/품목명
                "min_vendor":          min_vendor,
                "min_price":           min_price,
                "max_vendor":          max_vendor,
                "max_price":           max_price,
                "vendors":             vendors,
            })

        # 카테고리별 피벗 (클러스터 미포함 항목만)
        cat_order = ["자재", "인건비", "출장비", "영업이익", "관리비"]
        categories = {cat: {} for cat in cat_order}
        cat_totals = {v: {cat: 0 for cat in cat_order} for v in vendors}

        for it in all_items:
            if it["item_id"] in clustered_item_ids:
                continue  # 클러스터에 포함된 항목은 제외
            cat = it["category"] or "기타"
            vendor = it["vendor_name"]
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
            row["prices"][vendor]     = it["unit_price"]
            row["quantities"][vendor] = it["quantity"]
            row["amounts"][vendor]    = it["amount"]
            if cat in cat_totals[vendor]:
                cat_totals[vendor][cat] += (it["amount"] or 0)

        result_cats = {}
        for cat in cat_order:
            if categories.get(cat):
                result_cats[cat] = list(categories[cat].values())
        # 기타 카테고리
        for cat, items_dict in categories.items():
            if cat not in cat_order and items_dict:
                result_cats[cat] = list(items_dict.values())

        return {
            "vendors":         vendors,
            "categories":      result_cats,
            "subtotals":       subtotals,
            "category_totals": cat_totals,
            "clusters":        clusters_data,
        }
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

DOMAIN_LIST = ['IT', '설비', '용역', '기타']

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
    """클러스터링 대상 submission_items (카테고리 헤더 제외)"""
    with get_conn() as c:
        return c.execute("""
            SELECT si.item_id, si.name_raw, si.name_normalized,
                   si.spec, si.category, si.unit, si.quantity,
                   si.unit_price, si.amount,
                   s.vendor_name, s.submission_id
            FROM submission_items si
            JOIN submissions s USING (submission_id)
            WHERE s.bid_id = ?
              AND s.extraction_status = 'done'
              AND (si.is_header = 0 OR si.is_header IS NULL)
              AND si.name_raw IS NOT NULL
            ORDER BY s.vendor_name, si.sort_order
        """, (bid_id,)).fetchall()
