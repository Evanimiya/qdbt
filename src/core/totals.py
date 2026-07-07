"""합계 검증 공통 모듈 (T5).

견적 데이터의 4종 합계 불변식을 단일 창구로 검증한다. 순수 함수(DB 비의존)이므로
추출 결과를 삽입 전에도, 저장된 항목을 조회 후에도 동일하게 검증할 수 있다.

4종 불변식:
  ① 공급가액 = 트리 합 = 라인 합   (헤더 제외 잎 amount 합)
  ② Σ잎 == 묶음 금액              (묶음의 자식 잎 합 = 묶음 표기액)
  ③ Σ배치 == 노드 소계           (다중시트·N레벨: 하위 배치 합 = 상위 노드 소계)
  ④ amount == 단가 × 수량         (라인 내부 정합)

설계:
  · 각 불변식을 개별 함수로(필요한 것만 호출 가능).
  · check_all()이 통합 진입점(mode로 선택).
  · 허용 오차(tol)로 반올림·부가세 흡수. 기본 1원(정수 반올림 차이).
  · 반환은 Result(ok, diffs) — 불일치 상세(어디서 얼마 차이)를 담는다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


# ─── 결과 표현 ───────────────────────────────

@dataclass
class Diff:
    """불일치 1건."""
    kind: str          # 불변식 종류 (supply/bundle/node/line)
    where: str         # 위치 식별(노드명·item_id·path)
    expected: float    # 기대값
    actual: float      # 실제값
    delta: float       # 차액(actual - expected)


@dataclass
class Result:
    """검증 결과. ok=True면 전 불변식 정합."""
    ok: bool = True
    diffs: list = field(default_factory=list)

    def add(self, kind, where, expected, actual):
        d = round((actual or 0) - (expected or 0), 4)
        # 허용 오차 판정은 호출부에서 tol 적용 후 add하므로 여기선 기록만
        self.diffs.append(Diff(kind, where, expected, actual, d))
        self.ok = False
        return self

    def merge(self, other: "Result"):
        if not other.ok:
            self.ok = False
            self.diffs.extend(other.diffs)
        return self

    def summary(self) -> str:
        if self.ok:
            return "정합 (전 불변식 통과)"
        lines = [f"{len(self.diffs)}건 불일치:"]
        for d in self.diffs[:20]:
            lines.append(f"  · [{d.kind}] {d.where}: 기대 {d.expected:,.2f} vs 실제 {d.actual:,.2f} (차액 {d.delta:+,.2f})")
        return "\n".join(lines)


# ─── 유틸 ────────────────────────────────────

def _num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _get(item, key, default=None):
    """dict / sqlite3.Row 양쪽 지원 조회."""
    if isinstance(item, dict):
        return item.get(key, default)
    try:
        return item[key]
    except (KeyError, IndexError):
        return default


# ─── ④ 라인 정합: amount == 단가 × 수량 ───────

def check_line_amounts(items, tol: float = 1.0) -> Result:
    """각 라인의 amount == round(unit_price × quantity) 검증.
    단가·수량이 모두 있는 잎 라인만 대상(묶음·소계·헤더 제외)."""
    r = Result()
    for it in items:
        if _get(it, "is_header"):
            continue
        up, qty = _get(it, "unit_price"), _get(it, "quantity")
        if up is None or qty is None:
            continue  # 단가·수량 없는 행(묶음 등)은 이 불변식 대상 아님
        amt = _num(_get(it, "amount"))
        expected = round(_num(up) * _num(qty))
        if abs(amt - expected) > tol:
            r.add("line", str(_get(it, "item_id") or _get(it, "name_normalized") or "?"),
                  expected, amt)
    return r


# ─── ① 공급가액 = 트리 = 라인 ────────────────

def check_supply_total(items, declared_total: float, tol: float = 1.0) -> Result:
    """헤더(소계) 제외 잎 amount 합 == 선언된 공급가액."""
    r = Result()
    leaf_sum = sum(_num(_get(it, "amount")) for it in items if not _get(it, "is_header"))
    if abs(leaf_sum - _num(declared_total)) > tol:
        r.add("supply", "공급가액", _num(declared_total), leaf_sum)
    return r


# ─── ② Σ잎 == 묶음 금액 ──────────────────────

def check_bundle_sums(bundles, tol: float = 1.0) -> Result:
    """각 묶음의 자식 잎 합 == 묶음 표기액.
    bundles: [{'name','amount','leaves':[{'amount'},...]}, ...]"""
    r = Result()
    for b in bundles:
        declared = _num(_get(b, "amount"))
        leaves = _get(b, "leaves") or []
        leaf_sum = sum(_num(_get(l, "amount")) for l in leaves)
        if abs(leaf_sum - declared) > tol:
            r.add("bundle", str(_get(b, "name") or _get(b, "path") or "?"),
                  declared, leaf_sum)
    return r


# ─── ③ Σ배치 == 노드 소계 (다중시트·N레벨) ────

def check_node_subtotals(nodes, tol: float = 1.0) -> Result:
    """각 상위 노드의 소계 == 그에 배치된 하위 항목 합.
    nodes: [{'name','subtotal','children':[{'amount'},...]}, ...]
    N레벨 연계에서 부모 노드 소계와 자식 배치 합의 정합 검증."""
    r = Result()
    for n in nodes:
        declared = _num(_get(n, "subtotal"))
        children = _get(n, "children") or []
        child_sum = sum(_num(_get(c, "amount", _get(c, "subtotal"))) for c in children)
        if abs(child_sum - declared) > tol:
            r.add("node", str(_get(n, "name") or _get(n, "path") or "?"),
                  declared, child_sum)
    return r


# ─── 통합 진입점 ─────────────────────────────

def check_all(items=None, declared_total=None, bundles=None, nodes=None,
              modes=("line", "supply"), tol: float = 1.0) -> Result:
    """선택된 불변식을 한 번에 검증.

    modes: 검증할 불변식 집합 — 'line'(④), 'supply'(①), 'bundle'(②), 'node'(③).
    각 mode에 필요한 인자가 주어졌을 때만 해당 검증 수행.
    """
    r = Result()
    if "line" in modes and items is not None:
        r.merge(check_line_amounts(items, tol))
    if "supply" in modes and items is not None and declared_total is not None:
        r.merge(check_supply_total(items, declared_total, tol))
    if "bundle" in modes and bundles is not None:
        r.merge(check_bundle_sums(bundles, tol))
    if "node" in modes and nodes is not None:
        r.merge(check_node_subtotals(nodes, tol))
    return r
