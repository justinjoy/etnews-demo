'use strict';
const { TfidfRetriever, buildPrompt } = AmnesiaRag;
let scenario = SCENARIO;
let retriever = new TfidfRetriever(scenario.docs);
const $ = id => document.getElementById(id);
const escapeHtml = text => String(text).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const state = { step: 0, topK: 1, backend: null, job: null, poll: null, error: '', follow: true, starting: false };
const descriptions = [
  '같은 질문과 검색 문맥으로 두 모드의 1턴 답변을 각각 생성합니다. 실제 응답 내용은 서로 다를 수 있습니다.',
  '메모리 모드의 1턴 답변을 실행별 criteria.md에 저장합니다. 일반 RAG에는 이전 답변을 전달하지 않습니다.',
  '같은 오늘 일기를 검색합니다. 메모리 모드만 저장된 파일을 다시 읽어 2턴 프롬프트에 추가합니다.',
  '실제 2턴 응답에 세 기준이 언급되는지 검사합니다. 0/3, 3/3 등의 결과를 미리 정하지 않습니다.',
];
const callFor = (mode, turn) => state.job?.calls.find(call => call.mode === mode && call.turn === turn);
const running = () => state.starting || state.job?.status === 'running';
function criteriaList(scores, lost = false) {
  if (!scores) return '<p class="waiting">응답을 받은 뒤 기준 언급 여부를 계산합니다.</p>';
  return `<div class="criteria-list">${Object.keys(scenario.criteria).map(name => {
    const present = scores[name] && !lost;
    return `<div class="criterion${present ? '' : ' lost'}"><span aria-hidden="true">${present ? '✓' : '−'}</span><strong>${escapeHtml(name)}</strong><small>${lost ? '다음 턴 전달 없음' : present ? '언급됨' : '언급 없음'}</small></div>`;
  }).join('')}</div>`;
}
function contextBlocks(retrieved) {
  return retrieved.map(({ doc, score }) => `<div class="context-block"><strong>${escapeHtml(doc.title)}<small>유사도 ${score.toFixed(3)}</small></strong><p>${doc.id.startsWith('01') ? '내일도 지킬 기준: 걷기 · 커피 지출 · 야식' : '제품 회고 회의 · 데모 자료 수정 · 편의점 생수'}</p></div>`).join('');
}
function scoreCard(scores) {
  const count = Object.values(scores).filter(Boolean).length;
  return `<div class="score-row"><div class="score-ring" style="--kept:${count}" aria-label="기준 ${count}개 언급, 전체 3개"><strong>${count}<span style="font-size:11px"> / 3</span></strong></div><div><h3>실제 답변에 언급된 기준</h3><p>원본의 키워드 검사 적용<br>생활 기준을 실제로 지켰다는 의미는 아닙니다.</p></div></div>`;
}
function answerBlock(call, showScore = true) {
  if (!call || call.status !== 'complete') {
    const label = call?.status === 'running' ? 'Claude가 실제 답변을 생성하고 있습니다…' : call?.status === 'error' ? '이 호출에 실패했습니다. 위 오류 메시지를 확인하세요.' : call?.status === 'cancelled' ? '이 호출은 취소되었습니다.' : state.job?.status === 'error' || state.job?.status === 'cancelled' ? '실행이 중단되어 이 호출은 수행하지 않았습니다.' : '아직 응답이 없습니다. 실제 Claude 실행을 기다립니다.';
    return `<div class="waiting ${call?.status === 'running' ? 'generating' : ''}">${label}</div>`;
  }
  return `<div class="answer-label"><p class="flow-label" style="margin:0">${call.turn}턴 · 실제 Claude 응답</p><span>${call.duration_seconds.toFixed(1)}초</span></div><p class="answer">${escapeHtml(call.answer)}</p>${criteriaList(call.criteria)}${showScore ? scoreCard(call.criteria) : ''}`;
}
function renderModes(currentRetrieved) {
  for (const mode of ['naive', 'fixed']) {
    const first = callFor(mode, 1);
    let content;
    if (state.step === 0) {
      content = `<p class="flow-label">검색된 컨텍스트</p>${contextBlocks(first?.retrieved || currentRetrieved)}<div class="flow-arrow" aria-hidden="true">↓</div>${answerBlock(first, false)}`;
    } else if (state.step === 1) {
      if (mode === 'naive') content = '<p class="flow-label">1턴 답변의 기준 언급 여부</p>' + criteriaList(first?.criteria, true) + '<div class="boundary">TURN 01 끝 ─── 이전 답변 전달 없음</div><p class="state-message">일반 RAG의 다음 요청에는 새 질문과 검색 결과만 포함합니다.</p>';
      else content = '<p class="flow-label">파일에 저장한 실제 1턴 답변</p>' + (state.job?.memory ? `<pre class="stored-memory">${escapeHtml(state.job.memory)}</pre><div class="boundary">실제 파일 저장 완료 → 다음 턴에서 읽기</div>${criteriaList(first?.criteria)}` : '<p class="waiting">메모리 모드의 1턴이 성공하면 파일에 저장합니다. 아직 저장된 기준이 없습니다.</p>');
    } else if (state.step === 2) {
      const call = callFor(mode, 2);
      content = `<p class="flow-label">2턴 프롬프트 = 질문 + 검색된 일기${mode === 'fixed' ? ' + 파일에서 읽은 기준' : ''}</p>${contextBlocks(call?.retrieved || currentRetrieved)}`;
      if (mode === 'fixed') {
        const memory = call?.memory_injected ?? state.job?.memory;
        content += `<div class="flow-arrow" aria-hidden="true">＋</div><div class="context-block memory-block"><strong>${call?.memory_injected ? '파일에서 읽어 실제 주입한 기준' : memory ? '파일 저장 완료 · 주입 대기' : '메모리 저장 대기'}</strong><pre>${escapeHtml(memory || '1턴의 실제 응답을 기다립니다.')}</pre></div>`;
      } else content += '<p class="state-message">이전 턴의 답변을 자동으로 전달하지 않습니다. 검색 문서 안에 포함된 기준만 참고할 수 있습니다.</p>';
      content += `<p class="flow-label call-state">${call ? call.status === 'complete' ? '이 요청의 응답 수신 완료' : call.status === 'running' ? '이 요청으로 Claude 호출 중' : '이 요청 중단' : '아직 전송하지 않은 요청'}</p>`;
    } else content = answerBlock(callFor(mode, 2));
    $(mode + '-body').innerHTML = content;
  }
}
function renderInspector(query, retrieved) {
  const turn = state.step < 2 ? 1 : 2;
  $('retrieval-title').textContent = `검색 점수 · ${turn}턴`;
  const actual = callFor('naive', turn)?.retrieved || callFor('fixed', turn)?.retrieved;
  const selected = new Set((actual || retrieved).map(item => item.doc.id));
  $('retrieval-scores').innerHTML = retriever.search(query, scenario.docs.length).map(({ doc, score }) => `<div class="retrieval-item${selected.has(doc.id) ? '' : ' excluded'}"><div class="retrieval-heading"><span>${escapeHtml(doc.title)}</span><small>${selected.has(doc.id) ? '컨텍스트에 포함' : '검색에서 제외'}</small><strong>${score.toFixed(3)}</strong></div><div class="track"><div class="bar" style="width:${Math.min(1, score) * 100}%"></div></div></div>`).join('');
  $('memory-state').textContent = state.job?.memory ? '● 실제 파일 저장됨' : '○ 아직 저장 전';
  $('memory-content').textContent = state.job?.memory || '메모리 모드의 실제 1턴 답변을 기다립니다.';
  $('memory-path').textContent = state.job?.memory_path || '실행 전 · 아직 저장된 파일이 없습니다.';
  $('download').disabled = !state.job?.memory;
  for (const mode of ['naive', 'fixed']) {
    const call = callFor(mode, turn);
    $(mode + '-prompt').textContent = call?.prompt || buildPrompt(query, retrieved, mode === 'fixed' && turn === 2 ? state.job?.memory : null);
  }
  $('prompt-note').textContent = ['naive', 'fixed'].map(mode => `${mode === 'naive' ? '일반' : '메모리'} RAG: ${callFor(mode, turn) ? '실제 호출에 사용한 프롬프트' : '준비된 프롬프트 · 아직 미전송'}`).join(' / ');
}
function renderExecution() {
  $('connection-status').textContent = state.backend ? state.backend.claude_available ? '● Claude CLI 연결 준비 완료' : '● 서버 연결됨 · Claude CLI 설치 필요' : '● 실제 실행 서버에 연결되지 않았습니다';
  const actualModels = [...new Set((state.job?.calls || []).flatMap(call => call.models || []))];
  $('model-info').textContent = state.backend ? `요청 모델: ${state.backend.model}${actualModels.length ? ' · 응답 모델: ' + actualModels.join(', ') : ''}` : 'python3 02-rag-amnesia/server.py --port 8003 로 실행하세요';
  $('run-status').textContent = state.job ? `${state.job.message} · 실행 ${state.job.id.slice(0, 8)}` : state.starting ? '실행 요청을 보내는 중…' : '실행 버튼을 누르면 두 모드 × 두 턴, 총 4회 Claude CLI를 호출합니다.';
  const error = state.error || state.job?.error;
  $('run-error').hidden = !error;
  $('run-error').textContent = error || '';
  $('call-progress').innerHTML = [['naive', 1], ['fixed', 1], ['naive', 2], ['fixed', 2]].map(([mode, turn]) => {
    const call = callFor(mode, turn);
    const status = call?.status || 'pending';
    const label = { complete: '완료', running: '생성 중', error: '실패', cancelled: '취소', pending: '대기' }[status];
    return `<div class="call-chip ${status}"><span>${mode === 'naive' ? '일반' : '메모리'} RAG · ${turn}턴</span><strong>${label}${status === 'complete' ? ` · ${call.duration_seconds.toFixed(1)}초` : ''}</strong></div>`;
  }).join('');
}
function render() {
  const turn = state.step < 2 ? 0 : 1;
  const query = scenario.queries[turn];
  const retrieved = retriever.search(query, state.topK);
  $('query-turn').textContent = `TURN 0${turn + 1}`;
  $('current-query-title').textContent = turn === 0 ? '어제의 일기에서 기준을 정리합니다' : '오늘의 일기를 보고 하루를 평가합니다';
  $('current-query').textContent = query;
  $('step-description').textContent = descriptions[state.step];
  $('top-k').value = String(state.topK);
  $('top-k').disabled = Boolean(running());
  $('reset').disabled = Boolean(running());
  $('play').disabled = !state.backend?.claude_available || Boolean(running());
  $('play').textContent = running() ? 'Claude 실행 중…' : state.job ? '↻ 새로 실제 실행' : '▶ 실제 Claude로 실행';
  $('cancel').disabled = !state.job || state.job.status !== 'running';
  $('next').disabled = state.step === 3;
  document.querySelectorAll('[data-step]').forEach(button => {
    const step = Number(button.dataset.step);
    button.classList.toggle('active', step === state.step);
    button.classList.toggle('complete', step < state.step);
    if (step === state.step) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
  renderModes(retrieved); renderInspector(query, retrieved); renderExecution();
  const naive = callFor('naive', 2), fixed = callFor('fixed', 2);
  const text = naive?.criteria && fixed?.criteria ? `실제 기준 언급 수: 일반 RAG ${Object.values(naive.criteria).filter(Boolean).length}/3, 메모리 RAG ${Object.values(fixed.criteria).filter(Boolean).length}/3. 결과는 매번 달라질 수 있습니다.` : '검색 문맥과 파일 메모리가 실제 Claude 응답에 어떤 차이를 만드는지 확인하세요. 응답 전에는 점수를 표시하지 않습니다.';
  $('takeaway').textContent = text;
}
async function api(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  const type = response.headers.get('content-type') || '';
  if (!type.includes('application/json')) throw new Error('실제 실행 서버가 아닙니다. 정적 http.server 대신 python3 02-rag-amnesia/server.py --port 8003 로 실행하세요.');
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `서버 오류 ${response.status}`);
  return data;
}
function post(path, body = {}) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Run-Token': state.backend.token }, body: JSON.stringify(body) });
}
async function pollRun(id) {
  try {
    const job = await api(`/api/runs/${id}`);
    state.job = job; state.topK = job.top_k; state.error = '';
    if (state.follow) state.step = job.status === 'complete' ? 3 : job.phase === 'turn2' ? 2 : 0;
    render();
    if (job.status === 'running') state.poll = setTimeout(() => pollRun(id), 1000);
  } catch (error) {
    state.error = `실행 상태 조회 실패: ${error.message} 새로고침하면 서버의 마지막 실행에 다시 연결합니다.`;
    render();
  }
}
async function connect() {
  try {
    const info = await api('/api/status');
    if (info.service !== 'rag-amnesia-claude') throw new Error('다른 서버에 연결되었습니다. server.py의 접속 주소를 확인하세요.');
    state.backend = info;
    scenario = { docs: info.docs, queries: info.queries, criteria: info.criteria, systemPrompt: info.system_prompt };
    retriever = new TfidfRetriever(scenario.docs);
    renderCorpus();
    if (info.latest_run) await pollRun(info.latest_run);
  } catch (error) { state.error = error.message; }
  render();
}
$('play').addEventListener('click', async () => {
  if (running()) return;
  clearTimeout(state.poll);
  state.starting = true; state.job = null; state.error = ''; state.follow = true; state.step = 0; render();
  try {
    const { id } = await post('/api/runs', { top_k: state.topK });
    await pollRun(id);
  } catch (error) { state.error = error.message; }
  finally { state.starting = false; render(); }
});
$('cancel').addEventListener('click', async () => {
  try { await post(`/api/runs/${state.job.id}/cancel`); }
  catch (error) { state.error = error.message; render(); }
});
document.querySelectorAll('[data-step]').forEach(button => button.addEventListener('click', () => { state.follow = false; state.step = Number(button.dataset.step); render(); }));
$('next').addEventListener('click', () => { state.follow = false; state.step = Math.min(3, state.step + 1); render(); });
$('reset').addEventListener('click', () => { clearTimeout(state.poll); state.job = null; state.error = ''; state.step = 0; state.topK = 1; state.follow = true; render(); });
$('top-k').addEventListener('change', event => { state.topK = Number(event.target.value); state.job = null; state.error = ''; state.step = 0; render(); });
$('download').addEventListener('click', async () => {
  if (!state.job?.memory) return;
  try {
    const response = await fetch(`/api/runs/${state.job.id}/memory`);
    if (!response.ok) throw new Error('저장된 메모리를 가져오지 못했습니다.');
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = url; link.download = 'criteria.md';
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { state.error = error.message; render(); }
});
function renderCorpus() {
  $('system-prompt').textContent = scenario.systemPrompt;
  $('corpus').replaceChildren();
  for (const [index, doc] of scenario.docs.entries()) {
    const card = document.createElement('article'); card.className = 'corpus-card';
    const body = doc.text.trim().split('\n').slice(1).join('\n').trim();
    const highlighted = escapeHtml(body).replace(/30분 걷기|커피 지출|5천원 이하|야식 금지/g, match => `<mark>${match}</mark>`);
    card.innerHTML = `<h3><span>DOC 0${index + 1}</span>${escapeHtml(doc.title)}</h3><p>${highlighted}</p>`;
    $('corpus').append(card);
  }
}
renderCorpus(); render(); connect();
