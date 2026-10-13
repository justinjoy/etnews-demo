'use strict';
const $ = id => document.getElementById(id);
const esc = text => String(text ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const state = { backend: null, job: null, busy: false, error: '', poll: null, questionsKey: '' };
const phases = ['extract', 'commit', 'validate', 'clarify', 'rule', 'answer'];
const phaseNames = { extract: 'Claude가 요청을 추출하고 있습니다.', commit: '추출값을 RDF 그래프에 적재합니다.', validate: 'SPARQL로 필수 속성을 검사합니다.', clarify: 'Claude가 누락 정보를 물을 질문을 작성합니다.', rule: 'SPARQL로 금액 규칙과 위임 관계를 확인합니다.', answer: '확정된 근거로 Claude가 최종 답변을 작성합니다.' };
const deptLabels = { Marketing: '마케팅팀', HR: '인사팀', Engineering: '개발팀' };
const personLabels = { Director: '본부장', DelegatedTeamLead: '위임 팀장' };
const active = () => state.busy || ['running', 'waiting'].includes(state.job?.status);
const won = value => Number(value).toLocaleString('ko-KR') + '원';
async function api(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('실행 서버에 연결되지 않았습니다. README.txt의 server.py 실행 방법을 확인하세요.');
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `서버 오류 ${response.status}`);
  return data;
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Run-Token': state.backend.token }, body: JSON.stringify(body) });

function renderFields() {
  const fields = state.job?.fields;
  let count = 0;
  $('fields').innerHTML = [['department', '담당 부서'], ['amount', '금액'], ['purpose', '목적']].map(([key, label]) => {
    const value = fields?.[key];
    const present = value !== null && value !== undefined;
    if (present) count++;
    const source = state.job?.field_sources[key];
    const display = present ? key === 'amount' ? won(value) : key === 'department' ? `${deptLabels[value] || value} · ${value}` : value : fields ? '필수 정보 누락' : '추출 대기';
    return `<div class="field ${present ? source === 'user' ? 'user' : '' : 'missing'}"><span class="field-label">${label}</span><span class="field-value">${esc(display)}</span><span class="field-meta">${present ? source === 'user' ? '사용자가 직접 입력' : '실제 Claude 응답에서 추출' : fields ? 'SPARQL로 누락 여부 검사' : '실행 전 · 값 없음'}</span></div>`;
  }).join('');
  $('field-count').textContent = `${count} / 3`;
  $('issues').textContent = (state.job?.issues || []).join(' ');
}

function renderGraph() {
  const triples = state.job?.graph || state.backend?.policy || [];
  if (!triples.length) { $('graph').innerHTML = '<p class="graph-placeholder">서버에서 원본 정책 그래프를 불러옵니다.</p>'; return; }
  const reqDept = triples.find(t => t.subject === 'Request_web' && t.predicate === 'hasDepartment')?.object;
  const resolved = state.job?.decision?.kind === 'resolved';
  const delegated = Boolean(state.job?.decision?.delegate);
  const nodes = new Map();
  function addNode(id, x, y, label, sub, kind = 'policy') {
    if (!nodes.has(id)) nodes.set(id, { id, x, y, label, sub, kind });
  }
  for (const triple of triples) {
    const { subject, predicate, object, literal } = triple;
    if (subject === 'Request_web') addNode(subject, 25, 40, '이번 결재 요청', 'Request_web', 'request');
    else if (subject === 'RuleMarketing') addNode(subject, 535, 40, '마케팅 승인 규칙', subject);
    else if (subject === 'Director') addNode(subject, 535, 185, '본부장', subject);
    if (!literal) {
      if (object === 'Director') addNode(object, 535, 185, '본부장', object);
      else if (object === 'DelegatedTeamLead') addNode(object, 535, 330, '위임 팀장', object);
      else addNode(object, 280, reqDept && object !== reqDept ? 330 : 40, deptLabels[object] || object, object, subject === 'Request_web' ? 'request' : 'policy');
    } else {
      const isAmount = predicate === 'hasAmount' || predicate === 'threshold';
      addNode(`${subject}:${predicate}`, subject === 'RuleMarketing' ? 280 : 25, predicate === 'hasPurpose' ? 330 : 185, isAmount ? won(object) : object, { hasAmount: 'hasAmount · 요청 금액', threshold: 'threshold · 초과 기준', hasPurpose: 'hasPurpose · 목적' }[predicate], subject === 'Request_web' ? 'request' : 'policy');
    }
  }
  const edgeLabels = { hasDepartment: 'hasDepartment', hasAmount: 'hasAmount', hasPurpose: 'hasPurpose', forDepartment: 'forDepartment', defaultApprover: 'defaultApprover', delegatesTo: 'delegatesTo', threshold: 'threshold' };
  let svg = '<svg viewBox="0 0 760 430" role="img" aria-label="실제 RDF 요청과 승인 규칙의 관계"><title>요청, 부서, 금액 규칙, 기본 승인자, 위임 관계</title><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#a3b5cc"/></marker><marker id="arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#398c77"/></marker></defs>';
  for (const triple of triples) {
    const source = nodes.get(triple.subject);
    const target = nodes.get(triple.literal ? `${triple.subject}:${triple.predicate}` : triple.object);
    if (!source || !target) continue;
    const vertical = source.x === target.x;
    let x1, y1, x2, y2, path, lx, ly;
    if (triple.predicate === 'threshold') {
      path = `M${source.x+92},${source.y+65} V135 H${target.x+92} V${target.y}`;
      lx = 427; ly = 128;
    } else if (triple.predicate === 'hasPurpose') {
      path = `M${source.x},${source.y+45} H10 V${target.y+32} H${target.x}`;
      lx = 28; ly = target.y - 13;
    } else if (vertical) {
      x1 = source.x + (triple.predicate === 'hasPurpose' ? 27 : 92); y1 = source.y + 65;
      x2 = target.x + (triple.predicate === 'hasPurpose' ? 27 : 92); y2 = target.y;
      path = `M${x1},${y1} L${x2},${y2}`; lx = x1 + 10; ly = (y1 + y2) / 2;
    } else {
      const right = target.x > source.x;
      x1 = source.x + (right ? 185 : 0); y1 = source.y + 32;
      x2 = target.x + (right ? 0 : 185); y2 = target.y + 32;
      path = `M${x1},${y1} C${(x1+x2)/2},${y1} ${(x1+x2)/2},${y2} ${x2},${y2}`;
      lx = (x1+x2)/2; ly = Math.min(source.y,target.y) - 14;
    }
    const highlight = resolved && (triple.predicate !== 'delegatesTo' || delegated) && triple.predicate !== 'hasPurpose';
    svg += `<path d="${path}" fill="none" stroke="${highlight ? '#398c77' : '#b1bfd0'}" stroke-width="${highlight ? 2.7 : 1.7}" marker-end="url(#${highlight ? 'arrow-active' : 'arrow'})"/><text x="${lx}" y="${ly}" text-anchor="${vertical ? 'start' : 'middle'}" font-size="10" fill="#91a1b5">${edgeLabels[triple.predicate]}</text>`;
  }
  for (const node of nodes.values()) {
    const chosen = resolved && (node.id === state.job.decision.approver || node.id === 'RuleMarketing');
    const color = chosen ? '#38856d' : node.kind === 'request' ? '#8066b8' : '#6685ab';
    const fill = chosen ? '#ecf7f0' : node.kind === 'request' ? '#f6f2fc' : '#f2f6fc';
    const short = Array.from(node.label).length > 20 ? Array.from(node.label).slice(0, 19).join('') + '…' : node.label;
    svg += `<g><title>${esc(node.label)} · ${esc(node.sub)}</title><rect x="${node.x}" y="${node.y}" width="185" height="65" rx="10" fill="${fill}" stroke="${chosen ? '#82bba2' : '#d6dfec'}"/><circle cx="${node.x+15}" cy="${node.y+22}" r="3" fill="${color}"/><text x="${node.x+26}" y="${node.y+27}" fill="${color}" font-size="13" font-weight="650">${esc(short)}</text><text x="${node.x+15}" y="${node.y+48}" fill="#91a0b4" font-size="10">${esc(node.sub)}</text></g>`;
  }
  if (!state.job?.fields) svg += '<text x="28" y="118" fill="#a1adbc" font-size="13">실행하면 요청이 추가됩니다.</text>';
  if (state.job?.fields && !state.job.fields.purpose) svg += '<text x="25" y="368" fill="#b6944e" font-size="13">목적 누락 · 사용자 입력 필요</text>';
  svg += '</svg>';
  $('graph').innerHTML = svg;
  $('graph-badge').textContent = state.job?.fields ? `${triples.length}개 관계` : '원본 정책 미리보기';
  $('graph-description').textContent = state.job?.fields ? '현재 요청과 실제 정책 트리플을 표시합니다. 녹색은 규칙으로 확정한 승인 경로입니다.' : '원본 정책을 먼저 확인하세요. 실행하면 요청의 부서·금액·목적이 추가됩니다.';
}

function renderQuestions() {
  const visible = state.job?.status === 'waiting';
  $('clarification').hidden = !visible;
  if (!visible) { state.questionsKey = ''; return; }
  const key = state.job.id + ':' + state.job.round;
  if (state.questionsKey !== key) {
    state.questionsKey = key;
    $('questions').innerHTML = state.job.questions.map(item => `<label for="input-${esc(item.property)}">${esc(item.question)}</label><input type="text" id="input-${esc(item.property)}" name="${esc(item.property)}" required maxlength="${item.property === 'hasAmount' ? 20 : 1000}" ${item.property === 'hasAmount' ? 'inputmode="numeric"' : ''} placeholder="${item.property === 'hasAmount' ? '원 단위 숫자 · 예: 40000000' : item.property === 'hasDepartment' ? '예: 마케팅팀, 인사팀, 개발팀' : '예: 신제품 출시 마케팅 캠페인'}">`).join('');
  }
  $('submit-answers').disabled = state.busy;
}

function renderDecision() {
  const decision = state.job?.decision;
  $('decision-badge').textContent = decision ? decision.kind === 'resolved' ? 'SPARQL VERIFIED' : 'SPARQL RESULT' : 'WAITING';
  if (!decision) {
    $('decision').innerHTML = `<div class="empty-state">${state.job?.status === 'waiting' ? '필수 정보가 빠져 있습니다. 추가 정보를 입력하면 SPARQL 검증을 계속합니다.' : '아직 승인권자를 결정하지 않았습니다. 요청 추출과 필수 정보 검증이 먼저 진행됩니다.'}</div>`;
  } else if (decision.kind === 'resolved') {
    $('decision').innerHTML = `<div class="decision-top"><span class="symbol">✓</span><div><small>그래프가 확정한 승인권자</small><strong>${esc(personLabels[decision.approver] || decision.approver)} · ${esc(decision.approver)}</strong></div></div><div class="evidence"><span>${esc(decision.department)}</span>→<span>${won(decision.amount)} &gt; ${won(decision.threshold)}</span>→<span>${esc(decision.default)}</span>${decision.delegate ? `→<span>위임 · ${esc(decision.delegate)}</span>` : ''}</div><p class="decision-note">금액 조건과 위임 관계를 SPARQL로 검증했습니다. 실제 결재를 실행한 것은 아닙니다.</p>`;
  } else if (decision.kind === 'no_rule') {
    $('decision').innerHTML = `<div class="decision-top warning"><span class="symbol">?</span><div><small>${esc(decision.department)} · 규칙 데이터 없음</small><strong>판단 보류 · 규칙 등록 필요</strong></div></div><p class="decision-note">조회 결과가 없다는 이유로 승인 불필요라고 단정하지 않습니다. 이 부서의 규칙이 그래프에 없습니다.</p>`;
  } else {
    $('decision').innerHTML = `<div class="decision-top neutral"><span class="symbol">≤</span><div><small>${won(decision.amount)} ≤ ${won(decision.threshold)}</small><strong>초과금액 승인 규칙 미적용</strong></div></div><p class="decision-note">규칙은 존재하지만 금액이 임계값을 초과하지 않습니다. 이 데모 그래프의 규칙 범위에서만 판단합니다.</p>`;
  }
  $('answer').innerHTML = state.job?.answer ? `<div class="answer-box"><span>✦ 실제 CLAUDE 답변</span><p>${esc(state.job.answer)}</p></div>` : decision?.kind === 'resolved' ? '<p class="description">승인권자는 그래프가 확정했습니다. Claude의 최종 문장 응답은 아직 없습니다.</p>' : decision ? '<p class="description">원본과 같이 이 분기는 규칙 결과로 종료하며, 최종 답변용 LLM을 추가 호출하지 않습니다.</p>' : '';
}

function renderRecords() {
  const job = state.job;
  const status = { waiting: '추가 정보를 입력해 주세요. 추측으로 채우지 않고 기다립니다.', complete: '실행 완료 · 실제 그래프 판단과 모델 응답을 확인하세요.', error: '실행 실패 · 아래의 완료된 호출 기록은 유지됩니다.', cancelled: '실행이 취소되었습니다.' }[job?.status];
  $('run-status').textContent = job ? status || phaseNames[job.phase] : '실행 버튼을 누르면 실제 Claude CLI를 호출합니다.';
  $('run-id').textContent = job ? job.id.slice(0, 8) : '실행 전';
  $('timeline').innerHTML = (job?.events || []).map(event => `<li class="${esc(event.kind)}"><time>${new Date(event.time * 1000).toLocaleTimeString('ko-KR', { hour12: false })}</time>${esc(event.text)}</li>`).join('');
  const calls = job?.calls || [];
  $('call-count').textContent = `${calls.length}회`;
  $('calls').innerHTML = calls.length ? calls.map((call, i) => `<article class="call-record"><h3>${i+1}. ${esc(call.stage)}<span class="status-tag">${esc({ running: '생성 중', complete: '완료', error: '실패', cancelled: '취소' }[call.status] || call.status)}</span></h3><p>${call.duration_seconds !== undefined ? `${call.duration_seconds.toFixed(1)}초 · ` : ''}${esc((call.models || []).join(', '))}${call.session_id ? ` · 세션 ${esc(call.session_id)}` : ''}</p><label>실제 전송 프롬프트</label><pre>${esc(call.prompt)}</pre><label>실제 응답 원문</label><pre class="response">${esc(call.answer || '아직 응답이 없습니다.')}</pre></article>`).join('') : '<p class="description" style="margin-top:16px">아직 호출하지 않았습니다. 예시 응답은 표시하지 않습니다.</p>';
  $('download-json').disabled = !job;
  $('download-graph').disabled = !job?.graph;
}

function render() {
  const job = state.job;
  const models = [...new Set((job?.calls || []).flatMap(call => call.models || []))];
  $('connection').textContent = state.backend ? state.backend.claude_available ? '● 실제 Claude CLI · RDFLib 연결 준비 완료' : '● 서버 연결됨 · Claude CLI 설치 필요' : '● 실행 서버 연결 필요';
  $('model').textContent = models.length ? `응답 모델: ${models.join(', ')}` : state.backend ? `요청 모델: ${state.backend.model}` : '';
  $('error').hidden = !(state.error || job?.error);
  $('error').textContent = state.error || job?.error || '';
  $('run').disabled = !state.backend?.claude_available || Boolean(active());
  $('run').textContent = active() ? job?.status === 'waiting' ? '추가 정보 입력 대기 중' : 'Claude · SPARQL 처리 중…' : job ? '✦ 새로 실제 실행' : '✦ 실제 Claude로 실행';
  $('cancel').disabled = !job || !['running', 'waiting'].includes(job.status) || state.busy;
  for (const id of ['request', 'question', 'delegation']) $(id).disabled = Boolean(active());
  document.querySelectorAll('[data-preset]').forEach(button => { button.disabled = Boolean(active()); });
  const index = phases.indexOf(job?.phase);
  document.querySelectorAll('[data-phase]').forEach(node => {
    const at = phases.indexOf(node.dataset.phase);
    node.classList.toggle('active', at === index && job?.status !== 'complete');
    const clarificationHappened = (job?.calls || []).some(call => call.stage.startsWith('재질의'));
    node.classList.toggle('done', Boolean(job) && at <= index && (at !== 3 || clarificationHappened) && (at < index || job.status === 'complete'));
    if (at === index) node.setAttribute('aria-current', 'step'); else node.removeAttribute('aria-current');
  });
  renderFields(); renderGraph(); renderQuestions(); renderDecision(); renderRecords();
}
async function poll(rid) {
  try {
    state.job = await api(`/api/runs/${rid}`); state.error = '';
    render();
    if (state.job.status === 'running') state.poll = setTimeout(() => poll(rid), 1000);
  } catch (error) { state.error = `상태 조회 실패: ${error.message} 새로고침하면 마지막 실행에 다시 연결합니다.`; render(); }
}
async function connect() {
  try {
    const info = await api('/api/status');
    if (info.service !== 'askitect-claude') throw new Error('다른 서버에 연결되었습니다. 03-askitect/server.py로 실행하세요.');
    state.backend = info;
    $('missing-query').textContent = info.queries.missing;
    $('approver-query').textContent = info.queries.approver;
    $('ontology').textContent = info.ontology;
    if (info.latest_run) {
      await poll(info.latest_run);
      if (state.job) { $('request').value = state.job.request; $('question').value = state.job.question; $('delegation').checked = state.job.delegation; }
    }
  } catch (error) { state.error = error.message; }
  render();
}
$('request-form').addEventListener('submit', async event => {
  event.preventDefault(); if (active()) return;
  clearTimeout(state.poll); state.busy = true; state.job = null; state.error = ''; render();
  try {
    const { id } = await post('/api/runs', { request: $('request').value, question: $('question').value, delegation: $('delegation').checked });
    await poll(id);
  } catch (error) { state.error = error.message; }
  finally { state.busy = false; render(); }
});
$('answers-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy) return;
  state.busy = true; state.error = ''; render();
  try {
    const answers = Object.fromEntries(new FormData($('answers-form')));
    await post(`/api/runs/${state.job.id}/answers`, { answers });
    await poll(state.job.id);
  } catch (error) { state.error = error.message; }
  finally { state.busy = false; render(); }
});
$('cancel').addEventListener('click', async () => {
  try { await post(`/api/runs/${state.job.id}/cancel`, {}); clearTimeout(state.poll); await poll(state.job.id); }
  catch (error) { state.error = error.message; render(); }
});
const presets = {
  delegated: '마케팅팀에서 4,000만 원짜리 집행 건을 올렸습니다.',
  below: '마케팅팀에서 1,200만 원짜리 집행 건을 올렸습니다.',
  unknown: '인사팀에서 4,000만 원짜리 집행 건을 올렸습니다.',
};
document.querySelectorAll('[data-preset]').forEach(button => button.addEventListener('click', () => {
  clearTimeout(state.poll); state.job = null; state.error = ''; $('request').value = presets[button.dataset.preset]; render();
}));
async function download(kind) {
  if (!state.job) return;
  try {
    const response = await fetch(`/api/runs/${state.job.id}${kind === 'graph' ? '/graph' : ''}`);
    if (!response.ok) throw new Error('실행 자료 다운로드에 실패했습니다.');
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = url; link.download = kind === 'graph' ? 'graph.ttl' : 'result.json';
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { state.error = error.message; render(); }
}
$('download-json').addEventListener('click', () => download('json'));
$('download-graph').addEventListener('click', () => download('graph'));
render(); connect();
