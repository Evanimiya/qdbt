import React, { useState } from 'react';

const VENDORS = ["A상사", "B기업", "C산업"];

// 공통 트리 구조. 잎에 업체별 금액. is_nego = 차감 항목(special nego)
const INITIAL_TREE = [
  {
    name: "재료비", children: [
      {
        name: "기구부", children: [
          { name: "차폐", children: [
            { name: "납 BLOCK", prices: { "A상사": 9600000, "B기업": 9200000, "C산업": 9800000 } },
            { name: "고정 샤프트", prices: { "A상사": 4800000, "B기업": 4600000, "C산업": 5000000 } },
          ]},
          { name: "프레임", children: [
            { name: "각관 Frame", prices: { "A상사": 6260000, "B기업": 6000000, "C산업": 6400000 } },
            { name: "상부 FENCE", prices: { "A상사": 3000000, "B기업": 3100000, "C산업": 2900000 } },
          ]},
        ]
      },
      {
        name: "전장/제어부", children: [
          { name: "전장부", children: [
            { name: "Servo Motor", prices: { "A상사": 1180000, "B기업": 1150000, "C산업": 1200000 } },
            { name: "ROBOT", prices: { "A상사": 4250000, "B기업": 4100000, "C산업": 4400000 } },
          ]},
        ]
      },
    ]
  },
  {
    name: "인건비", children: [
      { name: "설계 인건비", prices: { "A상사": 18000000, "B기업": 17500000, "C산업": 18500000 } },
      { name: "제작 인건비", prices: { "A상사": 22000000, "B기업": 21000000, "C산업": 23000000 } },
    ]
  },
  {
    name: "이윤 및 관리비", children: [
      { name: "관리비", prices: { "A상사": 30000000, "B기업": 31000000, "C산업": 29000000 } },
      // special nego: 차감 항목인데 부호가 +로 잘못 들어온 경우
      { name: "SPECIAL NEGO", prices: { "A상사": 5000000, "B기업": 4000000, "C산업": 6000000 }, is_nego: true, sign: 1 },
    ]
  },
];

const won = (n) => (n == null ? "—" : Math.round(n).toLocaleString('ko-KR'));

