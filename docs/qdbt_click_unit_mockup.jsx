import React, { useState } from 'react';

// 추출된 데이터 (열 역할이 이미 지정되어 분류가 정리된 상태)
// 각 행: 대/중/소분류 + 품목 + 금액
const ROWS = [
  { cat1: "재료비", cat2: "기구부", cat3: "Tube", name: "Tube", spec: "", amount: 104000000 },
  { cat1: "재료비", cat2: "기구부", cat3: "Detector", name: "Detector", spec: "", amount: 48000000 },
  { cat1: "재료비", cat2: "기구부", cat3: "차폐", name: "Maint Door", spec: "Steel도장", amount: 1200000 },
  { cat1: "재료비", cat2: "기구부", cat3: "차폐", name: "측면 Door", spec: "Steel도장", amount: 1200000 },
  { cat1: "재료비", cat2: "기구부", cat3: "차폐", name: "납 BLOCK", spec: "순납", amount: 9600000 },
  { cat1: "재료비", cat2: "기구부", cat3: "차폐", name: "납 Plate", spec: "순납", amount: 7200000 },
  { cat1: "재료비", cat2: "기구부", cat3: "프레임", name: "하부 Frame", spec: "", amount: 5000000 },
  { cat1: "재료비", cat2: "기구부", cat3: "프레임", name: "Main Frame", spec: "", amount: 8000000 },
  { cat1: "재료비", cat2: "기구부", cat3: "셔틀", name: "Cylinder", spec: "", amount: 3840000 },
  { cat1: "재료비", cat2: "기구부", cat3: "셔틀", name: "Linear", spec: "", amount: 15000000 },
  { cat1: "재료비", cat2: "전장부", cat3: "제어", name: "제어 PC", spec: "", amount: 2500000 },
  { cat1: "재료비", cat2: "전장부", cat3: "제어", name: "ROBOT", spec: "", amount: 4250000 },
  { cat1: "인건비", cat2: "설계", cat3: "기구설계", name: "기구설계", spec: "", amount: 18000000 },
  { cat1: "인건비", cat2: "조립", cat3: "기구조립", name: "기구조립", spec: "", amount: 12000000 },
  { cat1: "이윤및관리비", cat2: "관리비", cat3: "관리비", name: "관리비", spec: "", amount: 30000000 },
  { cat1: "이윤및관리비", cat2: "이윤", cat3: "이윤", name: "이윤", spec: "", amount: 45000000 },
];

const won = (n) => n.toLocaleString('ko-KR');
const LEVELS = ["cat1", "cat2", "cat3"];
const LEVEL_NAMES = { cat1: "대분류", cat2: "중분류", cat3: "소분류" };

