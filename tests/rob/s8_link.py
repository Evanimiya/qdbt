# -*- coding: utf-8 -*-
"""시나리오 8: 스티칭 자체 3단계 항목 연결 (코드 완전합치 → LLM 유사 제안 → 사람 확정).

시트 간 유사(비완전합치) 항목이 자동 병합/종결되지 않고, 코드 후보 → LLM(또는 휴리스틱)
연결 '제안' → 사람 확정으로 흐르는지, 폐쇄망 폴백·총액 Δ=0을 함수 직접 호출로 검증.
"""
import io
import os
import json
import contextlib
import _util
from _util import build_wb, leaf_total, approx, Recorder
from extractors.stitch import stitch_sheets
from extractors.stitch_link import suggest_links


class _StubSame:
    def __init__(self, same=True, conf=0.95):
        self.same, self.conf = same, conf
    def extract(self, **kw):
        return json.dumps({"links": [{"group_id": "g1", "same": self.same,
                                      "member_ids": ["g1m1", "g1m2"],
                                      "representative_id": "g1m1",
                                      "confidence": self.conf, "reason": "stub"}]})


def _cands_from(res):
    """stitch 결과의 link_candidates에 가짜 item_id 부여(라우트의 복원 모사)."""
    out = []
    for g in res["link_candidates"]:
        ms = [{**m, "item_id": f"{i}"} for i, m in enumerate(g["members"])]
        out.append({**g, "members": ms})
    return out


