# -*- coding: utf-8 -*-
"""시나리오 11: 중간 레벨(중/소/세) 부재 = '직접 붙임' 표준 (규칙 ①~⑤).

_prev_val 상위값 복제 제거(가짜 노드 금지) + extract·stitch 경로 통일.
placeholder(⟨미연계⟩)는 ③(번호 암시 이름 누락)·④(형제 일부 누락)에만.
"""
import _util
from _util import build_wb, leaf_total, approx, Recorder
from extractors.extract_by_mapping import extract_by_mapping, _LEVEL_PLACEHOLDER
from extractors.stitch import stitch_sheets

PH = set(_LEVEL_PLACEHOLDER.values())


def _paths(items):
    return [(it["path"], it.get("name_normalized"), it.get("is_level_residual")) for it in items]


def run():
    rec = Recorder("S11 직접붙임")

    # ── 11-1. ① 미매핑 중간레벨 → 직접 ([대,품명]) ──
    p = build_wb("s11_1.xlsx", [("S", [
        ["대분류", "품명", "금액"], ["재료비", "납블록", 1000], ["노무비", "설치공", 2000],
    ], [])])
    r = extract_by_mapping(p, "S", {1: "cat1", 2: "name", 3: "amount"}, 1)
    ok = (all(it["depth"] == 1 for it in r["items"])
          and {it["path"] for it in r["items"]} == {"재료비", "노무비"}
          and approx(leaf_total(r["items"]), 3000))
    rec.add("11-1", "① 미매핑 → 대 > 품목 직접",
            "[대,품명] 매핑(중/소/세 없음)",
            "path=대분류(depth1), 품목은 잎, 총액3000",
            f"paths={[it['path'] for it in r['items']]} tot={leaf_total(r['items'])}",
            "PASS" if ok else "FAIL", "" if ok else "직접 붙임 안 됨")

    # ── 11-2. _prev_val 복제 제거 ([대,중,소,품명] 빈칸 → 재료비>재료비 금지) ──
    p = build_wb("s11_2.xlsx", [("S", [
        ["대분류", "중분류", "소분류", "품명", "금액"], ["재료비", None, None, "납블록", 1000],
    ], [])])
    r = extract_by_mapping(p, "S", {1: "cat1", 2: "cat2", 3: "cat3", 4: "name", 5: "amount"}, 1)
    it0 = r["items"][0]
    ok = (it0["path"] == "재료비" and it0["depth"] == 1
          and "재료비 > 재료비" not in it0["path"])
    rec.add("11-2", "_prev_val 상위값 복제 제거",
            "[대,중,소,품명] 중·소 빈칸",
            "path=재료비(직접) — 재료비>재료비>재료비 가짜 노드 없음",
            f"path={it0['path']!r} depth={it0['depth']}",
            "PASS" if ok else "FAIL", "" if ok else "대분류 이름 복제(가짜 노드) 잔존")

    # ── 11-3. ② fill-down 정당 상속 유지 ──
    p = build_wb("s11_3.xlsx", [("S", [
        ["대분류", "중분류", "품명", "금액"],
        ["재료비", "기구부", "볼트", 500], ["재료비", None, "너트", 300],
    ], [])])
    r = extract_by_mapping(p, "S", {1: "cat1", 2: "cat2", 3: "name", 4: "amount"}, 1)
    by = {it["name_normalized"]: it["path"] for it in r["items"]}
    ok = by.get("너트") == "재료비 > 기구부"    # 위 행에서 상속(정당)
    rec.add("11-3", "② fill-down 정당 상속 유지",
            "중분류 빈칸이 위 행(기구부) 상속",
            "너트=재료비 > 기구부(상속 유지)",
            f"너트={by.get('너트')!r}",
            "PASS" if ok else "FAIL", "" if ok else "fill-down 상속 깨짐")

    # ── 11-4. ④ 형제 일부 누락(누락행 먼저) → 미연계 ──
    p = build_wb("s11_4.xlsx", [("S", [
        ["대분류", "중분류", "품명", "금액"],
        ["재료비", None, "직접품목", 300], ["재료비", "기구부", "볼트", 500],
    ], [])])
    r = extract_by_mapping(p, "S", {1: "cat1", 2: "cat2", 3: "name", 4: "amount"}, 1)
    direct = next(it for it in r["items"] if it["name_normalized"] == "직접품목")
    ok = (any(x in PH for x in direct["path"].split(" > ")) and direct.get("is_level_residual") is True)
    rec.add("11-4", "④ 형제 일부 누락 → 미연계",
            "형제(볼트)는 중분류 있는데 직접품목만 빔(누락행 먼저)",
            "직접품목 path에 ⟨미연계·중분류⟩ + residual",
            f"직접품목 path={direct['path']!r} resid={direct.get('is_level_residual')}",
            "PASS" if ok else "FAIL", "" if ok else "형제 누락 미탐(미연계 안 됨)")

    # ── 11-5. ④ 형제 전부 빔 → 직접(미연계 아님) ──
    p = build_wb("s11_5.xlsx", [("S", [
        ["대분류", "중분류", "품명", "금액"],
        ["재료비", None, "품목A", 300], ["재료비", None, "품목B", 500],
    ], [])])
    r = extract_by_mapping(p, "S", {1: "cat1", 2: "cat2", 3: "name", 4: "amount"}, 1)
    ok = (all(it["path"] == "재료비" and not it.get("is_level_residual") for it in r["items"]))
    rec.add("11-5", "④ 형제 전부 빔 → 직접",
            "같은 부모 형제 모두 중분류 없음",
            "둘 다 재료비 직접(미연계 아님)",
            f"{_paths(r['items'])}",
            "PASS" if ok else "FAIL", "" if ok else "정상 빈 계층을 미연계로 오탐")

    # ── 11-6. extract·stitch 경로 통일(둘 다 직접) ──
    p = build_wb("s11_6.xlsx", [("S", [
        ["대분류", "중분류", "소분류", "품명", "금액"], ["재료비", None, None, "납블록", 1000],
    ], [])])
    mp = {1: "cat1", 2: "cat2", 3: "cat3", 4: "name", 5: "amount"}
    e = extract_by_mapping(p, "S", mp, 1)["items"][0]["path"]
    s = stitch_sheets(p, [{"sheet": "S", "mapping": mp, "header_row": 1}])["items"][0]["path"]
    ok = (e == s == "재료비")
    rec.add("11-6", "extract·stitch 경로 통일",
            "동일 입력([대,중,소,품명] 빈칸) 두 경로",
            "둘 다 '재료비' 직접(동일)",
            f"extract={e!r} stitch={s!r}",
            "PASS" if ok else "FAIL", "" if ok else "경로 간 동작 상충")

    return rec.flush()


if __name__ == "__main__":
    run()