export default function App() {
  // 비교 단위 선택: 각 분류 경로(prefix)별로 "여기가 비교 단위"를 저장
  // 키 = "재료비 > 기구부", 값 = true (이 경로가 비교 단위)
  const [units, setUnits] = useState({
    // 초기: 추출 시 정한 레벨 (예: 기구부 묶음은 소분류, 나머지는 중분류)
  });

  // 어떤 분류 칸을 클릭하면 그 레벨이 비교 단위가 됨
  // path 구성
  const pathAt = (row, level) => LEVELS.slice(0, LEVELS.indexOf(level) + 1)
    .map((l) => row[l]).join(" > ");

  const toggleUnit = (path) => {
    setUnits((u) => {
      const next = { ...u };
      if (next[path]) delete next[path];
      else next[path] = true;
      return next;
    });
  };

  // 어떤 행의 어떤 분류가 "선택된 비교 단위"인지 판정
  const isUnit = (path) => !!units[path];
  // 이 행이 속한 비교 단위 찾기 (가장 구체적인 선택)
  const unitForRow = (row) => {
    for (let i = LEVELS.length - 1; i >= 0; i--) {
      const p = pathAt(row, LEVELS[i]);
      if (units[p]) return p;
    }
    return null;
  };

  // 비교 단위별 집계
  const grouped = {};
  ROWS.forEach((row) => {
    const unit = unitForRow(row);
    const key = unit || pathAt(row, "cat3"); // 선택 없으면 최하위(소분류)
    if (!grouped[key]) grouped[key] = { path: key, amount: 0, items: [] };
    grouped[key].amount += row.amount;
    grouped[key].items.push(row);
  });

  // 분류 셀 렌더 (병합처럼 같은 값 연속이면 첫 행만 표시)
  const showCell = (rows, idx, level) => {
    if (idx === 0) return true;
    return pathAt(rows[idx], level) !== pathAt(rows[idx - 1], level);
  };

  return (
    <div style={{ fontFamily: '-apple-system, "Malgun Gothic", sans-serif', background: '#f3f4f6', minHeight: '100vh', padding: 16 }}>
      <div style={{ maxWidth: 1000, margin: '0 auto' }}>

        <div style={{ marginBottom: 12 }}>
          <h1 style={{ fontSize: 19, fontWeight: 700, color: '#111827', margin: 0 }}>비교 단위 지정 — 분류 클릭</h1>
          <p style={{ fontSize: 13, color: '#6b7280', margin: '6px 0 0' }}>
            분류가 열로 정리되어 있습니다. <b style={{ color: '#1d4ed8' }}>분류 값을 클릭</b>하면 그 레벨이 비교 단위가 됩니다.
            트리를 펼칠 필요 없이 한 번에 지정. 그룹마다 다른 레벨도 가능합니다.
          </p>
        </div>

        <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', overflow: 'auto', marginBottom: 16 }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 13, width: '100%', minWidth: 760 }}>
            <thead>
              <tr style={{ background: '#f9fafb', borderBottom: '2px solid #e5e7eb' }}>
                <th style={th}>대분류</th>
                <th style={th}>중분류</th>
                <th style={th}>소분류</th>
                <th style={{ ...th, textAlign: 'left' }}>품목</th>
                <th style={{ ...th, textAlign: 'right' }}>금액</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row, i) => {
                const unit = unitForRow(row);
                return (
                  <tr key={i} style={{ borderBottom: '1px solid #f3f4f6' }}>
                    {LEVELS.map((level) => {
                      const path = pathAt(row, level);
                      const show = showCell(ROWS, i, level);
                      const selected = isUnit(path);
                      const isInUnit = unit === path;
                      return (
                        <td key={level} onClick={() => toggleUnit(path)}
                          style={{
                            ...cell, cursor: 'pointer',
                            background: selected ? '#dbeafe' : (isInUnit ? '#eff6ff' : 'transparent'),
                            fontWeight: selected ? 700 : 400,
                            color: show ? (selected ? '#1d4ed8' : '#374151') : '#d1d5db',
                            borderLeft: selected ? '3px solid #2563eb' : '1px solid #f3f4f6',
                          }}
                          onMouseEnter={(e) => { if (!selected) e.currentTarget.style.background = '#f1f5f9'; }}
                          onMouseLeave={(e) => { if (!selected) e.currentTarget.style.background = isInUnit ? '#eff6ff' : 'transparent'; }}>
                          {show ? row[level] : ''}
                          {selected && <span style={{ fontSize: 9, marginLeft: 4, background: '#2563eb', color: '#fff', padding: '0 5px', borderRadius: 999 }}>비교단위</span>}
                        </td>
                      );
                    })}
                    <td style={{ ...cell, textAlign: 'left', color: '#15803d' }}>
                      {row.name}
                      {row.spec && <span style={{ color: '#9ca3af', fontSize: 11, marginLeft: 6 }}>{row.spec}</span>}
                    </td>
                    <td style={{ ...cell, textAlign: 'right', color: '#6b7280' }}>{won(row.amount)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* 선택 결과: 비교 단위별 집계 */}
        <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', padding: 16 }}>
          <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 4px', color: '#111827' }}>
            현재 비교 단위 ({Object.keys(grouped).length}개)
          </h3>
          <p style={{ fontSize: 12, color: '#6b7280', margin: '0 0 10px' }}>
            선택한 분류가 비교 단위가 됩니다. 선택 안 한 곳은 소분류(최하위)가 기본 단위.
          </p>
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#f9fafb', color: '#6b7280', fontSize: 12 }}>
                <th style={{ textAlign: 'left', padding: '6px 10px' }}>비교 단위 (경로)</th>
                <th style={{ textAlign: 'right', padding: '6px 10px' }}>품목 수</th>
                <th style={{ textAlign: 'right', padding: '6px 10px' }}>금액 합계</th>
              </tr>
            </thead>
            <tbody>
              {Object.values(grouped).map((g, i) => (
                <tr key={i} style={{ borderTop: '1px solid #f3f4f6' }}>
                  <td style={{ padding: '6px 10px', fontWeight: 600, color: '#1d4ed8' }}>{g.path}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right', color: '#6b7280' }}>{g.items.length}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600 }}>{won(g.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p style={{ fontSize: 12, color: '#9ca3af', marginTop: 16, lineHeight: 1.7 }}>
          💡 <b>분류 클릭 = 비교 단위 지정.</b> "기구부"(중분류) 클릭하면 기구부가 한 단위로,
          "차폐"(소분류) 클릭하면 차폐가 한 단위로. 같은 화면에서 그룹마다 다른 레벨 선택 가능.<br/>
          트리 펼침/접기 없이 직관적입니다. 선택 안 하면 최하위(소분류)가 기본 비교 단위.
        </p>
      </div>
    </div>
  );
}

const th = { padding: '8px 12px', fontSize: 12, fontWeight: 600, color: '#374151', textAlign: 'center', borderRight: '1px solid #f3f4f6', whiteSpace: 'nowrap' };
const cell = { padding: '6px 12px', textAlign: 'center', whiteSpace: 'nowrap', borderRight: '1px solid #f3f4f6' };
