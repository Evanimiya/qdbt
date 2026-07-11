# -*- coding: utf-8 -*-
"""시나리오 10: 실파일 C3_v2_real.xlsx — 품목이 '소분류'로 오분류 (사용자 제보).

구조(공종목록=대분류 정수번호, 산출내역=1.1.1 중-소 결합명, 세부산출=1.1.1.1 품목 잎).
버그: (A 구조) 공종목록이 금액 탓 role='leaf'→seqmap 제외→대분류(공종) 소실.
      (B 표시) 품목 잎 열이 depth로만 라벨→'소분류'.
기대: 대분류(공종) > 중분류(산출) > 품명(품목 잎). 품목은 잎, 열 라벨은 '품명'.
"""
import io
import os
import json
import subprocess
import contextlib
import _util
from _util import approx, Recorder, ROOT

FIX = ROOT / "tests" / "rob" / "fixtures" / "C3_v2_real.xlsx"


def run():
    rec = Recorder("S10 실파일품명")
    from extractors.stitch import stitch_workbook

    if not FIX.exists():
        rec.add("10-0", "픽스처 존재", "C3_v2_real.xlsx", "없음", "FAIL",
                "tests/rob/fixtures/C3_v2_real.xlsx 필요")
        return rec.flush()

    sres = stitch_workbook(str(FIX))
    leaves = [it for it in sres["items"] if not it.get("merge_status")]

    # ── 10-1. (A) 대분류(공종) 복원 + 품목 잎 + 총액/건수 ──
    tops = {it["path"].split(" > ")[0] for it in leaves}
    gpu = next((it for it in leaves if it["name_normalized"] == "GPU Server"), None)
    ok = (gpu is not None
          and gpu["path"] == "컴퓨팅 인프라 > GPU 컴퓨팅 - GPU 서버 노드 > GPU Server"
          and "컴퓨팅 인프라" in tops                 # 대분류(공종) 복원
          and approx(sres["totals"]["leaf_sum"], 1611071000)
          and sres["n_items"] == 13)
    rec.add("10-1", "대분류(공종) 복원 + 품목 잎 [구조 A]",
            "공종목록(정수번호 대분류)+산출(1.1.1)+세부(1.1.1.1 품목)",
            "GPU Server 잎, 경로=컴퓨팅 인프라>GPU 컴퓨팅...>GPU Server, 총액 16.1억, 13건",
            f"top={sorted(tops)[:2]}... GPU경로={gpu['path'] if gpu else None} 총액={sres['totals']['leaf_sum']} n={sres['n_items']}",
            "PASS" if ok else "FAIL",
            "" if ok else "대분류 소실 or 품목 비잎")

    # ── 10-2. build_items_tree: 품목 GPU Server = is_leaf=True (구조상 잎) ──
    tmp = os.path.join(_util.FIX_DIR, "s10.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    tree_json = os.path.join(_util.FIX_DIR, "s10_tree.json")
    with contextlib.redirect_stdout(io.StringIO()):
        import db.schema as schema, db.queries as q
        q.DB_PATH = tmp; schema.DB_PATH = tmp
        schema.init_db(tmp, reset=True); schema.migrate_db(tmp)
        pid = q.create_project("P"); bid = q.create_bid(pid, "B")
        sid = q.create_submission(bid, "X", "c.xlsx", "/t", "xlsx")
        q.insert_items_bulk(sid, sres["items"]); q.update_submission(sid, extraction_status="done")
        q.recompute_subtotal(sid)
        tree = q.build_items_tree(sid)
    json.dump(tree["tree"], open(tree_json, "w"), ensure_ascii=False)

    def find(nodes, name):
        for n in nodes:
            if n["name"] == name:
                return n
            f = find(n["children"], name)
            if f:
                return f
        return None
    gv = find(tree["tree"], "GPU Server")
    ok = gv is not None and gv["is_leaf"] is True and gv["depth"] == 3
    rec.add("10-2", "build_items_tree 품목 잎 판정",
            "트리에서 GPU Server 노드",
            "GPU Server is_leaf=True (depth3, 대>중 아래 잎)",
            f"GPU Server: leaf={gv['is_leaf'] if gv else None} depth={gv['depth'] if gv else None}",
            "PASS" if ok else "FAIL",
            "" if ok else "품목이 잎으로 인식 안 됨")

    # ── 10-3. (B) 연계 캔버스 열 헤더: 품목 열이 '품명'(≠소분류) — 실제 LKC 렌더 ──
    labels = None
    try:
        js = ROOT / "tests" / "rob" / "canvas_labels.js"
        pr = subprocess.run(["node", str(js), tree_json], capture_output=True, text=True, timeout=60)
        labels = json.loads((pr.stdout or "[]").strip() or "[]")
    except Exception as e:
        labels = f"node 실패: {e}"
    ok = (isinstance(labels, list) and labels == ["대분류", "중분류", "품명"])
    rec.add("10-3", "연계 캔버스 열 헤더 라벨 [표시 B]",
            "실 LKC 렌더(서버리스 DOM)",
            "열 헤더 = 대분류 \\| 중분류 \\| 품명 (품목이 소분류 아님)",
            f"열 헤더={labels}",
            "PASS" if ok else "FAIL",
            "" if ok else "품목 열이 소분류로 오라벨")

    return rec.flush()


if __name__ == "__main__":
    run()
