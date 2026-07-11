// [T8] 잎펼치기 render() 서버리스 DOM 검사 (블로킹/서버/jsdom 없음).
// 준비: (1) bid.html 마지막 <script>의 render/won/esc → /tmp/t8_render.js (Jinja 치환)
//       (2) get_cluster_leaves 실 JSON → /tmp/leaf_samples.json
// 실행: node tests/rob/t8_render_domcheck.js  (생성 DOM 노드수 == 기대 검증)
// 최소 DOM 스텁: innerHTML 캡처 + querySelector/All 안전 스텁 (render 실행용, 서버·jsdom 없음)
function mkEl(){
  return {
    _html:'', className:'', style:{}, textContent:'',
    set innerHTML(v){ this._html=v; }, get innerHTML(){ return this._html; },
    appendChild(){}, removeChild(){}, setAttribute(){}, getAttribute(){return '';},
    addEventListener(){}, set onclick(f){}, set oninput(f){}, set onkeydown(f){},
    querySelector(sel){ return mkEl(); },
    querySelectorAll(sel){ return []; },
    dataset:{}, parentNode:null,
  };
}
global.window = { openLeafModal:null };
global.document = {
  createElement(){ return mkEl(); },
  addEventListener(){}, removeEventListener(){}, body:{ appendChild(){}, removeChild(){} },
};
require('/tmp/t8_render.js');
const render = globalThis.__render;
const samples = require('/tmp/leaf_samples.json');

let allOK = true;
for(const d of samples){
  // veil.querySelector('.lfx-modal') 가 반환하는 el 의 innerHTML 을 캡처하도록 veil 스텁
  const modal = mkEl();
  const veil = { querySelector(sel){ return sel==='.lfx-modal'? modal : mkEl(); },
                 parentNode:{ removeChild(){} } };
  render(veil, d, d.name);
  const html = modal._html;
  const nLeaf = (html.match(/class="lfx-leaf"/g)||[]).length;
  const nGrp  = (html.match(/class="lfx-grp"/g)||[]).length;
  const nMin  = (html.match(/class="lv lo"/g)||[]).length;
  const expLeaf = d.groups.reduce((a,g)=>a+g.rows.length,0);
  const expGrp  = d.groups.length;
  const expMin  = d.groups.reduce((a,g)=>a+g.rows.filter(r=>r.min_vendor).length,0);
  const hasFoot = html.indexOf('소계 정합')>=0;
  const ok = nLeaf===expLeaf && nGrp===expGrp && nMin===expMin && hasFoot;
  if(!ok) allOK=false;
  console.log(`  ${ok?'✅':'❌'} ${d.name}: 잎노드 ${nLeaf}/${expLeaf} 그룹 ${nGrp}/${expGrp} 최저가 ${nMin}/${expMin} 푸터=${hasFoot}`);
}
console.log(allOK? '\nALL OK — render()가 실 JSON에서 정확한 DOM 생성(서버 없이 검증)' : '\nFAIL');
process.exit(allOK?0:1);
