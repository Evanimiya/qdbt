# -*- coding: utf-8 -*-
"""시나리오 9: 품명 레벨 버그 — [대,중,품명] 매핑에서 품명이 잎으로, '소분류'로 오분류 안 됨.

구조(추출/트리)는 정상이어야 하고, 연계 캔버스 열 헤더 라벨도 '품명'(≠소분류)이어야 한다.
표시 라벨 검증은 서버리스 node DOM 테스트(bug_pumyeong_leaflabel.js)로 수행.
"""
import io
import os
import json
import subprocess
import contextlib
import _util
from _util import build_wb, approx, Recorder, ROOT
from extractors.extract_by_mapping import extract_by_mapping
from extractors.stitch import stitch_sheets


def run():
    rec = Recorder("S9 품명레벨")

    # ── 9-1. extract_by_mapping [대,중,품명] → 품명은 잎, path는 분류만(placeholder 없음) ──
    p = build_wb("s9a.xlsx", [("S", [
        ["대분류", "중분류", "품명", "금액"],
        ["재료비", "기구부", "납블록", 1000],
        ["재료비", "전장부", "PLC", 2000],
    ], [])])
    r = extract_by_mapping(p, "S", {1: "cat1", 2: "cat2", 3: "name", 4: "amount"}, 1)
    it0 = r["items"][0]
    ok = (it0["path"] == "재료비 > 기구부" and it0["depth"] == 2
          and it0["name_normalized"] == "납블록"
          and not it0.get("is_level_residual")           # gap placeholder 미삽입
          and "⟨미연계" not in it0["path"])                # 소분류 자리 placeholder 없음
    rec.add("9-1", "[대,중,품명] extract 구조",
            "cat1+cat2+name 매핑(소분류 없음)",
            "path=재료비>기구부(depth2), 품명=납블록 잎, placeholder 없음",
            f"path={it0['path']} depth={it0['depth']} name={it0['name_normalized']} resid={it0.get('is_level_residual')}",
            "PASS" if ok else "FAIL",
            "" if ok else "품명이 분류경로(소분류)로 들어감")

    # ── 9-2. stitch_sheets [대,중,품명] → 동일(품명 잎, 경로는 분류만) ──
    rs = stitch_sheets(p, [{"sheet": "S", "mapping": {1: "cat1", 2: "cat2", 3: "name", 4: "amount"}, "header_row": 1}])
    s0 = rs["items"][0]
    ok = (s0["path"] == "재료비 > 기구부" and s0["name_normalized"] == "납블록"
          and "⟨미연계" not in s0["path"])
    rec.add("9-2", "[대,중,품명] stitch 구조",
            "스티칭 경로도 품명을 잎으로",
            "path=재료비>기구부, 품명=납블록 잎",
            f"path={s0['path']} name={s0['name_normalized']}",
            "PASS" if ok else "FAIL",
            "" if ok else "스티칭이 품명을 분류로 배치")

    # ── 9-3. build_items_tree: 품명이 is_leaf=True (구조상 잎) ──
    tmp = os.path.join(_util.FIX_DIR, "s9.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    with contextlib.redirect_stdout(io.StringIO()):
        import db.schema as schema, db.queries as q
        q.DB_PATH = tmp; schema.DB_PATH = tmp
        schema.init_db(tmp, reset=True); schema.migrate_db(tmp)
        pid = q.create_project("P"); bid = q.create_bid(pid, "B")
        sid = q.create_submission(bid, "X", "s9.xlsx", "/t", "xlsx")
        q.insert_items_bulk(sid, rs["items"]); q.update_submission(sid, extraction_status="done")
        q.recompute_subtotal(sid)
        tree = q.build_items_tree(sid)

    def find_leaf(nodes, name):
        for n in nodes:
            if n["name"] == name:
                return n
            f = find_leaf(n["children"], name)
            if f:
                return f
        return None
    nb = find_leaf(tree["tree"], "납블록")
    ok = (nb is not None and nb["is_leaf"] is True and nb["depth"] == 3
          and nb["path"] == "재료비 > 기구부 > 납블록")
    rec.add("9-3", "build_items_tree 품명 잎 판정",
            "트리에서 품명 노드",
            "납블록 is_leaf=True (재료비>기구부 아래 잎)",
            f"납블록: leaf={nb['is_leaf'] if nb else None} depth={nb['depth'] if nb else None} path={nb['path'] if nb else None}",
            "PASS" if ok else "FAIL",
            "" if ok else "품명이 잎으로 인식 안 됨")

    # ── 9-4. 연계 캔버스 열 헤더 라벨(서버리스 node DOM) — 품명 열이 '품명'(≠소분류) ──
    js = ROOT / "tests" / "rob" / "bug_pumyeong_leaflabel.js"
    try:
        pr = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=60)
        out = (pr.stdout or "") + (pr.stderr or "")
        ok = pr.returncode == 0 and "PASS" in out
    except Exception as e:
        out = f"node 실행 실패: {e}"; ok = False
    last = out.strip().splitlines()[-1] if out.strip() else "(출력 없음)"
    rec.add("9-4", "연계 캔버스 열 헤더 라벨(node DOM)",
            "[대,중,품명]→대\\|중\\|품명, [대,중,소,세,품명]→회귀 없음",
            "품명 잎 열이 '품명'으로 라벨(소분류 아님)",
            last,
            "PASS" if ok else "FAIL",
            "" if ok else "연계 캔버스 열 라벨이 품명을 소분류로 표기")

    return rec.flush()


if __name__ == "__main__":
    run()
