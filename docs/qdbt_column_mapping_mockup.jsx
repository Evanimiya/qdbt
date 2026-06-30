import React, { useState } from 'react';

// 실제 견적서 데이터 (진단 결과 그대로)
const HEADER_ROW = 3; // 헤더가 있는 행
const RAW = [
  ["", "", "", "↓필요시", "↓기입후", "", "", "", "", "", ""],
  ["", "", "대분류", "중분류", "소분류", "주요구성품", "Q'ty", "Unit", "Unit Price", "Total Price", "Remarks"],
  ["", "재료비", "기구부", "Tube", "Tube", "", "2", "식", "52000000", "104000000", ""],
  ["", "재료비", "기구부", "Detector", "Detector", "", "2", "식", "24000000", "48000000", ""],
  ["", "재료비", "기구부", "차폐", "Maint Door", "Steel도장", "2", "식", "600000", "1200000", "설계중"],
  ["", "재료비", "기구부", "차폐", "측면 Door", "Steel도장", "2", "EA", "600000", "1200000", "설계중"],
  ["", "재료비", "기구부", "차폐", "납 BLOCK", "순납", "150", "EA", "64000", "9600000", "설계중"],
  ["", "재료비", "기구부", "차폐", "납 Plate", "순납", "30", "EA", "240000", "7200000", "설계중"],
  ["", "재료비", "기구부", "차폐", "고정 샤프트", "SUS304", "100", "EA", "48000", "4800000", "설계중"],
  ["", "재료비", "기구부", "차폐", "SENSOR", "FT-H50(K)", "2", "EA", "1950000", "3900000", ""],
];
// 행 번호(엑셀 기준): RAW[0]=R1 ... 헤더는 R3 (index 1)... 실제로는 R1부터지만 데모상 R1~R10
const ROW_LABELS = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10"];

// 역할 종류
const ROLES = [
  { id: "", label: "—", color: "#9ca3af", group: "" },
  { id: "cat1", label: "대분류", color: "#1d4ed8", group: "분류" },
  { id: "cat2", label: "중분류", color: "#2563eb", group: "분류" },
  { id: "cat3", label: "소분류", color: "#3b82f6", group: "분류" },
  { id: "cat4", label: "세분류", color: "#60a5fa", group: "분류" },
  { id: "name", label: "품목명", color: "#15803d", group: "정보" },
  { id: "spec", label: "규격", color: "#16a34a", group: "정보" },
  { id: "qty", label: "수량", color: "#ca8a04", group: "정보" },
  { id: "unit", label: "단위", color: "#a16207", group: "정보" },
  { id: "price", label: "단가", color: "#dc2626", group: "정보" },
  { id: "amount", label: "금액", color: "#b91c1c", group: "정보" },
  { id: "remark", label: "비고", color: "#6b7280", group: "정보" },
  { id: "ignore", label: "무시", color: "#d1d5db", group: "" },
];

const roleOf = (id) => ROLES.find((r) => r.id === id) || ROLES[0];

