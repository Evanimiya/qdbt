import React, { useState } from 'react';

// 실제 견적서 (엑셀 원본 그대로)
const RAW = [
  ["", "", "", "↓필요시", "↓기입후", "", "", "", "", "", ""],
  ["", "", "대분류", "중분류", "소분류", "주요구성품", "Q'ty", "Unit", "Unit Price", "Total Price", "Remarks"],
  ["", "재료비", "기구부", "Tube", "Tube", "", "2", "식", "52000000", "104000000", ""],
  ["", "재료비", "기구부", "Detector", "Detector", "", "2", "식", "24000000", "48000000", ""],
  ["", "재료비", "기구부", "차폐", "Maint Door", "Steel도장", "2", "식", "600000", "1200000", "설계중"],
  ["", "재료비", "기구부", "차폐", "측면 Door", "Steel도장", "2", "EA", "600000", "1200000", "설계중"],
  ["", "재료비", "기구부", "차폐", "납 BLOCK", "순납", "150", "EA", "64000", "9600000", "설계중"],
  ["", "재료비", "기구부", "차폐", "납 Plate", "순납", "30", "EA", "240000", "7200000", "설계중"],
  ["", "재료비", "기구부", "프레임", "하부 Frame", "", "1", "식", "5000000", "5000000", ""],
  ["", "재료비", "기구부", "셔틀", "Cylinder", "", "2", "EA", "1920000", "3840000", ""],
  ["", "재료비", "전장부", "제어", "제어 PC", "", "1", "EA", "2500000", "2500000", ""],
  ["", "인건비", "설계", "기구설계", "기구설계", "", "1", "식", "18000000", "18000000", ""],
  ["", "이윤및관리비", "관리비", "관리비", "관리비", "", "1", "식", "30000000", "30000000", ""],
];
const ROW_LABELS = RAW.map((_, i) => "R" + (i + 1));

const ROLES = [
  { id: "", label: "—", color: "#9ca3af" },
  { id: "cat1", label: "대분류", color: "#1d4ed8" },
  { id: "cat2", label: "중분류", color: "#2563eb" },
  { id: "cat3", label: "소분류", color: "#3b82f6" },
  { id: "cat4", label: "세분류", color: "#60a5fa" },
  { id: "name", label: "품목명", color: "#15803d" },
  { id: "spec", label: "규격", color: "#16a34a" },
  { id: "qty", label: "수량", color: "#ca8a04" },
  { id: "unit", label: "단위", color: "#a16207" },
  { id: "price", label: "단가", color: "#dc2626" },
  { id: "amount", label: "금액", color: "#b91c1c" },
  { id: "remark", label: "비고", color: "#6b7280" },
  { id: "ignore", label: "무시", color: "#d1d5db" },
];
const roleOf = (id) => ROLES.find((r) => r.id === id) || ROLES[0];
const won = (s) => { const n = parseInt(s); return isNaN(n) ? s : n.toLocaleString('ko-KR'); };

