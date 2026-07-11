// [범용] LKC 연계 캔버스 열(depth) 헤더 라벨을 서버리스 DOM으로 렌더해 출력.
// 사용: node canvas_labels.js <tree.json>   → JSON 배열(열 헤더 라벨)을 stdout에 출력.
// 실제 _link_canvas.html의 LKC 렌더를 그대로 실행(라벨 로직 충실 검증, jsdom/서버 없음).
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
try { LKC.init({ mount: 'm', tree: tree, residual: [], manual: [], levels: LEVELS, subId: 's', readonly: true }); }
catch (e) { console.error('init 예외:', e.message); }
const re = /lkc-col-h">([^<]*)</g; let x; const out = [];
while ((x = re.exec(captured))) out.push(x[1]);
process.stdout.write(JSON.stringify(out));
