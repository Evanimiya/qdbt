import React, { useState } from 'react';

// 진단 결과의 실제 양식 구조 — 계층 트리로 구성
// 각 노드: { name, amount(잎만), children }
const TREE = [
  {
    name: "재료비", children: [
      {
        name: "기구부", children: [
          { name: "차폐", children: [
            { name: "납 BLOCK", amount: 9600000, line: "1.1.1" },
            { name: "고정 샤프트", amount: 4800000, line: "1.1.2" },
            { name: "납 Plate", children: [
              { name: "납 Plate 상부", amount: 7545000, line: "1.1.3.1" },
              { name: "납 Plate 측면", amount: 5030000, line: "1.1.3.2" },
            ]},
          ]},
          { name: "프레임", children: [
            { name: "각관 Frame", amount: 6260000, line: "1.2.1" },
            { name: "상부 FENCE", amount: 3000000, line: "1.2.2" },
          ]},
          { name: "셔틀", children: [
            { name: "Cylinder", amount: 3840000, line: "1.3.1" },
            { name: "Linear", amount: 15000000, line: "1.3.2" },
            { name: "SENSOR", amount: 90000, line: "1.3.3" },
          ]},
        ]
      },
      {
        name: "전장/제어부", children: [
          { name: "운영/제어부", children: [
            { name: "키보드/마우스", amount: 65000, line: "2.1.1" },
            { name: "제어 PC", amount: 2500000, line: "2.1.2" },
          ]},
          { name: "전장/전장부", children: [
            { name: "Servo Motor", amount: 1180000, line: "2.2.1" },
            { name: "ROBOT", amount: 4250000, line: "2.2.2" },
          ]},
        ]
      },
    ]
  },
  {
    name: "인건비", children: [
      { name: "인건비(설계제작)", children: [
        { name: "설계 인건비", amount: 18000000, line: "3.1" },
        { name: "제작 인건비", amount: 22000000, line: "3.2" },
      ]},
      { name: "인건비(셋업양산대기)", children: [
        { name: "셋업 인건비", amount: 12000000, line: "3.3" },
      ]},
    ]
  },
  {
    name: "경비", children: [
      { name: "출장비", amount: 5000000, line: "4.1" },
      { name: "운반비", amount: 3000000, line: "4.2" },
    ]
  },
  {
    name: "이윤 및 관리비", children: [
      { name: "관리비", amount: 30000000, line: "5.1" },
      { name: "이윤", amount: 45000000, line: "5.2" },
    ]
  },
];

const won = (n) => n.toLocaleString('ko-KR');

// 노드 합계 (잎=amount, 가지=자식 합)
function sumNode(node) {
  if (node.amount != null) return node.amount;
  return (node.children || []).reduce((s, c) => s + sumNode(c), 0);
}