def run():
    rec = Recorder("S8 항목연결3단계")
    mp = {1: "cat1", 2: "name", 3: "spec", 4: "qty", 5: "price", 6: "amount"}

    def wb_pair(pathA, pathB, specB="6203ZZ", amtB=5000):
        return build_wb("s8.xlsx", [
            ("자재A", [["대분류", "품명", "규격", "수량", "단가", "금액"],
                       [pathA, "베어링", "6203ZZ", 1, 5000, 5000]], []),
            ("자재B", [["대분류", "품명", "규격", "수량", "단가", "금액"],
                       [pathB, "베어링", specB, 1, amtB, amtB]], []),
        ])
    specs = [{"sheet": "자재A", "mapping": mp, "header_row": 1},
             {"sheet": "자재B", "mapping": mp, "header_row": 1}]

    # ── 8-1. 1단계: 완전합치=코드 dedup / 유사(다른경로)=후보(자동병합 아님), Δ=0 ──
    res_same = stitch_sheets(wb_pair("구매자재", "구매자재"), specs)   # 같은 경로 → dedup
    res_sim = stitch_sheets(wb_pair("구매자재", "수입자재"), specs)    # 다른 경로 → 후보
    ok = (res_same["reconciliation"]["duplicates"] == 1
          and res_same["n_link_candidates"] == 0
          and res_sim["n_link_candidates"] == 1
          and res_sim["reconciliation"]["duplicates"] == 0
          and approx(leaf_total(res_sim["items"]), 10000))
    rec.add("8-1", "1단계 완전합치=dedup / 유사=후보(비파괴)",
            "같은 경로 동일항목 vs 다른 경로 유사항목",
            "완전동일 dedup·후보0 / 유사 후보1·자동병합0·총액10000(Δ=0)",
            f"완전동일:dup={res_same['reconciliation']['duplicates']},후보={res_same['n_link_candidates']} | "
            f"유사:후보={res_sim['n_link_candidates']},dup={res_sim['reconciliation']['duplicates']},총액={leaf_total(res_sim['items'])}",
            "PASS" if ok else "FAIL",
            "" if ok else "1단계 코드 분리 오류")

    # ── 8-2. 2단계 LLM 제안: same=true→proposed / same=false→미제안 ──
    cands = _cands_from(res_sim)
    r_true = suggest_links(cands, provider=_StubSame(True, 0.95))
    r_false = suggest_links(cands, provider=_StubSame(False))
    ok = (r_true["method"] == "llm" and len(r_true["proposals"]) == 1
          and r_true["proposals"][0]["status"] == "proposed"
          and len(r_false["proposals"]) == 0)
    rec.add("8-2", "2단계 LLM 유사 제안(same=true/false)",
            "유사 후보를 LLM이 동일 라인아이템인지 판정",
            "same=true→proposed 1건, same=false→제안 0(자동병합 없음)",
            f"true:{r_true['method']}/{len(r_true['proposals'])}/{r_true['proposals'][0]['status'] if r_true['proposals'] else '-'} | false:{len(r_false['proposals'])}",
            "PASS" if ok else "FAIL",
            "" if ok else "LLM 제안 경로 오류")

    # ── 8-3. 폐쇄망/무LLM 휴리스틱 폴백: 규격일치→proposed / 규격불명→pending ──
    r_heur = suggest_links(cands)   # api_key·provider 없음 → 휴리스틱
    res_nospec = stitch_sheets(wb_pair("구매자재", "수입자재", specB=""), specs)
    # 규격 한쪽 공란이면 그룹 규격 불일치 → 낮은 확신 → pending
    r_pending = suggest_links(_cands_from(res_nospec))
    ok = (r_heur["method"] == "heuristic"
          and r_heur["proposals"][0]["status"] == "proposed"
          and r_pending["proposals"][0]["status"] == "pending")
    rec.add("8-3", "폐쇄망 휴리스틱 폴백(proposed/pending)",
            "LLM 미가용 → 결정적 휴리스틱, 애매하면 pending",
            "규격·금액 일치→proposed, 규격 불명→pending(사람 확인 대기)",
            f"규격일치:{r_heur['proposals'][0]['status']}(score {r_heur['proposals'][0]['score']}) | 규격불명:{r_pending['proposals'][0]['status']}",
            "PASS" if ok else "FAIL",
            "" if ok else "휴리스틱 폴백/pending 오류")

    # ── 8-4. 3단계 사람 확정(DB): accept=Δ=0 / exclude→변동→restore 원복 ──
    tmp = os.path.join(_util.FIX_DIR, "s8_confirm.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    with contextlib.redirect_stdout(io.StringIO()):
        import db.schema as schema, db.queries as q
        q.DB_PATH = tmp; schema.DB_PATH = tmp
        schema.init_db(tmp, reset=True); schema.migrate_db(tmp)
        from web.blueprints.submissions import (_link_suggestions_for, _link_confirm,
                                                _build_link_view)
        sres = stitch_sheets(wb_pair("구매자재", "수입자재"), specs)
        uid = q.create_user("m@s8.com", "M", "manager")
        pid = q.create_project("P"); bid = q.create_bid(pid, "B")
        sid = q.create_submission(bid, "X", "s8.xlsx", "/t", "xlsx")
        q.insert_items_bulk(sid, sres["items"])
        q.update_submission(sid, extraction_status="done",
                            map_config=json.dumps({"stitch": {"link_candidates": sres["link_candidates"]}}))
        tot0 = q.recompute_subtotal(sid)
        _link_suggestions_for(sid, llm={})
        lv = _build_link_view(dict(q.get_submission(sid)), [dict(i) for i in q.get_items(sid)])
        gid = lv[0]["group_id"]; mid = lv[0]["members"][0]["item_id"]
        _link_confirm(sid, {"group_id": gid, "action": "accept"})
        t_accept = q.recompute_subtotal(sid)
        r_ex = _link_confirm(sid, {"group_id": gid, "action": "exclude_member", "item_id": mid})
        r_re = _link_confirm(sid, {"group_id": gid, "action": "restore_member", "item_id": mid})
    ok = (tot0 == 10000 and t_accept == 10000            # accept 후 Δ=0
          and r_ex["total"] == 5000 and r_ex["changed_total"]  # 명시적 제외 → 변동
          and r_re["total"] == 10000)                    # 복원 → 원복
    rec.add("8-4", "3단계 사람 확정(accept Δ=0 / 명시 제외 가역)",
            "제안 accept / 사람 명시 중복 제외·복원",
            "accept 총액 불변(10000), 제외 5000·복원 10000(되돌리기 가능)",
            f"tot0={tot0} accept={t_accept} exclude={r_ex['total']} restore={r_re['total']}",
            "PASS" if ok else "FAIL",
            "" if ok else "사람 확정/제외 동작 오류")

    return rec.flush()


if __name__ == "__main__":
    run()
