// [범용] LKC 연계 캔버스를 서버리스 DOM으로 렌더해 '열(의미레벨)별 노드 배치'를 출력.
// 사용: node canvas_grid.js <tree.json>  → JSON [{col, header, nodes:[names]}] 출력.
// 실제 _link_canvas.html LKC 렌더 실행(의미레벨 열 배치·빈 열 검증용, jsdom/서버 없음).
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
  Object.defineProperty(el, 'innerHTML', { set(v) { if (cap) captured = v; el._h = v; }, get() { return el._h || ''; } });
  return el;
}
const mount = mkEl(true);
global.window = { addEventListener() {}, prompt() { return null; }, confirm() { return true; } };
global.requestAnimationFrame = function () {};
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

const tree = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
captured = '';
try { LKC.init({ mount: 'm', tree, residual: [], manual: [], levels: LEVELS, subId: 's', readonly: true }); }
catch (e) { console.error('init 예외:', e.message); }

// 컬럼 단위로 분해: 각 lkc-col 조각에서 헤더 + 노드명(lkc-nm) 추출.
const out = [];
const colRe = /<div class="lkc-col" data-depth="(\d+)"><div class="lkc-col-h">([^<]*)<[\s\S]*?(?=<div class="lkc-col" data-depth=|<\/div><\/div>$|$)/g;
let m;
while ((m = colRe.exec(captured))) {
  const frag = m[0];
  const names = [];
  const nmRe = /<span class="lkc-nm">([^<]*?)\s*<span/g;
  let x;
  while ((x = nmRe.exec(frag))) names.push(x[1].trim());
  out.push({ col: +m[1], header: m[2].trim(), nodes: names });
}
process.stdout.write(JSON.stringify(out));