function App() {
  // 펼침 상태: 노드 경로(key) -> true/false
  const [open, setOpen] = useState({ "재료비": true, "재료비>기구부": true });

  const toggle = (key) => setOpen((o) => ({ ...o, [key]: !o[key] }));

  const expandAll = () => {
    const all = {};
    const walk = (nodes, prefix) => {
      nodes.forEach((n) => {
        const key = prefix ? prefix + ">" + n.name : n.name;
        if (n.children) { all[key] = true; walk(n.children, key); }
      });
    };
    walk(TREE, "");
    setOpen(all);
  };
  const collapseAll = () => setOpen({});

  const total = TREE.reduce((s, n) => s + sumNode(n), 0);

  // 재귀 렌더링
  const renderNode = (node, prefix, depth) => {
    const key = prefix ? prefix + ">" + node.name : node.name;
    const isLeaf = !node.children;
    const isOpen = open[key];
    const amount = sumNode(node);
    const path = key.split(">");

    const rows = [];
    // 비교 단위 판정: 더 펼칠 수 없는 노드가 비교 단위.
    //  - 잎(leaf): 항상 비교 단위
    //  - 펼치지 않은 가지: 비교 단위
    //  - 펼친 가지: 비교 단위 아님 (그 아래가 단위)
    const isCompareUnit = isLeaf || !isOpen;

    rows.push(
      <div key={key}
        onClick={() => !isLeaf && toggle(key)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '7px 12px', paddingLeft: 12 + depth * 22,
          borderTop: '1px solid #f3f4f6',
          cursor: isLeaf ? 'default' : 'pointer',
          background: isCompareUnit ? '#eff6ff' : '#fff',
        }}
        onMouseEnter={(e) => { if (!isLeaf) e.currentTarget.style.background = isCompareUnit ? '#dbeafe' : '#f9fafb'; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = isCompareUnit ? '#eff6ff' : '#fff'; }}
      >
        <span style={{ display: 'flex', alignItems: 'center', fontSize: 14 }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 18, height: 18, marginRight: 6, fontSize: 11, fontWeight: 700,
            border: isLeaf ? 'none' : '1px solid #cbd5e1', borderRadius: 4,
            color: isLeaf ? 'transparent' : '#475569',
            background: isLeaf ? 'transparent' : '#fff',
          }}>
            {isLeaf ? '·' : (isOpen ? '−' : '+')}
          </span>
          <span style={{
            fontWeight: isLeaf ? 400 : 600,
            color: isLeaf ? '#374151' : '#1f2937',
          }}>
            {isLeaf && node.line && <span style={{ color: '#9ca3af', fontSize: 12 }}>[{node.line}] </span>}
            {node.name}
          </span>
          {isCompareUnit && (
            <span style={{ marginLeft: 8, fontSize: 10, background: '#3b82f6', color: '#fff', padding: '1px 6px', borderRadius: 999 }}>
              비교 단위
            </span>
          )}
        </span>
        <span style={{
          fontSize: 14, fontWeight: isLeaf ? 400 : 600,
          color: isLeaf ? '#374151' : '#111827',
        }}>{won(amount)}</span>
      </div>
    );

    if (!isLeaf && isOpen) {
      node.children.forEach((c) => {
        rows.push(...renderNode(c, key, depth + 1));
      });
    }
    return rows;
  };

  return (
    <div style={{ fontFamily: '-apple-system, "Malgun Gothic", sans-serif', background: '#f3f4f6', minHeight: '100vh', padding: 16 }}>
      <div style={{ maxWidth: 760, margin: '0 auto' }}>

        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 20, marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <h1 style={{ fontSize: 18, fontWeight: 700, color: '#111827', margin: 0 }}>㈜자비스 — 추출 검토</h1>
            <span style={{ fontSize: 11, background: '#dcfce7', color: '#15803d', padding: '2px 8px', borderRadius: 999 }}>추출 완료</span>
            <span style={{ fontSize: 11, color: '#9ca3af', marginLeft: 'auto' }}>v0.8.9+</span>
          </div>
          <p style={{ fontSize: 13, color: '#6b7280', margin: '6px 0 0' }}>서산3동 이물검사 견적서 · 총액 {won(total)}원</p>
        </div>

        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 12 }}>
            <div>
              <h2 style={{ fontSize: 16, fontWeight: 700, color: '#111827', margin: 0 }}>🌳 비교 단위 트리</h2>
              <p style={{ fontSize: 12, color: '#6b7280', margin: '4px 0 0' }}>
                +/− 로 그룹별로 원하는 깊이까지 펼치세요. <b style={{ color: '#3b82f6' }}>펼치지 않은 가지</b>가 비교 단위가 됩니다.
              </p>
            </div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
              <button onClick={expandAll} style={btnStyle}>모두 펼치기</button>
              <button onClick={collapseAll} style={btnStyle}>모두 접기</button>
            </div>
          </div>

          <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: '#f9fafb', fontSize: 12, color: '#6b7280', fontWeight: 500 }}>
              <span>분류 트리</span>
              <span>금액 (하위 합산)</span>
            </div>
            {TREE.map((n) => renderNode(n, "", 0))}
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: '#f9fafb', borderTop: '2px solid #e5e7eb', fontWeight: 700, color: '#111827', fontSize: 14 }}>
              <span>합계</span>
              <span>{won(total)}</span>
            </div>
          </div>

          <div style={{ marginTop: 12, padding: 12, background: '#f0f9ff', borderRadius: 8, fontSize: 12, color: '#0369a1' }}>
            💡 <b>그룹별 다른 깊이</b>: 예를 들어 "재료비 &gt; 기구부"는 펼쳐서 차폐·프레임·셔틀까지 세밀하게,
            "이윤 및 관리비"는 접어서 대분류로. 파랗게 표시된 <b>비교 단위</b>들이 업체 비교 시 매칭 기준이 됩니다.
          </div>
        </div>

        <p style={{ textAlign: 'center', fontSize: 12, color: '#9ca3af', marginTop: 24 }}>
          QDBT 비교 단위 트리 (목업) · 펼친 상태 = 비교 단위. 추출/비교 화면이 각자의 펼침 상태를 가짐.
        </p>
      </div>
    </div>
  );
}

const btnStyle = {
  fontSize: 12, padding: '5px 10px', borderRadius: 8,
  border: '1px solid #d1d5db', background: '#fff', color: '#374151',
  cursor: 'pointer', fontWeight: 600,
};

export default App;
