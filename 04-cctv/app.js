'use strict';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {weather:'날씨',pedestrian_congestion:'보행 혼잡도',traffic_volume:'교통량'};
const names = {Clear:'맑음',Cloudy:'흐림',Rain:'비',Snow:'눈',Fog:'안개',Unknown:'판단 불가',VeryCrowded:'매우 혼잡',Crowded:'혼잡',Normal:'보통',Sparse:'한산',Heavy:'많음',Moderate:'보통',Light:'적음'};
const levels = {Sparse:1,Normal:2,Crowded:3,VeryCrowded:4,Light:1,Moderate:2,Heavy:3};
let token='', ready=false, busy=false, current=null, timer=null;
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Run-Token':token},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function error(message) {$('error').textContent=message;$('error').hidden=!message;}
function controls() {$('run').disabled=busy||!ready;document.querySelectorAll('#run-form input').forEach(el=>el.disabled=busy);$('extract').disabled=busy||current?.state!=='waiting';}
function metrics(data) {
  $('metrics').innerHTML=Object.keys(labels).map(key=>{
    const value=data?.[key], max=key==='pedestrian_congestion'?4:3;
    const bar=key==='weather'?'<div class="metric-icon">◌</div>':`<div class="level ${levels[value]===max?'high':''}">${Array.from({length:max},(_,i)=>`<span class="${i<(levels[value]||0)?'on':''}"></span>`).join('')}</div>`;
    return `<div class="metric"><small>${labels[key]}</small><strong>${names[value]||'—'}</strong><code>${esc(value||'WAITING')}</code>${bar}</div>`;
  }).join('');
}
function graph(run) {
  if(!run.rows.length){$('graph').innerHTML='<p class="empty">분석 후 실제 RDF 관계가 여기에 표시됩니다.</p>';return;}
  const short=s=>s.split(/[\/#]/).pop();
  const sensor=run.rows[0], position=sensor.location.match(/POINT\(([^)]+)\)/)?.[1]||sensor.location;
  let body='<title>실제 RDF의 센서, 관측 속성과 결과 연결</title><path class="edge" d="M108 155 V76"/><text class="caption" x="125" y="115">geo:hasGeometry</text><rect class="node" x="38" y="28" width="140" height="45" rx="8"/><text x="108" y="48" text-anchor="middle">'+esc(sensor.label)+' · 위치</text><text class="caption" x="108" y="63" text-anchor="middle">'+esc(position)+'</text><rect class="sensor" x="38" y="155" width="140" height="62" rx="9"/><text x="108" y="180" text-anchor="middle">CCTV Sensor</text><text x="108" y="199" text-anchor="middle">'+esc(short(sensor.sensor))+'</text>';
  run.rows.forEach((row,i)=>{const y=64+i*100, val=short(row.result);body+=`<path class="edge" d="M270 ${y} H230 V186 H178 M412 ${y} H480"/><text class="caption" x="230" y="${y-14}" text-anchor="middle">madeBySensor ←</text><rect class="node" x="270" y="${y-26}" width="142" height="53" rx="8"/><text class="caption" x="341" y="${y-7}" text-anchor="middle">Observation / observedProperty</text><text x="341" y="${y+12}" text-anchor="middle">${esc(short(row.property))}</text><text class="caption" x="446" y="${y-8}" text-anchor="middle">hasResult</text><rect class="result" x="480" y="${y-24}" width="132" height="49" rx="8"/><text class="value" x="546" y="${y-2}" text-anchor="middle">${esc(names[val])}</text><text class="caption" x="546" y="${y+15}" text-anchor="middle">${esc(val)}</text>`;});
  $('graph').innerHTML=`<svg viewBox="0 0 650 310" role="img" aria-label="센서와 세 관측의 RDF 연결">${body}</svg>`;
}
function render(run) {
  current=run; busy=run.state==='running'; controls();
  metrics(run.data); graph(run);
  $('result-badge').textContent=run.data?'MODEL OUTPUT':'WAITING';
  $('graph-badge').textContent=run.ttl?'3 OBSERVATIONS · 22 TRIPLES':'SOSA';
  const states={running:'실제 모델 처리 중입니다. 모델 크기에 따라 수 분 걸릴 수 있습니다.',waiting:'영문 설명을 확인한 뒤 변환 버튼을 누르세요.',done:'완료 · 실제 모델 응답 → RDF → SPARQL 조회',error:'실행 실패 · 아래 호출 기록에서 응답을 확인하세요.'};
  $('run-status').textContent=states[run.state];
  error([run.error,...run.warnings].filter(Boolean).join('\n'));
  $('description-panel').hidden=!run.description;
  $('description-en').textContent=run.description;$('description-ko').textContent=run.translation||'한국어 번역을 준비하고 있습니다.';
  if(run.warnings.length&&!run.translation) $('description-ko').textContent='번역 실패 · 영문 원문을 확인하세요.';
  $('ttl').textContent=run.ttl||'분석 후 생성됩니다.';
  $('row-count').textContent=`${run.rows.length} ROWS`;
  $('rows').innerHTML=run.rows.length?run.rows.map(row=>`<tr><td>${esc(row.label)}<small>L010009</small></td><td>${esc(row.property.split('/').pop())}</td><td><strong>${esc(row.result.split('/').pop())}</strong></td><td>${esc(row.time)}</td></tr>`).join(''):'<tr><td colspan="4" class="empty">분석을 실행하면 조회 결과가 나타납니다.</td></tr>';
  $('calls').innerHTML=run.calls.map((call,i)=>`<div class="call-record"><h3>${i+1}. ${esc(call.name)}</h3><p>${esc(run.model)} · 이미지 ${call.images}장 · ${esc(call.state)} · ${call.seconds??'…'}초${call.tokens?` · ${call.tokens} tokens`:''}</p><label>실제 프롬프트</label><pre>${esc(call.prompt)}</pre><label>모델 원문 응답</label><pre>${esc(call.response||call.error||'Ollama 응답 대기 중…')}</pre></div>`).join('');
  $('call-count').textContent=`${run.calls.length}회`;$('run-id').textContent=`실행 ${run.id} · ${run.created} · ${run.mode==='direct'?'이미지 → JSON':'이미지 → 설명 → JSON'}`;
  $('download-json').disabled=false;$('download-ttl').disabled=!run.ttl;
  const phase=run.phase==='translate'?'extract':run.phase;
  const order=['vision','extract','rdf','query','done'], index=order.indexOf(phase);
  document.querySelectorAll('[data-phase]').forEach(el=>{const n=order.indexOf(el.dataset.phase);el.classList.toggle('active',n===index);el.classList.toggle('done',n<index);});
}
async function poll(id) {
  clearTimeout(timer);
  try {const run=await api(`/api/runs/${id}`);render(run);if(run.state==='running')timer=setTimeout(()=>poll(id),1200);}
  catch(e){error('실행 상태 조회 실패: '+e.message);timer=setTimeout(()=>poll(id),4000);}
}
$('run-form').addEventListener('submit',async e=>{
  e.preventDefault();busy=true;controls();error('');
  try {const result=await api('/api/runs',{mode:document.querySelector('[name=mode]:checked').value,time:$('time').value});await poll(result.id);}
  catch(e){busy=false;controls();error(e.message);}
});
$('extract').addEventListener('click',async()=>{busy=true;controls();error('');try{await api(`/api/runs/${current.id}/extract`,{});await poll(current.id);}catch(e){busy=false;controls();error(e.message);}});
function download(value,type,name){const url=URL.createObjectURL(new Blob([value],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('download-json').addEventListener('click',()=>download(JSON.stringify(current,null,2),'application/json',`cctv-${current.id}.json`));
$('download-ttl').addEventListener('click',()=>download(current.ttl,'text/turtle',`cctv-${current.id}.ttl`));
for(let i=1;i<=5;i++) {
  const number=String(i).padStart(2,'0'),button=document.createElement('button');button.type='button';button.setAttribute('aria-label',`스냅샷 ${i} 보기`);button.setAttribute('aria-pressed',String(i===1));button.innerHTML=`<img src="shots/shot_${number}.jpg" alt=""><span>${number}</span>`;
  button.addEventListener('click',()=>{$('main-image').src=`shots/shot_${number}.jpg`;$('main-image').alt=`CCTV 원본 스냅샷 ${i}`;$('frame-label').textContent=`FRAME ${number} / 05`;document.querySelectorAll('#filmstrip button').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));});$('filmstrip').append(button);
}
metrics(null);
async function connect(){try{const status=await api('/api/status');token=status.token;ready=status.ready;busy=!!status.active;$('connection').textContent=status.ready?'● Ollama 연결됨 · 실제 모델 호출 준비':'○ Ollama 연결 또는 모델 설치 필요';$('model').textContent=status.model;$('sensor-count').textContent=status.sensors.toLocaleString();$('triple-count').textContent=status.triples.toLocaleString();$('sensor-name').textContent=status.sensor.label;$('location').textContent=status.sensor.wkt;$('query').textContent=status.query;controls();if(status.latest){await poll(status.latest);if(current){document.querySelector(`[name=mode][value="${current.mode}"]`).checked=true;$('time').value=current.time;}}else if(status.error)error(status.error);if(!status.ready)timer=setTimeout(connect,6000);}catch(e){error('서버 연결 실패. README.txt의 Python 서버 실행 방법을 확인하세요. '+e.message);timer=setTimeout(connect,6000);}}
connect();
