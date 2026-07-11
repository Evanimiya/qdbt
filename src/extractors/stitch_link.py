# -*- coding: utf-8 -*-
"""[항목연결 2단계] intra-submission 시트 간 유사 항목 LLM 연결 '제안'.

스티칭 1단계(코드 완전합치, `_dedup_and_rollup`)가 못 붙인 '유사 후보'
(`stitch._detect_link_candidates`: 정규화 품명 동일 + 다른 분류경로 + 다른 시트 + 금액 근접)를
LLM 유사도로 "서로 다른 시트에 중복 기재된 **동일 라인아이템**인가?"를 판정해 연결을 **제안**한다.

원칙(절대 준수):
  · 자동 병합 금지 — 여기서는 '제안'만 만든다. 병합/제외는 3단계(사람 확정)에서만.
  · 폐쇄망/무LLM: 결정적 **휴리스틱 폴백**으로 제안 생성 → 그래도 애매하면 `status='pending'`
    (사람 확인 대기). LLM 단계를 '건너뛰지' 않고 시도→실패 시 사람에게 넘긴다.
  · 정본 불변·총액 Δ=0 — 제안은 `map_config`(구조/표시)일 뿐, `submission_items` 미변경.

기존 자산 재사용: `extractors.providers.get_provider` + `provider.extract` + `core.llm_json.extract_json`
  + 짧은 id(gNmM) 매핑 트릭(클러스터러와 동일). **프롬프트만 intra-submission 전용**
  ('동종 품목' 그룹핑이 아니라 '동일 라인아이템' 확인이라 의미가 달라 전용 프롬프트 채택 —
  설계 결정 #1, docs/QDBT_스티칭_LLM유사연결_계획_20260711.md).
"""
import json
import re

# 이 미만 신뢰도의 휴리스틱 제안은 'pending'(사람 확인 대기)로만 남긴다(LLM 제안은 항상 노출).
HEUR_CONFIDENT = 0.8

LINK_PROMPT = (
    "당신은 하나의 견적서에서 '서로 다른 시트(분류)'에 중복 기재됐을 수 있는 "
    "'동일 라인아이템'을 판정하는 시스템입니다.\n\n"
    "## 작업\n각 그룹의 members(같은 견적서, 서로 다른 시트의 품목 후보)가 실제로 '같은 물리적 "
    "품목이 다른 시트/분류에 중복 기재된 것'인지 판정하세요.\n"
    "## 규칙\n"
    "1. 같은 물리적 품목이면 same=true, 단순히 비슷하지만 다른 품목이면 same=false.\n"
    "2. 반드시 '서로 다른 시트'의 항목만 연결(같은 시트 항목끼리는 연결 대상 아님).\n"
    "3. 품명·규격(spec)·금액을 함께 고려. 규격이 명백히 다르면 다른 품목일 수 있음.\n"
    "4. representative_id는 대표로 남길 member id.\n"
    "5. member id·group_id는 입력값을 그대로 사용.\n"
    "## 출력(순수 JSON만)\n"
    '{"links":[{"group_id":"g1","same":true,"member_ids":["g1m1","g1m2"],'
    '"representative_id":"g1m1","confidence":0.0,"reason":"..."}]}'
)


def _norm(s):
    return re.sub(r"\s+", "", str(s if s is not None else "")).lower()


def _prep_groups(candidate_groups):
    """후보 그룹 정규화: item_id 있는 멤버 2+ · 서로 다른 시트 2+ 만 남긴다."""
    groups = []
    for i, g in enumerate(candidate_groups or [], 1):
        ms = [m for m in (g.get("members") or []) if m.get("item_id")]
        if len(ms) < 2:
            continue
        if len({m.get("sheet") for m in ms}) < 2:
            continue
        groups.append({"group_id": f"g{i}", "key": g.get("key"),
                       "name": g.get("name"), "members": ms})
    return groups


def _heuristic_link(group):
    """LLM 미가용/실패 시 결정적 폴백. 후보는 이미 품명 동일이므로 규격·금액으로 확신 산정.
    반환: (score, summary)."""
    members = group["members"]
    specs = {_norm(m.get("spec")) for m in members}
    amts = [float(m.get("amount") or 0) for m in members]
    amax = max((abs(a) for a in amts), default=1.0) or 1.0
    amt_ok = (max(amts) - min(amts)) <= max(1.0, amax * 0.01)
    spec_ok = (len(specs) == 1 and next(iter(specs)) != "")
    if spec_ok and amt_ok:
        return 0.85, "품명·규격·금액 일치(휴리스틱)"
    if amt_ok:
        return 0.6, "품명·금액 일치, 규격 상이/불명 — 확인 필요(휴리스틱)"
    return 0.4, "품명만 일치 — 확인 필요(휴리스틱)"


