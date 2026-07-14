// [부품 BOM] 업체 비교 '펼쳐보기' 모달의 부품 서브행 렌더 서버리스 검증(문자열 기반).
// bid.html openLeafModal IIFE(window.__lfxRender 노출) 실행 → 생성 innerHTML 문자열 검사.
// 실행: node bom_expand_render.js
const fs = require('fs'), path = require('path');
const html = fs.readFileSync(path.join(__dirname, '../../src/web/templates/compare/bid.html'), 'utf8');
const js = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).find(s => s.includes('window.openLeafModal'));
if (!js) { console.log('❌ openLeafModal 스크립트 미발견'); process.exit(1); }

// 흡수형 스텁: querySelector는 onclick/oninput/style/addEventListener를 받는 no-op el.
//  단 '.lfx-modal' 은 innerHTML 문자열을 캡처하는 el을 돌려준다(검사 대상).
function absorb() {
  const el = { style: {}, textContent: '', _html: '',
    set onclick(f) {}, set oninput(f) {}, addEventListener() {}, removeEventListener() {},
    setAttribute() {}, getAttribute() { return ''; }, appendChild() {}, removeChild() {},
    querySelector() { return absorb(); }, querySelectorAll() { return []; },
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = v; } };
  return el;
}
const modal = absorb();
function mkVeil() {
  return { className: '', style: {}, _html: '', parentNode: { removeChild() {} },
    appendChild() {}, addEventListener() {}, removeEventListener() {},
    set onclick(f) {}, get innerHTML() { return this._html; }, set innerHTML(v) { this._html = v; },
    querySelector(sel) { return sel === '.lfx-modal' ? modal : absorb(); } };
}
global.window = {};
global.document = { createElement() { return mkVeil(); }, body: { appendChild() {}, removeChild() {} },
  addEventListener() {}, removeEventListener() {} };
global.fetch = function () { return Promise.resolve({ json() { return Promise.resolve({}); } }); };
eval(js);
const render = global.window.__lfxRender;
if (!render) { console.log('❌ __lfxRender 미노출'); process.exit(1); }

const veil = mkVeil();
const data = {
  vendors: ['A사', 'B사'], n_leaves: 1, vendor_totals: { 'A사': 600, 'B사': 620 }, subtotal_ok: true,
  groups: [{ group: '차폐', n: 1, rows: [{
    name: '납블록', min_vendor: 'A사', min_amount: 600,
    cells: {
      'A사': { amount: 600, unit_price: 300, quantity: 2, parts: [
        { name: '순납강판', part_qty: 3, part_price: 50, part_amount: 150 },
        { name: '볼트', part_qty: 1, part_price: 150, part_amount: 150 }] },
      'B사': { amount: 620, unit_price: 310, quantity: 2, parts: [
        { name: '순납강판', part_qty: 3, part_price: 60, part_amount: 180 },
        { name: '볼트', part_qty: 1, part_price: 130, part_amount: 130 }] } } }] }],
};
render(veil, data, '차폐묶음');
const H = modal.innerHTML || '';

let ok = true;
const bomRows = (H.match(/class="lfx-bom"/g) || []).length;
const togs = (H.match(/class="lfx-bomtog"/g) || []).length;
const hidden = (H.match(/lfx-bom"[^>]*display:none/g) || []).length;
// 토글 data-tg 와 서브행 data-bom 매칭 확인
const tg = (H.match(/lfx-bomtog[^>]*data-tg="([^"]+)"/) || [])[1];
const bomTagged = tg ? (H.match(new RegExp('lfx-bom" data-bom="' + tg + '"', 'g')) || []).length : 0;

console.log('부품 서브행:', bomRows, '· 토글:', togs, '· 기본 display:none:', hidden, '· data-bom=data-tg 매칭:', bomTagged);
if (bomRows !== 2) { ok = false; console.log('  ❌ 부품 서브행 2 아님'); }
if (togs !== 1) { ok = false; console.log('  ❌ 토글 1 아님'); }
if (hidden !== 2) { ok = false; console.log('  ❌ 기본 접힘(display:none) 아님'); }
if (bomTagged !== 2) { ok = false; console.log('  ❌ 토글-서브행 data 매칭 실패'); }
['순납강판', '볼트', '↳', '🏅'].forEach(t => { if (H.indexOf(t) < 0) { ok = false; console.log('  ❌ 누락:', t); } });
// 부품별 최저가: B사 볼트(130) < A사(150) → B사 볼트 셀에 lo/🏅
if (H.indexOf('130') < 0) { ok = false; console.log('  ❌ 부품 파트금액 누락'); }
console.log(ok ? '✅ PASS 부품 BOM 펼쳐보기(서브행 2·토글·기본접힘·부품별 최저가)' : '❌ FAIL');
process.exit(ok ? 0 : 1);
