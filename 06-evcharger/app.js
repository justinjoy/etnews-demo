'use strict';
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const distance=d=>d>=1000?`${(d/1000).toFixed(1)} km`:`${Math.round(d)} m`;
let token='',current=null,selected=null,busy=false,connected=false;
async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Run-Token':token},body:JSON.stringify(body)});const value=await r.json();if(!r.ok)throw new Error(value.error||`HTTP ${r.status}`);return value;}
function error(message){$('error').textContent=message;$('error').hidden=!message;}
function controls(){document.querySelectorAll('#form input,#form select,#form button').forEach(el=>el.disabled=busy||!connected);}
function config(){return {radius:Number($('radius').value),min_available:Number($('min-available').value),max_busy:Number($('max-busy').value),public_only:$('public-only').checked};}
function dirty(){$('radius-value').textContent=distance(Number($('radius').value));if(current){$('status').textContent='조건이 변경되었습니다. 검색하면 반영됩니다.';$('status').classList.add('dirty');$('map-badge').textContent='이전 검색 결과';}}
function renderMap(){
 const cx=350,cy=310,scale=.27;
 let svg='<title>강남역 중심의 충전소 거리와 방향</title><rect width="700" height="620" fill="#fbfcfd"/>';
 svg+=`<circle class="search-area" cx="${cx}" cy="${cy}" r="${current.filters.radius*scale}"/><path class="axis" d="M350 32 V588 M60 310 H640"/>`;
 for(const metres of [250,500,750,1000])svg+=`<circle class="ring" cx="${cx}" cy="${cy}" r="${metres*scale}"/><text x="${cx+6}" y="${cy-metres*scale-7}">${distance(metres)}</text>`;
 svg+='<text x="350" y="19" text-anchor="middle">N ↑</text><text x="659" y="314">E</text><text x="33" y="314">W</text><text x="350" y="607" text-anchor="middle">S</text>';
 svg+=`<circle class="origin-circle" cx="${cx}" cy="${cy}" r="8"/><text class="origin-label" x="${cx}" y="${cy+24}" text-anchor="middle">강남역</text>`;
 const ordered=[...current.stations].sort((a,b)=>Number(a.station===selected)-Number(b.station===selected));
 ordered.forEach(s=>{const x=cx+s.east*scale,y=cy-s.north*scale,rank=current.candidates.findIndex(c=>c.station===s.station)+1;svg+=`<g class="marker ${s.matched?'matched':'excluded'} ${s.station===selected?'selected':''}" tabindex="0" role="button" aria-label="${esc(s.name)} · ${distance(s.distance)} · 가용 ${s.available}대${s.matched?'':' · 조건 제외'}" data-station="${esc(s.station)}"><title>${esc(s.name)} · ${distance(s.distance)} · 사용 가능 ${s.available}/${s.total}대</title><circle cx="${x}" cy="${y}" r="${s.matched?8:5}"/>${s.matched?`<text x="${x}" y="${y+2.5}" text-anchor="middle">${rank}</text>`:''}</g>`;});
 const active=current.stations.find(s=>s.station===selected);
 if(active){const x=cx+active.east*scale,y=cy-active.north*scale,bx=Math.max(20,Math.min(492,x+15)),by=Math.max(38,Math.min(548,y-50));svg+=`<g class="callout" pointer-events="none"><rect x="${bx}" y="${by}" width="188" height="41" rx="6"/><text x="${bx+10}" y="${by+16}">${esc(active.name.length>19?active.name.slice(0,18)+'…':active.name)}</text><text x="${bx+10}" y="${by+31}">${distance(active.distance)} · 가용 ${active.available}/${active.total}대</text></g>`;}
 $('radial-map').innerHTML=`<svg viewBox="0 0 700 620" role="group" aria-label="충전소 위치도. 동심원 250, 500, 750, 1000미터">${svg}</svg>`;
 $('radial-map').querySelectorAll('[data-station]').forEach(el=>{el.addEventListener('click',()=>select(el.dataset.station));el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(el.dataset.station);}});});
}
function renderResults(){
 $('results').innerHTML=current.candidates.length?current.candidates.map((s,i)=>`<button class="station-card ${selected===s.station?'selected':''}" data-station="${esc(s.station)}" aria-pressed="${selected===s.station}"><span class="rank">${i+1}</span><span><strong>${esc(s.name)}</strong><small>${esc(s.address||'주소 미기록')}</small><span class="availability">사용 가능 <b>${s.available}</b> / ${s.total}대 · 충전 중 ${Math.round(s.busyRatio*100)}%</span></span><span class="distance">${distance(s.distance)}</span></button>`).join(''):'<p class="empty"><strong>조건을 만족하는 충전소가 없습니다.</strong>반경을 넓히거나 가용 대수·충전 중 비율 조건을 조정하세요.<br>회색 점을 선택하면 제외된 이유를 확인할 수 있습니다.</p>';
 $('results').querySelectorAll('[data-station]').forEach(el=>el.addEventListener('click',()=>select(el.dataset.station)));
}
function renderDetail(){
 const s=current.stations.find(st=>st.station===selected);
 if(!s){$('detail').innerHTML='<p class="empty">동심원의 점을 선택하면 제외된 충전소의 정보도 볼 수 있습니다.</p>';$('detail-badge').textContent='DETAIL';$('station-json').textContent='충전소를 선택하세요.';return;}
 $('detail-badge').textContent=s.matched?'조건 충족':'조건 제외';
 const updates=s.chargers.map(c=>c.updated).filter(Boolean).sort();
 $('detail').innerHTML=`<div class="detail-heading"><h3>${esc(s.name)}</h3><p>${esc(s.address||'주소 미기록')}</p><span class="distance-tag">강남역에서 직선 ${distance(s.distance)}</span></div>${s.matched?'':`<div class="excluded-note">검색 제외 이유 · ${s.reasons.map(esc).join(' / ')}</div>`}<div class="capacity"><div><strong>${s.available}</strong><small>사용 가능</small></div><div class="busy"><strong>${s.busy}</strong><small>충전 중</small></div><div class="other"><strong>${s.other}</strong><small>기타 / 미확인</small></div></div><dl class="detail-fields"><dt>가용 최대 출력</dt><dd>${s.maxOutput?`${s.maxOutput} kW`:'기록 없음 / 가용 충전기 없음'}</dd><dt>이용 시간</dt><dd>${esc(s.hours||'미기록')}</dd><dt>이용 제한</dt><dd>${s.accessConflict?'기록 상충 · 제한 있음으로 처리':s.access==='N'?'제한 없음':s.access==='Y'?'제한 있음':'미확인'}${s.accessNote?` · ${esc(s.accessNote)}`:''}</dd><dt>주차 요금</dt><dd>${s.parking==='Y'?'유료':s.parking==='N'?'무료':'미기록'}</dd><dt>최근 상태 기록</dt><dd>${esc(updates.at(-1)?.replace('T',' ')||'미기록')}</dd></dl><div class="table-wrap"><table><thead><tr><th>충전기</th><th>상태</th><th>출력 / 유형</th><th>상태 기록 시각</th></tr></thead><tbody>${s.chargers.map((c,i)=>`<tr><td>${String(i+1).padStart(2,'0')}</td><td class="${c.status==='사용가능'?'available-state':c.status==='충전중'?'busy-state':''}">${esc(c.status||'미기록')}</td><td>${esc(c.output||'—')} kW / ${esc(c.type||'미기록')}</td><td>${esc(c.updated?.replace('T',' ')||'미기록')}</td></tr>`).join('')}</tbody></table></div><p class="input-note">각 충전기의 상태 기록 시각은 다를 수 있습니다.</p>`;
 $('station-json').textContent=JSON.stringify(s,null,2);
}
function select(id){selected=id;renderMap();renderResults();renderDetail();}
function render(result){current=result;selected=result.candidates.some(s=>s.station===selected)?selected:(result.candidates[0]?.station||null);$('status').classList.remove('dirty');$('status').textContent=`조회 완료 · ${result.stats.matched}곳 · ${result.ms} ms`;$('map-badge').textContent=`RADIUS ${distance(result.filters.radius)}`;$('in-range').textContent=result.stats.within_radius;$('matched-count').textContent=result.stats.matched;$('available-count').textContent=result.stats.available;$('list-count').textContent=`${result.stats.matched} PLACES`;$('active-filters').innerHTML=[`반경 ${distance(result.filters.radius)}`,`가용 ${result.filters.min_available}대 이상`,result.filters.max_busy<100?`충전 중 ${result.filters.max_busy}% 이하`:'충전 중 비율 제한 없음',result.filters.public_only?'이용 제한 없음':'이용 제한 포함'].map(label=>`<span>${esc(label)}</span>`).join('');$('query').textContent=result.query;$('download').disabled=false;renderMap();renderResults();renderDetail();}
async function search(){busy=true;controls();error('');$('status').textContent='거리와 가용 상태를 조회하고 있습니다…';try{render(await api('/api/search',config()));}catch(e){error(e.message);$('status').textContent='조회 실패';}finally{busy=false;controls();}}
$('form').addEventListener('submit',e=>{e.preventDefault();search();});
$('radius').addEventListener('input',dirty);
for(const id of ['min-available','max-busy','public-only'])$(id).addEventListener('change',dirty);
document.querySelectorAll('[data-radius]').forEach(button=>button.addEventListener('click',()=>{$('radius').value=button.dataset.radius;$('radius-value').textContent=distance(Number(button.dataset.radius));search();}));
$('download').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='evcharger-results.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
async function init(){try{const data=await api('/api/status');token=data.token;connected=true;$('total-stations').textContent=data.station_count;$('total-chargers').textContent=data.charger_count;$('connection').textContent=`● 저장된 충전소 ${data.station_count}곳 준비됨${data.unlocated.length?` · 좌표 없는 ${data.unlocated.length}곳 제외`:''}`;$('center-query').textContent=data.queries.center;const filters=data.latest?.filters||data.defaults;$('radius').value=filters.radius;$('radius-value').textContent=distance(filters.radius);$('min-available').value=filters.min_available;$('max-busy').value=filters.max_busy;$('public-only').checked=filters.public_only;controls();if(data.latest)render(data.latest);else await search();}catch(e){error('서버 연결 실패. README.txt의 실행 방법을 확인하세요. '+e.message);}}
init();