export default function App() {
  const nCols = RAW[1].length;
  const [headerRow, setHeaderRow] = useState(2);
  const [mode, setMode] = useState("map"); // map | select

  // LLM 제안 초기 매핑
  const [map, setMap] = useState({
    1: "ignore", 2: "cat1", 3: "cat2", 4: "cat3", 5: "name",
    6: "spec", 7: "qty", 8: "unit", 9: "price", 10: "amount", 11: "remark",
  });
  const setColRole = (col, r) => setMap((m) => ({ ...m, [col]: r }));

  // 비교 단위 선택 (분류 경로 → true)
  const [units, setUnits] = useState({});
  const toggleUnit = (path) => setUnits((u) => {
    const n = { ...u }; if (n[path]) delete n[path]; else n[path] = true; return n;
  });

  // 분류 열들 (cat1~cat4 순) — 먼저 정의 (일괄선택에서 사용)
  const catColsTop = Object.entries(map)
    .filter(([, r]) => r.startsWith("cat"))
    .sort((a, b) => a[1].localeCompare(b[1]))
    .map(([c]) => parseInt(c));

  // 특정 레벨로 일괄 선택 (해당 레벨의 모든 분류 경로를 비교 단위로)
  const bulkSelectLevel = (catColIdx) => {
    const dataR = RAW.slice(headerRow);
    const paths = {};
    dataR.forEach((row) => {
      const p = catColsTop.slice(0, catColIdx + 1).map((c) => row[c - 1]).filter(Boolean).join(" > ");
      if (p) paths[p] = true;
    });
    setUnits(paths);  // 기존 선택 대체 (일괄은 새로 깔기)
  };
  const clearUnits = () => setUnits({});

  // 분류 열들 (cat1~cat4 순)
  const catCols = catColsTop;
  const colByRole = (role) => { const e = Object.entries(map).find(([, r]) => r === role); return e ? parseInt(e[0]) : null; };

  const dataRows = RAW.slice(headerRow); // 헤더 다음부터
  const pathTo = (row, catColIdx) => catCols.slice(0, catColIdx + 1).map((c) => row[c - 1]).filter(Boolean).join(" > ");

  // 비교단위 판정
  const unitForRow = (row) => {
    for (let i = catCols.length - 1; i >= 0; i--) {
      const p = pathTo(row, i);
      if (units[p]) return p;
    }
    return null;
  };

  // 집계
  const grouped = {};
  if (mode === "select") {
    dataRows.forEach((row) => {
      const unit = unitForRow(row) || pathTo(row, catCols.length - 1);
      if (!grouped[unit]) grouped[unit] = { path: unit, amount: 0, n: 0 };
      const amtCol = colByRole("amount");
      grouped[unit].amount += amtCol ? (parseInt(row[amtCol - 1]) || 0) : 0;
      grouped[unit].n += 1;
    });
  }

  const showCell = (rows, idx, catColIdx) => {
    if (idx === 0) return true;
    return pathTo(rows[idx], catColIdx) !== pathTo(rows[idx - 1], catColIdx);
  };

  return (
    <div style={{ fontFamily: '-apple-system, "Malgun Gothic", sans-serif', background: '#f3f4f6', minHeight: '100vh', padding: 16 }}>
      <div style={{ maxWidth: 1040, margin: '0 auto' }}>

        <div style={{ marginBottom: 12 }}>
          <h1 style={{ fontSize: 19, fontWeight: 700, color: '#111827', margin: 0 }}>견적서 추출 · 비교 단위 설정</h1>
          <p style={{ fontSize: 13, color: '#6b7280', margin: '6px 0 0' }}>
            엑셀을 그대로 보며 ① 열 역할을 지정하고 ② 분류를 클릭해 비교 단위를 정합니다.
          </p>
        </div>

        {/* 모드 전환 */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', background: '#e5e7eb', borderRadius: 8, padding: 3 }}>
            <button onClick={() => setMode("map")} style={tab(mode === "map")}>① 열 역할 지정</button>
            <button onClick={() => setMode("select")} style={tab(mode === "select")}>② 비교 단위 선택</button>
          </div>
          {mode === "map" && (
            <>
              <span style={{ fontSize: 13, color: '#374151', marginLeft: 8 }}>헤더 행:</span>
              <select value={headerRow} onChange={(e) => setHeaderRow(+e.target.value)} style={{ fontSize: 13, border: '1px solid #d1d5db', borderRadius: 6, padding: '4px 8px' }}>
                {ROW_LABELS.map((r, i) => <option key={i} value={i + 1}>{r}</option>)}
              </select>
            </>
          )}
          {mode === "map" && (
            <span style={{ marginLeft: 'auto', fontSize: 12, color: '#b45309', background: '#fffbeb', padding: '3px 8px', borderRadius: 6, border: '1px solid #fde68a' }}>
              ⚠ "재료비"는 헤더 빈 분류 → B열을 대분류로 지정
            </span>
          )}
          {mode === "select" && (
            <span style={{ marginLeft: 'auto', fontSize: 12, color: '#1d4ed8', background: '#eff6ff', padding: '3px 8px', borderRadius: 6 }}>
              분류 칸을 클릭 = 비교 단위. 그룹마다 다른 레벨 가능.
            </span>
          )}
        </div>

        {/* select 모드: 일괄 선택 바 */}
        {mode === "select" && (
          <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', padding: '10px 14px', marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
            <span style={{ color: '#374151', fontWeight: 600 }}>일괄 선택:</span>
            {catColsTop.map((c, idx) => {
              const role = roleOf(map[c]);
              return (
                <button key={c} onClick={() => bulkSelectLevel(idx)}
                  style={{ fontSize: 12, padding: '5px 12px', borderRadius: 6, border: `1.5px solid ${role.color}`, background: '#fff', color: role.color, fontWeight: 600, cursor: 'pointer' }}>
                  {role.label}로 일괄
                </button>
              );
            })}
            <button onClick={clearUnits} style={{ fontSize: 12, padding: '5px 12px', borderRadius: 6, border: '1px solid #d1d5db', background: '#fff', color: '#6b7280', fontWeight: 600, cursor: 'pointer' }}>
              전체 해제
            </button>
            <span style={{ fontSize: 11, color: '#9ca3af', marginLeft: 4 }}>
              → 일괄로 깐 뒤, 개별 분류를 클릭해 조정하세요.
            </span>
          </div>
        )}

        {/* 메인 표 */}
        <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', overflow: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: 900 }}>
            <thead>
              {/* 역할 드롭다운 (map 모드만) */}
              {mode === "map" && (
                <tr>
                  <th style={{ ...cH, position: 'sticky', left: 0, background: '#f3f4f6' }}></th>
                  {Array.from({ length: nCols }, (_, i) => i + 1).map((col) => {
                    const role = roleOf(map[col]);
                    return (
                      <th key={col} style={{ ...cH, padding: 4, background: '#fafbfc' }}>
                        <select value={map[col] || ""} onChange={(e) => setColRole(col, e.target.value)}
                          style={{ fontSize: 11, fontWeight: 700, border: `1.5px solid ${role.color}`, borderRadius: 5, padding: '3px 4px', color: role.color, background: '#fff', cursor: 'pointer', width: '100%', minWidth: 62 }}>
                          {ROLES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                        </select>
                      </th>
                    );
                  })}
                </tr>
              )}
              {/* 열문자 */}
              <tr>
                <th style={{ ...cH, position: 'sticky', left: 0, background: '#f3f4f6' }}></th>
                {Array.from({ length: nCols }, (_, i) => i + 1).map((col) => (
                  <th key={col} style={{ ...cH, color: '#9ca3af', fontWeight: 400, background: '#f9fafb' }}>{String.fromCharCode(64 + col)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {RAW.map((row, ri) => {
                const isHeader = (ri + 1) === headerRow;
                const isData = (ri + 1) > headerRow;
                const dataIdx = ri - headerRow; // dataRows 내 인덱스
                return (
                  <tr key={ri} style={{ background: isHeader ? '#eff6ff' : '#fff' }}>
                    <td style={{ ...cB, color: '#9ca3af', fontWeight: 600, background: '#f9fafb', position: 'sticky', left: 0, textAlign: 'center' }}>{ROW_LABELS[ri]}</td>
                    {Array.from({ length: nCols }, (_, i) => i + 1).map((col) => {
                      const val = row[col - 1] || "";
                      const role = roleOf(map[col]);
                      const isCat = role.id.startsWith("cat");
                      const catColIdx = catCols.indexOf(col);

                      // select 모드 + 분류 열 + 데이터 행 → 클릭 가능
                      if (mode === "select" && isData && isCat && catColIdx >= 0) {
                        const path = pathTo(row, catColIdx);
                        const show = showCell(dataRows, dataIdx, catColIdx);
                        const selected = !!units[path];
                        const inUnit = unitForRow(row) === path;
                        return (
                          <td key={col} onClick={() => toggleUnit(path)}
                            style={{
                              ...cB, cursor: 'pointer', textAlign: 'center',
                              background: selected ? '#dbeafe' : (inUnit ? '#eff6ff' : 'transparent'),
                              fontWeight: selected ? 700 : 400,
                              color: show ? (selected ? '#1d4ed8' : '#374151') : '#e5e7eb',
                              borderLeft: selected ? '3px solid #2563eb' : '1px solid #f3f4f6',
                            }}>
                            {show ? val : ''}
                            {selected && <span style={{ fontSize: 8, marginLeft: 3, background: '#2563eb', color: '#fff', padding: '0 4px', borderRadius: 999 }}>단위</span>}
                          </td>
                        );
                      }

                      // 일반 셀
                      const isAmt = role.id === "amount" || role.id === "price";
                      const tint = isData && map[col] && map[col] !== "ignore" && mode === "map" ? role.color + "0c" : "transparent";
                      return (
                        <td key={col} style={{
                          ...cB, background: tint, textAlign: isAmt ? 'right' : 'center',
                          color: val ? (isCat ? '#1e40af' : '#374151') : '#e5e7eb',
                          fontWeight: isHeader ? 700 : 400,
                          borderLeft: isData && isCat && mode === 'map' ? `2px solid ${role.color}22` : '1px solid #f3f4f6',
                        }}>
                          {isAmt && val ? won(val) : (val || '·')}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* select 모드: 비교 단위 집계 */}
        {mode === "select" && (
          <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', padding: 16, marginTop: 12 }}>
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 8px', color: '#111827' }}>
              비교 단위 ({Object.keys(grouped).length}개) — 선택 안 한 곳은 최하위 분류 기본
            </h3>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#f9fafb', color: '#6b7280', fontSize: 12 }}>
                  <th style={{ textAlign: 'left', padding: '6px 10px' }}>비교 단위</th>
                  <th style={{ textAlign: 'right', padding: '6px 10px' }}>품목 수</th>
                  <th style={{ textAlign: 'right', padding: '6px 10px' }}>금액</th>
                </tr>
              </thead>
              <tbody>
                {Object.values(grouped).map((g, i) => (
                  <tr key={i} style={{ borderTop: '1px solid #f3f4f6' }}>
                    <td style={{ padding: '6px 10px', fontWeight: 600, color: '#1d4ed8' }}>{g.path}</td>
                    <td style={{ padding: '6px 10px', textAlign: 'right', color: '#6b7280' }}>{g.n}</td>
                    <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600 }}>{won(String(g.amount))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p style={{ fontSize: 12, color: '#9ca3af', marginTop: 16, lineHeight: 1.7 }}>
          💡 <b>①탭</b>: 엑셀을 보며 열 역할 지정(분류/품목/단가). LLM이 제안, 사람이 확인.
          → <b>②탭</b>: 정리된 분류를 클릭해 비교 단위 지정. "기구부" 클릭=중분류 단위, "차폐" 클릭=소분류 단위.<br/>
          추출은 확인된 매핑으로 코드가 결정론적 처리. 트리 펼침 없이 직관적입니다.
        </p>
      </div>
    </div>
  );
}

const tab = (active) => ({ fontSize: 13, padding: '6px 14px', borderRadius: 6, border: 'none', background: active ? '#fff' : 'transparent', color: active ? '#1d4ed8' : '#6b7280', fontWeight: 600, cursor: 'pointer', boxShadow: active ? '0 1px 2px rgba(0,0,0,0.1)' : 'none' });
const cH = { border: '1px solid #e5e7eb', padding: '4px 6px', fontSize: 11, textAlign: 'center', whiteSpace: 'nowrap' };
const cB = { border: '1px solid #f3f4f6', padding: '4px 8px', whiteSpace: 'nowrap', maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis' };