def _llm_links(groups, api_key, provider_id, model, base_url, verify_ssl, provider):
    """LLM 유사 판정(그룹 배치 1콜). 반환: {group_id: {same, confidence, member_ids, representative_id, reason}}."""
    from core.llm_json import extract_json as _extract_json
    if provider is None:
        from extractors.providers import get_provider
        provider = get_provider(provider_id)
    idmap, payload = {}, []
    for g in groups:
        gm = []
        for k, m in enumerate(g["members"], 1):
            sid = f"{g['group_id']}m{k}"
            idmap[sid] = m["item_id"]
            gm.append({"id": sid, "sheet": m.get("sheet"), "path": m.get("path"),
                       "name": m.get("name"), "spec": m.get("spec"),
                       "amount": m.get("amount")})
        payload.append({"group_id": g["group_id"], "name": g["name"], "members": gm})
    raw = provider.extract(
        parsed_text=json.dumps(payload, ensure_ascii=False),
        system_prompt=LINK_PROMPT, api_key=api_key, model=model,
        base_url=base_url, verify_ssl=verify_ssl, temperature=0)
    data = _extract_json(raw)
    out = {}
    for L in (data.get("links") or []):
        gid = str(L.get("group_id", "")).strip()
        if not gid:
            continue
        mids = [idmap.get(str(x).strip()) for x in (L.get("member_ids") or [])]
        mids = [x for x in mids if x]
        rep = idmap.get(str(L.get("representative_id", "")).strip())
        try:
            conf = float(L.get("confidence"))
        except (TypeError, ValueError):
            conf = 0.7
        out[gid] = {"same": bool(L.get("same")), "confidence": conf,
                    "member_ids": mids,
                    "representative_id": rep or (mids[0] if mids else None),
                    "reason": (L.get("reason") or "").strip() or "LLM 유사 판정"}
    return out


def suggest_links(candidate_groups, api_key=None, provider_id="claude", model=None,
                  base_url=None, verify_ssl=True, provider=None):
    """유사 후보 → 연결 '제안' 생성(2단계). 자동 병합 없음.

    candidate_groups: [{key, name, members:[{item_id, sheet, path, name, spec, amount}...]}]
      (member.item_id 는 라우트가 DB 저장 후 line_no+path로 복원해 채운다.)
    반환: {"proposals": [...], "method": "llm"|"heuristic"|"heuristic (LLM 실패…)"|"none"}
      proposal: {group_id, name, member_ids, representative_item_id, score, summary,
                 method:'llm'|'heuristic', status:'proposed'|'pending'}
    """
    groups = _prep_groups(candidate_groups)
    if not groups:
        return {"proposals": [], "method": "none"}
    method = "heuristic"
    llm_res = None
    if api_key or provider is not None:
        try:
            llm_res = _llm_links(groups, api_key, provider_id, model, base_url,
                                 verify_ssl, provider)
            method = "llm"
        except Exception as e:
            method = f"heuristic (LLM 실패: {type(e).__name__})"
    proposals = []
    for g in groups:
        dec = (llm_res or {}).get(g["group_id"])
        if dec is not None:
            if not dec["same"]:
                continue   # LLM: 다른 품목 → 제안 안 함
            mids = dec["member_ids"] or [m["item_id"] for m in g["members"]]
            proposals.append({
                "group_id": g["group_id"], "name": g["name"], "member_ids": mids,
                "representative_item_id": dec["representative_id"] or mids[0],
                "score": round(dec["confidence"], 3), "summary": dec["reason"],
                "method": "llm", "status": "proposed"})
        else:
            score, summary = _heuristic_link(g)
            mids = [m["item_id"] for m in g["members"]]
            # 휴리스틱은 확신 높을 때만 '제안', 애매하면 'pending'(사람 확인 대기).
            status = "proposed" if score >= HEUR_CONFIDENT else "pending"
            proposals.append({
                "group_id": g["group_id"], "name": g["name"], "member_ids": mids,
                "representative_item_id": mids[0],
                "score": round(score, 3), "summary": summary,
                "method": "heuristic", "status": status})
    return {"proposals": proposals, "method": method}