export default function App() {
  const nCols = RAW[1].length;

  // LLM이 제안했다고 가정한 초기 매핑 (헤더 읽고 추론)
  const [mapping, setMapping] = useState({
    1: "", // A: 빈칸
    2: "cat1", // B: 재료비 → 대분류 (헤더엔 비었지만 LLM이 감지)
    3: "cat2", // C: 대분류 헤더지만 실제 기구부 → 중분류로 보정?  ※ 데모는 헤더명 기준
    4: "cat3", 5: "name", 6: "spec", 7: "qty", 8: "unit", 9: "price", 10: "amount",
  });
  // 위는 "빈 분류 레벨" 이슈를 보여주기 위해 의도적으로 B열을 대분류로 잡음

  // 더 정확한 초기값: 헤더명 그대로 (대/중/소분류)
  const [map2, setMap2] = useState({
    1: "ignore", 2: "cat1", 3: "cat2", 4: "cat3", 5: "name",
    6: "spec", 7: "qty", 8: "unit", 9: "price", 10: "amount", 11: "remark",
  });

  const [headerRow, setHeaderRow] = useState(3);
  const [showResult, setShowResult] = useState(false);

  const setColRole = (col, roleId) => setMap2((m) => ({ ...m, [col]: roleId }));

  // 미리보기: 현재 매핑으로 path 구성 (코드가 할 일을 시뮬레이션)
  const catCols = Object.entries(map2)
    .filter(([, r]) => r.startsWith("cat"))
    .sort((a, b) => a[1].localeCompare(b[1]))
    .map(([c]) => parseInt(c));
  const nameCol = Object.entries(map2).find(([, r]) => r === "name")?.[0];

  const dataRows = RAW.slice(2); // 헤더 다음부터
  const preview = dataRows.map((row) => {
    const parts = catCols.map((c) => row[c - 1]).filter((x) => x);
    const name = nameCol ? row[nameCol - 1] : "";
    return { path: parts.join(" > "), name };
  });

  return (
    <div style={{ fontFamily: '-apple-system, "Malgun Gothic", sans-serif', background: '#f3f4f6', minHeight: '100vh', padding: 16 }}>
      <div style={{ maxWidth: 1000, margin: '0 auto' }}>

        <div style={{ marginBottom: 12 }}>
          <h1 style={{ fontSize: 19, fontWeight: 700, color: '#111827', margin: 0 }}>추출 기준 설정 — 열 역할 지정</h1>
          <p style={{ fontSize: 13, color: '#6b7280', margin: '6px 0 0' }}>
            엑셀을 그대로 표시합니다. 각 열이 <b style={{ color: '#1d4ed8' }}>분류(대/중/소)</b>인지
            <b style={{ color: '#15803d' }}> 정보(품목/규격/단가)</b>인지 지정하세요.
            <b> LLM이 제안한 값</b>이 미리 채워져 있습니다. 확인 후 수정하세요.
          </p>
        </div>

        {/* 헤더 행 지정 */}
        <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', padding: '10px 14px', marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
          <span style={{ color: '#374151', fontWeight: 600 }}>헤더 행:</span>
          <select value={headerRow} onChange={(e) => setHeaderRow(+e.target.value)}
            style={{ fontSize: 13, border: '1px solid #d1d5db', borderRadius: 6, padding: '4px 8px' }}>
            {ROW_LABELS.map((r, i) => <option key={i} value={i + 1}>{r}</option>)}
          </select>
          <span style={{ color: '#9ca3af', fontSize: 12 }}>← 분류/품목 제목이 있는 행. 이 아래부터 데이터로 추출.</span>
          <span style={{ marginLeft: 'auto', fontSize: 12, color: '#b45309', background: '#fffbeb', padding: '3px 8px', borderRadius: 6, border: '1px solid #fde68a' }}>
            ⚠ "재료비"는 헤더 빈 분류 — B열을 대분류로 지정됨
          </span>
        </div>

        {/* 스프레드시트 + 열 역할 드롭다운 */}
        <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', overflow: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: 880 }}>
            <thead>
              {/* 역할 지정 행 */}
              <tr>
                <th style={{ ...cellHead, background: '#f3f4f6', position: 'sticky', left: 0, zIndex: 2 }}></th>
                {Array.from({ length: nCols }, (_, i) => i + 1).map((col) => {
                  const role = roleOf(map2[col]);
                  return (
                    <th key={col} style={{ ...cellHead, padding: 4, background: '#fafbfc' }}>
                      <select value={map2[col] || ""} onChange={(e) => setColRole(col, e.target.value)}
                        style={{
                          fontSize: 11, fontWeight: 700, border: `1.5px solid ${role.color}`,
                          borderRadius: 5, padding: '3px 4px', color: role.color, background: '#fff',
                          cursor: 'pointer', width: '100%', minWidth: 64,
                        }}>
                        {ROLES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                      </select>
                    </th>
                  );
                })}
              </tr>
              {/* 엑셀 열문자 (A,B,C..) */}
              <tr>
                <th style={{ ...cellHead, background: '#f3f4f6', position: 'sticky', left: 0 }}></th>
                {Array.from({ length: nCols }, (_, i) => i + 1).map((col) => (
                  <th key={col} style={{ ...cellHead, color: '#9ca3af', fontWeight: 400, background: '#f9fafb' }}>
                    {String.fromCharCode(64 + col)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {RAW.map((row, ri) => {
                const isHeader = (ri + 1) === headerRow;
                const isData = (ri + 1) > headerRow;
                return (
                  <tr key={ri} style={{ background: isHeader ? '#eff6ff' : (isData ? '#fff' : '#fafafa') }}>
                    <td style={{ ...cellBody, color: '#9ca3af', fontWeight: 600, background: '#f9fafb', position: 'sticky', left: 0, textAlign: 'center' }}>
                      {ROW_LABELS[ri]}
                    </td>
                    {Array.from({ length: nCols }, (_, i) => i + 1).map((col) => {
                      const val = row[col - 1] || "";
                      const role = roleOf(map2[col]);
                      const tint = isData && role.id && role.id !== "ignore"
                        ? role.color + "0f" : "transparent";
                      return (
                        <td key={col} style={{
                          ...cellBody, background: tint,
                          color: val ? '#374151' : '#d1d5db',
                          fontWeight: isHeader ? 700 : 400,
                          borderLeft: isData && role.id && role.id !== 'ignore' ? `2px solid ${role.color}33` : '1px solid #f3f4f6',
                        }}>
                          {val || '·'}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* 액션 */}
        <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <button onClick={() => setShowResult(!showResult)}
            style={{ fontSize: 13, padding: '8px 16px', borderRadius: 8, border: 'none', background: '#1d4ed8', color: '#fff', fontWeight: 600, cursor: 'pointer' }}>
            {showResult ? '미리보기 닫기' : '이 매핑으로 추출 미리보기'}
          </button>
          <span style={{ fontSize: 12, color: '#6b7280' }}>
            분류 열: {catCols.map((c) => String.fromCharCode(64 + c)).join(", ") || "없음"} → path 자동 구성 (코드)
          </span>
        </div>

        {/* 미리보기: 코드가 만들 path */}
        {showResult && (
          <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', padding: 16, marginTop: 12 }}>
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: '0 0 8px', color: '#111827' }}>
              추출 결과 미리보기 (코드가 path 구성 — LLM 없이)
            </h3>
            <p style={{ fontSize: 12, color: '#6b7280', margin: '0 0 10px' }}>
              지정한 분류 열로 path를 조립하고, 품목명은 분리합니다. 병합/빈칸은 코드가 상속 처리.
            </p>
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#f9fafb', color: '#6b7280' }}>
                  <th style={{ textAlign: 'left', padding: '6px 10px' }}>분류 경로 (path)</th>
                  <th style={{ textAlign: 'left', padding: '6px 10px' }}>품목명</th>
                </tr>
              </thead>
              <tbody>
                {preview.map((p, i) => (
                  <tr key={i} style={{ borderTop: '1px solid #f3f4f6' }}>
                    <td style={{ padding: '6px 10px', color: '#1d4ed8', fontWeight: 600 }}>{p.path}</td>
                    <td style={{ padding: '6px 10px', color: '#15803d' }}>{p.name}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p style={{ fontSize: 12, color: '#9ca3af', marginTop: 16, lineHeight: 1.7 }}>
          💡 <b>2단계 추출</b>: ① LLM이 헤더 읽고 열 역할 제안 → 사람이 확인/수정 (이 화면)
          → ② 확인된 매핑으로 <b>코드가 결정론적 추출</b> (셀→DB, 병합 풀기, path 구성).<br/>
          분류(대/중/소/세)와 정보(품목/규격/단가)가 역할로 분리됩니다. LLM 추측이 없어 정확하고 빠릅니다.
        </p>
      </div>
    </div>
  );
}

const cellHead = { border: '1px solid #e5e7eb', padding: '4px 6px', fontSize: 11, textAlign: 'center', whiteSpace: 'nowrap' };
const cellBody = { border: '1px solid #f3f4f6', padding: '4px 8px', whiteSpace: 'nowrap', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis' };
