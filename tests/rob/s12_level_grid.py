# -*- coding: utf-8 -*-
"""시나리오 12: 의미 레벨 열 그리드 (직접 붙임 빈 열 노출).

'직접 붙임' 항목의 잎이 품명 열에, 대분류가 대분류 열에 놓이고 중간 레벨 열이 빈 칸으로
보이는지(연결선이 가로지름) — build_items_tree의 node.level + 캔버스 렌더(서버리스 DOM)로 검증.
표시 전용·Δ=0. seq/band는 depth 폴백(회귀 없음).
"""
import io
import os
import json
import subprocess
import contextlib
import _util
from _util import build_wb, approx, Recorder, ROOT, FIX_DIR


def _tree_json(rows, mp, tag):
    tmp = os.path.join(FIX_DIR, f"s12_{tag}.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    out = os.path.join(FIX_DIR, f"s12_{tag}.json")
    with contextlib.redirect_stdout(io.StringIO()):
        import importlib
        import db.schema as schema
        import db.queries as q
        importlib.reload(schema); importlib.reload(q)
        q.DB_PATH = tmp; schema.DB_PATH = tmp
        schema.init_db(tmp, reset=True); schema.migrate_db(tmp)
        from extractors.stitch import stitch_sheets
        p = build_wb(f"s12_{tag}.xlsx", [("S", rows, [])])
        sres = stitch_sheets(p, [{"sheet": "S", "mapping": mp, "header_row": 1}])
        pid = q.create_project("P"); bid = q.create_bid(pid, "B")
        sid = q.create_submission(bid, "X", "c.xlsx", "/t", "xlsx")
        q.insert_items_bulk(sid, sres["items"]); q.update_submission(sid, extraction_status="done")
        total = q.recompute_subtotal(sid)
        tree = q.build_items_tree(sid)
    json.dump(tree["tree"], open(out, "w"), ensure_ascii=False)
    return out, total


def _grid(tree_json):
    js = ROOT / "tests" / "rob" / "canvas_grid.js"
    pr = subprocess.run(["node", str(js), tree_json], capture_output=True, text=True, timeout=60)
    return json.loads((pr.stdout or "[]").strip() or "[]")


def run():
    rec = Recorder("S12 의미레벨열")

    # ── 12-1. 순수 [대,품명] → 대분류·품명 열 + 중/소/세 빈 열 ──
    tj, tot = _tree_json([["대분류", "품명", "금액"], ["재료비", "납블록", 1000]],
                         {1: "cat1", 2: "name", 3: "amount"}, "pure")
    g = _grid(tj)
    bycol = {c["col"]: c for c in g}
    ok = (bycol.get(0, {}).get("header") == "대분류" and bycol[0]["nodes"] == ["재료비"]
          and bycol.get(4, {}).get("header") == "품명" and bycol[4]["nodes"] == ["납블록"]
          and bycol.get(1, {}).get("nodes") == [] and bycol.get(2, {}).get("nodes") == []
          and bycol.get(3, {}).get("nodes") == []
          and approx(tot, 1000))
    rec.add("12-1", "순수 [대,품명] → 빈 중간 열 노출",
            "[대,품명] 직접 붙임",
            "대분류=재료비, 품명=납블록, 중/소/세 열 빈 칸(연결선 가로지름), 총액1000",
            f"cols={[(c['col'],c['header'],c['nodes']) for c in g]} tot={tot}",
            "PASS" if ok else "FAIL",
            "" if ok else "의미레벨 열 배치/빈 열 미노출")

    # ── 12-2. 얕은·깊은 공존: 잎은 모두 품명 열, 깊은 분류는 각 열 ──
    tj, tot = _tree_json([
        ["대분류", "중분류", "소분류", "세분류", "품명", "금액"],
        ["재료비", None, None, None, "납블록", 1000],
        ["재료비", "기구부", "차폐부", "납계열", "볼트", 500],
    ], {1: "cat1", 2: "cat2", 3: "cat3", 4: "cat4", 5: "name", 6: "amount"}, "mix")
    g = _grid(tj)
    bycol = {c["col"]: c for c in g}
    ok = (bycol.get(0, {}).get("nodes") == ["재료비"]
          and bycol.get(1, {}).get("nodes") == ["기구부"]
          and bycol.get(2, {}).get("nodes") == ["차폐부"]
          and bycol.get(3, {}).get("nodes") == ["납계열"]
          and set(bycol.get(4, {}).get("nodes") or []) == {"납블록", "볼트"}
          and approx(tot, 1500))
    rec.add("12-2", "얕은·깊은 공존 → 잎 모두 품명 열",
            "[대,품명] + [대,중,소,세,품명] 혼재",
            "잎(납블록·볼트) 모두 품명 열, 깊은 분류는 각 레벨 열, 총액1500",
            f"cols={[(c['col'],c['nodes']) for c in g]} tot={tot}",
            "PASS" if ok else "FAIL",
            "" if ok else "잎 품명 열 정렬/분류 열 배치 오류")

    # ── 12-3. seq 폴백(회귀 없음): 실파일 C3 — 대/중/품명 3열(빈 열 안 생김) ──
    from extractors.stitch import stitch_workbook
    fix = ROOT / "tests" / "rob" / "fixtures" / "C3_v2_real.xlsx"
    ok = True
    detail = "픽스처 없음"
    if fix.exists():
        tmp = os.path.join(FIX_DIR, "s12_c3.db")
        if os.path.exists(tmp):
            os.remove(tmp)
        out = os.path.join(FIX_DIR, "s12_c3.json")
        with contextlib.redirect_stdout(io.StringIO()):
            import importlib
            import db.schema as schema
            import db.queries as q
            importlib.reload(schema); importlib.reload(q)
            q.DB_PATH = tmp; schema.DB_PATH = tmp
            schema.init_db(tmp, reset=True); schema.migrate_db(tmp)
            sres = stitch_workbook(str(fix))
            pid = q.create_project("P"); bid = q.create_bid(pid, "B")
            sid = q.create_submission(bid, "X", "c.xlsx", "/t", "xlsx")
            q.insert_items_bulk(sid, sres["items"]); q.update_submission(sid, extraction_status="done")
            q.recompute_subtotal(sid)
            json.dump(q.build_items_tree(sid)["tree"], open(out, "w"), ensure_ascii=False)
        g = _grid(out)
        headers = [c["header"] for c in g]
        # seq → depth 폴백: 3열(대분류|중분류|품명), 빈 세분류 등 안 생김.
        ok = headers == ["대분류", "중분류", "품명"]
        detail = f"headers={headers}"
    rec.add("12-3", "seq 폴백 회귀 없음(C3 실파일)",
            "seq 모드는 depth 폴백 → 기존과 동일 열",
            "대분류 | 중분류 | 품명 (빈 열 안 생김)",
            detail,
            "PASS" if ok else "FAIL",
            "" if ok else "seq 폴백 회귀(열 구성 바뀜)")

    return rec.flush()


if __name__ == "__main__":
    run()
