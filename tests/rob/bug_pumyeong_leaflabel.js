// [품명레벨 버그] LKC 연계 캔버스 열(depth) 헤더 라벨 서버리스 DOM 검사 (jsdom/서버 없음).
// 재현: [대,중,품명] 시트는 품명 잎이 depth2에 놓여, 고정 위치매핑(depth2=소분류)상 '소분류'로
//   오라벨됐다. 수정: 그 depth가 '전부 잎'이면 '품명'으로 표기.
// 실행: node tests/rob/bug_pumyeong_leaflabel.js
const fs = require('fs'), path = require('path');
const html = fs.readFileSync(path.join(__dirname, '../../src/web/templates/submissions/_link_canvas.html'), 'utf8');
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];

let captured = '';
function mkEl(cap) {
  const el = {
    className: '', style: {}, textContent: '', dataset: {}, parentNode: null,
    appendChild() {}, removeChild() {}, setAttribute() {}, getAttribute() { return ''; },
    addEventListener() {}, insertBefore() {},
    querySelector() { return mkEl(false); }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 20, right: 100, bottom: 20 }; },
  };
  Object.defineProperty(el, 'innerHTML', {
    set(v) { if (cap) captured = v; el._h = v; }, get() { return el._h || ''; },
  });
  return el;
}
const mount = mkEl(true);
global.window = { addEventListener() {}, prompt() { return null; }, confirm() { return true; } };
global.requestAnimationFrame = function () {};   // drawLinks 지연 실행 skip
global.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
global.document = {
  getElementById() { return mount; }, createElement() { return mkEl(false); },
  addEventListener() {}, body: mkEl(false),
  querySelector() { return mkEl(false); }, querySelectorAll() { return []; },
};
global.getComputedStyle = function () { return { getPropertyValue() { return ''; } }; };
eval(js);
const LKC = global.window.LKC;

const LEVELS = ["대분류", "중분류", "소분류", "세분류", "품명", "부품", "세부"];
function headers(tree) {
  captured = '';
  try {
    LKC.init({ mount: 'm', tree: tree, residual: [], manual: [], levels: LEVELS, subId: 's', readonly: true });
  } catch (e) { console.log('  (init 예외:', e.message, ')'); }
  const re = /lkc-col-h">([^<]*)</g; let x; const out = [];
  while ((x = re.exec(captured))) out.push(x[1]);
  return out;
}
const leaf = (name, p, d) => ({ name, path: p, amount: 1000, depth: d, is_leaf: true, children: [] });
const br = (name, p, d, ch) => ({ name, path: p, amount: 1000, depth: d, is_leaf: false, children: ch });

// [대,중,품명] — 품명(납블록/PLC)이 잎
const t1 = [br('재료비', '재료비', 1, [
  br('기구부', '재료비 > 기구부', 2, [leaf('납블록', '재료비 > 기구부 > 납블록', 3)]),
  br('전장부', '재료비 > 전장부', 2, [leaf('PLC', '재료비 > 전장부 > PLC', 3)]),
])];
// [대,중,소,세,품명] — 정상 4분류 + 품명 잎(회귀 확인)
const t2 = [br('A', 'A', 1, [br('B', 'A > B', 2, [br('C', 'A > B > C', 3, [
  br('D', 'A > B > C > D', 4, [leaf('품X', 'A > B > C > D > 품X', 5)])])])])];

const h1 = headers(t1), h2 = headers(t2);
let ok = true;
console.log('[대,중,품명] 열 헤더:', JSON.stringify(h1));
if (!(h1[0] === '대분류' && h1[1] === '중분류' && h1[2] === '품명' && !h1.includes('소분류'))) {
  ok = false; console.log('  ❌ FAIL: 품명 잎 열이 소분류로 오라벨 (기대: 대분류|중분류|품명)');
}
console.log('[대,중,소,세,품명] 열 헤더:', JSON.stringify(h2));
if (!(h2[0] === '대분류' && h2[1] === '중분류' && h2[2] === '소분류' && h2[3] === '세분류' && h2[4] === '품명')) {
  ok = false; console.log('  ❌ FAIL: 정상 5레벨 회귀');
}
console.log(ok ? '✅ PASS 품명레벨 라벨 (버그 수정 확인)' : '❌ FAIL');
process.exit(ok ? 0 : 1);
