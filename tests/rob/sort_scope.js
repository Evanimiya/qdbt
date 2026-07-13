// [스코프 정렬] LKC.setSort 가 '선택 노드의 직계 자식'에만 적용됨을 서버리스 DOM으로 검증.
// 실행: node sort_scope.js  → PASS/FAIL. (전역 정렬이면 하위 그룹도 정렬돼 FAIL)
const fs = require('fs'), path = require('path');
const html = fs.readFileSync(path.join(__dirname, '../../src/web/templates/submissions/_link_canvas.html'), 'utf8');
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];

let captured = '';
function mkEl(cap) {
  const el = { className: '', style: {}, textContent: '', dataset: {}, parentNode: null,
    appendChild() {}, removeChild() {}, setAttribute() {}, getAttribute() { return ''; },
    addEventListener() {}, insertBefore() {}, querySelector() { return mkEl(false); },
    querySelectorAll() { return []; }, getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 20, right: 100, bottom: 20 }; } };
  Object.defineProperty(el, 'innerHTML', { set(v) { if (cap) captured = v; el._h = v; }, get() { return el._h || ''; } });
  return el;
}
const mount = mkEl(true);
global.window = { addEventListener() {}, prompt() { return null; }, confirm() { return true; } };
global.requestAnimationFrame = function () {};
global.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
global.document = { getElementById() { return mount; }, createElement() { return mkEl(false); },
  addEventListener() {}, body: mkEl(false), querySelector() { return mkEl(false); }, querySelectorAll() { return []; } };
global.getComputedStyle = function () { return { getPropertyValue() { return ''; } }; };
eval(js);
const LKC = global.window.LKC;
const LEVELS = ["대분류", "중분류", "소분류", "세분류", "품명", "부품", "세부"];

const leaf = (nm, p, amt) => ({ name: nm, path: p, level: 5, amount: amt, is_leaf: true, leaf_data: { item_id: nm }, children: [] });
const tree = [
  { name: 'A', path: 'A', level: 1, amount: 100, is_leaf: false, children: [leaf('a1', 'A > a1', 100)] },
  { name: 'B', path: 'B', level: 1, amount: 300, is_leaf: false, children: [leaf('b1', 'B > b1', 50), leaf('b2', 'B > b2', 250)] },
];
function order(colIdx) {
  const re = new RegExp('<div class="lkc-col" data-depth="' + colIdx + '">[\\s\\S]*?(?=<div class="lkc-col" data-depth=|$)');
  const m = captured.match(re); if (!m) return [];
  const names = []; const nmRe = /<span class="lkc-nm">([^<]*?)\s*<span/g; let x;
  while ((x = nmRe.exec(m[0]))) names.push(x[1].trim());
  return names;
}
LKC.init({ mount: 'm', tree, residual: [], manual: [], levels: LEVELS, subId: 's', readonly: true });

// 최상위(선택 없음) 금액↓ 정렬 → 루트만 정렬. B의 자식은 원래 순서(b1,b2) 유지여야(스코프 증명).
LKC.setSort('amt_desc');
const col0 = order(0);   // 대분류: 정렬 반영 → [B, A]
const col4 = order(4);   // 품명: B먼저(b1,b2 원순서) 그다음 a1  ← 전역이면 [b2,b1,a1]
let ok = true;
console.log('root 금액↓ 후 col0(대분류):', JSON.stringify(col0));
console.log('              col4(품명)  :', JSON.stringify(col4));
if (!(col0[0] === 'B' && col0[1] === 'A')) { ok = false; console.log('  ❌ 최상위 정렬 미반영'); }
if (!(col4[0] === 'b1' && col4[1] === 'b2')) { ok = false; console.log('  ❌ 스코프 위반: 하위(B) 자식이 정렬됨(전역 정렬)'); }

console.log(ok ? '✅ PASS 스코프 정렬(선택 노드 직계 자식만)' : '❌ FAIL');
process.exit(ok ? 0 : 1);
