# -*- coding: utf-8 -*-
"""시나리오 13: 부품(BOM) 이중 경제구조.

품목=집계 잎, 부품=품목 하위(레벨6)·is_header=1(총액 제외). 롤업: 단가=Σ파트금액,
금액=수량×단가, 총액=Σ품목금액(부품 미포함). 검증 게이트(check_bom) + 업체 비교 서브행.
"""
import io
import os
import subprocess
import contextlib
import _util
from _util import build_wb, leaf_total, approx, Recorder, ROOT
from extractors.extract_by_mapping import extract_by_mapping, suggest_column_mapping
from core.totals import check_bom

BOM_HDR = ["대분류", "품명", "수량", "단가", "금액", "부품", "부품수량", "부품단가", "파트금액"]
BOM_MP = {1: "cat1", 2: "name", 3: "qty", 4: "price", 5: "amount",
          6: "part", 7: "part_qty", 8: "part_price", 9: "part_amount"}


def run():
    rec = Recorder("S13 부품BOM")

    # ── 13-1. 추출: 품목 잎 + 부품(레벨6·bom_part) · 총액=Σ품목금액 ──
    p = build_wb("s13.xlsx", [("S", [
        BOM_HDR,
        ["재료비", "납블록", 2, 300, 600, None, None, None, None],
        [None, None, None, None, None, "순납강판", 3, 50, 150],
        [None, None, None, None, None, "볼트", 1, 150, 150],
        ["재료비", "볼트세트", 5, 100, 500, None, None, None, None],
    ], [])])
    r = extract_by_mapping(p, "S", BOM_MP, 1)
    items = r["items"]
    prods = [it for it in items if not it.get("bom_part")]
    parts = [it for it in items if it.get("bom_part")]
    part0 = parts[0] if parts else {}
    tot = leaf_total(items)   # bom_part 제외
    ok = (len(prods) == 2 and len(parts) == 2
          and part0.get("path") == "재료비 > 납블록" and part0.get("leaf_level") == 6
          and part0.get("part_amount") == 150
          and approx(tot, 1100))   # 600+500, 파트금액 미포함
    rec.add("13-1", "BOM 추출: 품목 잎 + 부품(레벨6) 총액 제외",
            "품목행(수량/단가/금액) + 부품행(부품수량/단가/파트금액)",
            "품목2·부품2, 부품 path=재료비>납블록·레벨6, 총액=1100(부품 미포함)",
            f"품목={len(prods)} 부품={len(parts)} 부품path={part0.get('path')} lvl={part0.get('leaf_level')} 총액={tot}",
            "PASS" if ok else "FAIL",
            "" if ok else "BOM 추출/총액 제외 오류")

    # ── 13-2. 자동감지: 부품 경제열이 qty/price/amount로 새지 않음 ──
    sug = suggest_column_mapping(p, "S")["mapping"]
    ok = (sug.get(3) == "qty" and sug.get(7) == "part_qty"
          and sug.get(8) == "part_price" and sug.get(9) == "part_amount")
    rec.add("13-2", "부품 경제열 자동감지",
            "부품수량/부품단가/파트금액 헤더",
            "part_qty/part_price/part_amount로 매핑(수량/단가/금액과 구분)",
            f"mapping={sug}",
            "PASS" if ok else "FAIL",
            "" if ok else "부품열이 품목 qty/price/amount로 오매핑")

    # ── 13-3. check_bom 게이트: 정상·원가불일치·per-unit/total 애매성 ──
    good = check_bom([{"name": "납블록", "quantity": 2, "unit_price": 300, "amount": 600,
                       "parts": [{"part_amount": 150}, {"part_amount": 150}]}])
    badcost = check_bom([{"name": "x", "quantity": 2, "unit_price": 300, "amount": 600,
                          "parts": [{"part_amount": 150}, {"part_amount": 130}]}])
    ambig = check_bom([{"name": "y", "quantity": 2, "unit_price": 300, "amount": 600,
                        "parts": [{"part_amount": 300}, {"part_amount": 300}]}])
    ok = (good.ok and (not badcost.ok) and "bom_cost" in [d.kind for d in badcost.diffs]
          and "bom_scale" in [d.kind for d in ambig.diffs])
    rec.add("13-3", "check_bom 검증 게이트",
            "정상 / Σ파트≠단가 / Σ파트≈금액(스케일 애매)",
            "정상 통과, 원가불일치=bom_cost, 애매=bom_scale",
            f"good={good.ok} bad={[d.kind for d in badcost.diffs]} ambig={[d.kind for d in ambig.diffs]}",
            "PASS" if ok else "FAIL",
            "" if ok else "check_bom 판정 오류")

    # ── 13-4. DB 왕복: 부품 is_header=1 → recompute 총액 제외 ──
    tmp = os.path.join(_util.FIX_DIR, "s13.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    with contextlib.redirect_stdout(io.StringIO()):
        import importlib
        import db.schema as schema
        import db.queries as q
        importlib.reload(schema); importlib.reload(q)
        q.DB_PATH = tmp; schema.DB_PATH = tmp
        schema.init_db(tmp, reset=True); schema.migrate_db(tmp)
        pid = q.create_project("P"); bid = q.create_bid(pid, "B")
        sid = q.create_submission(bid, "V", "c.xlsx", "/t", "xlsx")
        q.insert_items_bulk(sid, items); q.update_submission(sid, extraction_status="done")
        sub = q.recompute_subtotal(sid)
        headers = [dict(i) for i in q.get_items(sid, headers=True)]
    part_hdr = [h for h in headers if h.get("part_amount") is not None]
    ok = (approx(sub, 1100) and all(h["is_header"] == 1 for h in part_hdr) and len(part_hdr) == 2)
    rec.add("13-4", "DB 왕복: 부품 is_header=1·총액 제외",
            "insert → recompute_subtotal",
            "총액 1,100(부품 제외), 부품 2행 is_header=1",
            f"총액={sub} 부품행 is_header={[h['is_header'] for h in part_hdr]}",
            "PASS" if ok else "FAIL",
            "" if ok else "부품이 총액에 포함(이중계상)")

    # ── 13-5. 업체 비교 펼쳐보기 부품 서브행(node DOM) ──
    js = ROOT / "tests" / "rob" / "bom_expand_render.js"
    try:
        pr = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=60)
        out = (pr.stdout or "") + (pr.stderr or "")
        ok = pr.returncode == 0 and "PASS" in out
    except Exception as e:
        out = f"node 실패: {e}"; ok = False
    last = out.strip().splitlines()[-1] if out.strip() else "(출력 없음)"
    rec.add("13-5", "업체 비교 펼쳐보기 부품 서브행(node DOM)",
            "품목 -/+ 토글 → 부품 서브행·업체별 비교",
            "부품 서브행 2·토글·기본접힘·부품별 최저가",
            last,
            "PASS" if ok else "FAIL",
            "" if ok else "펼쳐보기 부품 렌더 오류")

    return rec.flush()


if __name__ == "__main__":
    run()