export default function App() {
  const [tree, setTree] = useState(INITIAL_TREE);
  const [open, setOpen] = useState({ "재료비": true });
  const [editing, setEditing] = useState(null); // 편집 중인 노드 path

  const toggle = (path) => setOpen((o) => ({ ...o, [path]: !o[path] }));

  // 노드 합산 (nego는 부호 반영)
  function sumPrices(node) {
    if (!node.children) {
      const sign = node.is_nego ? (node.sign ?? -1) : 1;
      const acc = {};
      VENDORS.forEach((v) => { if (node.prices[v] != null) acc[v] = node.prices[v] * sign; });
      return acc;
    }
    const acc = {};
    node.children.forEach((c) => {
      const cp = sumPrices(c);
      VENDORS.forEach((v) => { if (cp[v] != null) acc[v] = (acc[v] || 0) + cp[v]; });
    });
    return acc;
  }

  // 트리 수정 헬퍼 (path로 노드 찾아 변경)
  function updateNode(path, fn) {
    const newTree = JSON.parse(JSON.stringify(tree));
    function walk(nodes, prefix) {
      for (let node of nodes) {
        const p = prefix ? prefix + " > " + node.name : node.name;
        if (p === path) { fn(node); return true; }
        if (node.children && walk(node.children, p)) return true;
      }
      return false;
    }
    walk(newTree, "");
    setTree(newTree);
  }

  // 부호 토글 (special nego 차감)
  function toggleSign(path) {
    updateNode(path, (n) => { n.sign = (n.sign ?? -1) === -1 ? 1 : -1; });
  }
  // 품명 수정
  function renameNode(path, newName) {
    updateNode(path, (n) => { n.name = newName; });
    setEditing(null);
  }
  // nego 항목 추가 (이윤 및 관리비 아래에)
  function addNegoItem() {
    const newTree = JSON.parse(JSON.stringify(tree));
    const target = newTree.find((n) => n.name === "이윤 및 관리비");
    if (target) {
      target.children.push({
        name: "추가 NEGO", prices: { "A상사": 0, "B기업": 0, "C산업": 0 }, is_nego: true, sign: -1, _new: true,
      });
    }
    setTree(newTree);
    setOpen((o) => ({ ...o, "이윤 및 관리비": true }));
  }

  // 추가한 nego 항목 제외 (트리에서 제거)
  function removeNode(path) {
    const newTree = JSON.parse(JSON.stringify(tree));
    function walk(nodes, prefix) {
      for (let i = 0; i < nodes.length; i++) {
        const p = prefix ? prefix + " > " + nodes[i].name : nodes[i].name;
        if (p === path) { nodes.splice(i, 1); return true; }
        if (nodes[i].children && walk(nodes[i].children, p)) return true;
      }
      return false;
    }
    walk(newTree, "");
    setTree(newTree);
  }

  // 행 수집
  const rows = [];
  function walk(nodes, prefix, depth) {
    nodes.forEach((node) => {
      const path = prefix ? prefix + " > " + node.name : node.name;
      const isLeaf = !node.children;
      const isOpen = open[path];
      const isItem = isLeaf || !isOpen;
      rows.push({ node, path, depth, isLeaf, isOpen, isItem, prices: sumPrices(node) });
      if (!isLeaf && isOpen) walk(node.children, path, depth + 1);
    });
  }
  walk(tree, "", 0);

  const totals = {};
  VENDORS.forEach((v) => { totals[v] = rows.filter((r) => r.isItem).reduce((s, r) => s + (r.prices[v] || 0), 0); });
  const minTotal = Math.min(...VENDORS.map((v) => totals[v]));

  return (
    <div style={{ fontFamily: '-apple-system, "Malgun Gothic", sans-serif', background: '#f3f4f6', minHeight: '100vh', padding: 16 }}>
      <div style={{ maxWidth: 940, margin: '0 auto' }}>

        <div style={{ fontSize: 12, color: '#3b82f6', marginBottom: 8, cursor: 'pointer' }}>← 입찰 상세로</div>

        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
            <div>
              <h1 style={{ fontSize: 18, fontWeight: 700, color: '#111827', margin: 0 }}>서산3동 입찰 — 업체별 비교</h1>
              <p style={{ fontSize: 12, color: '#6b7280', margin: '4px 0 0' }}>추출 레벨을 가져왔습니다. +/− 로 조정 → <b style={{ color: '#3b82f6' }}>펼친 노드가 항목</b>으로 비교됩니다.</p>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button style={btnBlue}>🤖 클러스터 실행</button>
              <button style={btnBlue}>🔀 클러스터 관리</button>
            </div>
          </div>
        </div>

        {/* 기존 기능 바 — 유지 */}
        <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', padding: '10px 14px', marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 12 }}>
          <span style={{ color: '#64748b' }}>선택 항목:</span>
          <button style={btnSmall}>→ 클러스터로 이동</button>
          <button style={btnSmall}>⛓ 병합</button>
          <button style={btnSmall}>✕ 제외</button>
          <span style={{ color: '#cbd5e1' }}>|</span>
          <button style={btnSmallGreen} onClick={addNegoItem}>＋ NEGO 항목 추가</button>
          <span style={{ color: '#94a3b8', fontSize: 11, marginLeft: 'auto' }}>품명 클릭 = 수정 · ± 클릭 = 부호 변경</span>
        </div>

        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', overflow: 'hidden' }}>
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600, color: '#374151' }}>
                  <input type="checkbox" style={{ marginRight: 8 }} /> 항목 (분류 트리)
                </th>
                {VENDORS.map((v) => <th key={v} style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: '#374151', whiteSpace: 'nowrap' }}>{v}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const vals = VENDORS.map((v) => r.prices[v]).filter((x) => x != null);
                const minP = Math.min(...vals.map((x) => Math.abs(x) === x ? x : Infinity));
                const isNego = r.node.is_nego;
                return (
                  <tr key={i} style={{ borderBottom: '1px solid #f3f4f6', background: isNego ? '#fef2f2' : (r.isItem ? '#fff' : '#fafbfc') }}>
                    <td style={{ padding: '8px 12px', paddingLeft: 12 + r.depth * 20 }}>
                      <span style={{ display: 'flex', alignItems: 'center' }}>
                        {r.isItem && <input type="checkbox" style={{ marginRight: 8 }} />}
                        <span onClick={() => !r.isLeaf && toggle(r.path)} style={{
                          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18, marginRight: 6,
                          fontSize: 11, fontWeight: 700, border: r.isLeaf ? 'none' : '1px solid #cbd5e1', borderRadius: 4,
                          color: r.isLeaf ? 'transparent' : '#475569', cursor: r.isLeaf ? 'default' : 'pointer', background: r.isLeaf ? 'transparent' : '#fff',
                        }}>{r.isLeaf ? '·' : (r.isOpen ? '−' : '+')}</span>

                        {/* 품명 — 클릭하면 편집 */}
                        {editing === r.path ? (
                          <input autoFocus defaultValue={r.node.name}
                            onBlur={(e) => renameNode(r.path, e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') renameNode(r.path, e.target.value); }}
                            style={{ fontSize: 13, padding: '2px 6px', border: '1px solid #3b82f6', borderRadius: 4 }} />
                        ) : (
                          <span onClick={() => setEditing(r.path)} title="클릭하여 품명 수정"
                            style={{ fontWeight: r.isItem ? 600 : 400, color: isNego ? '#b91c1c' : (r.isItem ? '#1f2937' : '#9ca3af'), cursor: 'text', borderBottom: '1px dashed transparent' }}
                            onMouseEnter={(e) => e.currentTarget.style.borderBottomColor = '#cbd5e1'}
                            onMouseLeave={(e) => e.currentTarget.style.borderBottomColor = 'transparent'}>
                            {r.node.name}
                          </span>
                        )}

                        {/* nego 부호 토글 */}
                        {isNego && (
                          <button onClick={() => toggleSign(r.path)} title="부호 변경 (차감 ↔ 가산)"
                            style={{ marginLeft: 8, fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 999, border: '1px solid #f87171',
                              background: (r.node.sign ?? -1) === -1 ? '#dc2626' : '#fff', color: (r.node.sign ?? -1) === -1 ? '#fff' : '#dc2626', cursor: 'pointer' }}>
                            {(r.node.sign ?? -1) === -1 ? '− 차감' : '+ 가산'}
                          </button>
                        )}
                        {r.isItem && !r.isLeaf && !isNego && (
                          <span style={{ marginLeft: 8, fontSize: 10, background: '#3b82f6', color: '#fff', padding: '1px 6px', borderRadius: 999 }}>항목</span>
                        )}
                        {r.node._new && (
                          <>
                            <span style={{ marginLeft: 6, fontSize: 10, color: '#16a34a' }}>NEW</span>
                            <button onClick={() => removeNode(r.path)} title="추가한 항목 제외"
                              style={{ marginLeft: 6, fontSize: 11, fontWeight: 700, width: 18, height: 18, lineHeight: '14px',
                                borderRadius: 4, border: '1px solid #fca5a5', background: '#fff', color: '#dc2626', cursor: 'pointer' }}>
                              ✕
                            </button>
                          </>
                        )}
                      </span>
                    </td>
                    {VENDORS.map((v) => {
                      const p = r.prices[v];
                      const isMin = r.isItem && !isNego && p != null && p === minP;
                      return (
                        <td key={v} style={{ textAlign: 'right', padding: '8px 12px', whiteSpace: 'nowrap',
                          color: isNego ? '#b91c1c' : (r.isItem ? (isMin ? '#15803d' : '#374151') : '#cbd5e1'),
                          fontWeight: isMin ? 700 : 400, background: isMin ? '#f0fdf4' : 'transparent' }}>
                          {p != null && p < 0 ? '−' : ''}{won(p == null ? null : Math.abs(p))}{isMin && <span style={{ fontSize: 9, marginLeft: 3 }}>최저</span>}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr style={{ background: '#f9fafb', borderTop: '2px solid #e5e7eb', fontWeight: 700 }}>
                <td style={{ padding: '10px 12px', color: '#111827' }}>합계 (NEGO 반영)</td>
                {VENDORS.map((v) => (
                  <td key={v} style={{ textAlign: 'right', padding: '10px 12px', whiteSpace: 'nowrap', color: totals[v] === minTotal ? '#15803d' : '#111827' }}>
                    {totals[v] < 0 ? '−' : ''}{won(Math.abs(totals[v]))}
                  </td>
                ))}
              </tr>
            </tfoot>
          </table>
        </div>

        <p style={{ fontSize: 12, color: '#9ca3af', marginTop: 12, lineHeight: 1.7 }}>
          💡 <b>펼치지 않은 노드 = 항목</b>으로 비교·클러스터됩니다. 품명 클릭 = 수정(잎 데이터 보존, 표시명만).<br/>
          🔴 <b>SPECIAL NEGO</b>(차감 항목): <b>± 버튼</b>으로 부호 변경. 빨간 배경으로 구분. 합계에 부호 반영됩니다.<br/>
          ＋ <b>NEGO 항목 추가</b>: 누락된 차감 항목을 직접 추가. 추가한 항목은 <b style={{ color: '#dc2626' }}>✕ 버튼</b>으로 제외 가능.<br/>
          기존 옮기기·병합·제외·클러스터 명칭 변경 기능 모두 유지.
        </p>
      </div>
    </div>
  );
}

const btnBlue = { fontSize: 12, padding: '6px 12px', borderRadius: 8, border: 'none', background: '#1d4ed8', color: '#fff', cursor: 'pointer', fontWeight: 600 };
const btnSmall = { fontSize: 11, padding: '4px 10px', borderRadius: 6, border: '1px solid #d1d5db', background: '#fff', color: '#374151', cursor: 'pointer', fontWeight: 600 };
const btnSmallGreen = { fontSize: 11, padding: '4px 10px', borderRadius: 6, border: '1px solid #86efac', background: '#f0fdf4', color: '#15803d', cursor: 'pointer', fontWeight: 600 };
