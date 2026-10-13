'use strict';
const { embed, norm, retrieve } = MiniRag;
const colors = ['#3867ec', '#dc753b', '#16877f'];
const names = ['환불 안내', '배송 안내', '회원 등급'];
const $ = id => document.getElementById(id);
let ranked = [];
let selectedId = 0;
let queryVector = new Map();
let selectionPinned = false;
const format = score => score.toFixed(3);
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function renderRankings() {
  $('rankings').replaceChildren();
  ranked.forEach((doc, rank) => {
    const card = element('button', `rank-card${doc.id === selectedId ? ' selected' : ''}`);
    card.type = 'button';
    card.style.setProperty('--doc-color', colors[doc.id]);
    card.setAttribute('aria-pressed', String(doc.id === selectedId));
    card.setAttribute('aria-label', `${rank + 1}위, ${names[doc.id]}, 유사도 ${format(doc.score)}. 계산 과정 보기`);
    const heading = element('div', 'rank-heading');
    heading.append(element('span', 'doc-dot'), element('span', 'doc-name', `D${doc.id + 1} · ${names[doc.id]}`), element('span', 'rank-number', `${rank + 1}위`), element('span', 'score', format(doc.score)));
    const track = element('div', 'track');
    const bar = element('div', 'bar');
    bar.style.width = `${Math.min(1, doc.score) * 100}%`;
    track.append(bar);
    card.append(heading, element('p', '', doc.text), track);
    card.addEventListener('click', () => selectDocument(doc.id));
    $('rankings').append(card);
  });
  const top = ranked[0];
  const result = $('result');
  result.replaceChildren();
  result.classList.toggle('empty', top.score === 0);
  if (top.score === 0) {
    result.append(element('strong', '', '검색 근거 없음'), element('span', '', '공통 bigram이 없습니다. 질문의 표현을 바꿔 보세요.'));
  } else {
    const tied = ranked.filter(doc => Math.abs(doc.score - top.score) < 1e-12).length;
    result.append(element('strong', '', tied > 1 ? `최고 유사도 동점 ${tied}개 · 원본의 정렬 순서로 선택` : '↳ 가장 가까운 근거'), element('span', '', top.text));
  }
}
function renderChart() {
  if (!norm(queryVector)) {
    $('angle-chart').innerHTML = '<svg viewBox="0 0 470 300" role="img" aria-label="질문 벡터가 없으므로 각도를 계산할 수 없습니다"><text x="235" y="140" text-anchor="middle" fill="#697487" font-size="13">문자 2개 이상을 입력해 주세요.</text><text x="235" y="166" text-anchor="middle" fill="#8a94a5" font-size="11">길이가 0인 벡터의 각도는 정의되지 않습니다.</text></svg>';
    return;
  }
  const ox = 53, oy = 251, radius = 192;
  let svg = `<svg viewBox="0 0 470 300" role="img" aria-label="질문과 각 문서의 코사인 각도"><title>각도가 작을수록 유사도가 높습니다</title><defs><marker id="query-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#25334d"/></marker></defs>`;
  [64, 128, 192].forEach(r => { svg += `<path d="M ${ox} ${oy-r} A ${r} ${r} 0 0 1 ${ox+r} ${oy}" fill="none" stroke="#e8edf4" stroke-dasharray="3 5"/>`; });
  svg += `<path d="M ${ox} 34 V ${oy} H 271" fill="none" stroke="#e1e6ef"/><text x="${ox}" y="22" fill="#98a1b0" font-size="10" text-anchor="middle">90° · 유사도 0</text><text x="250" y="279" fill="#98a1b0" font-size="10" text-anchor="middle">0° · 유사도 1</text>`;
  ranked.forEach((doc, index) => {
    const theta = Math.acos(Math.max(-1, Math.min(1, doc.score)));
    const x = ox + radius * Math.cos(theta), y = oy - radius * Math.sin(theta);
    const arcRadius = 45 + index * 17;
    const ax = ox + arcRadius * Math.cos(theta), ay = oy - arcRadius * Math.sin(theta);
    const labelY = 60 + index * 55;
    svg += `<path d="M ${ox+arcRadius} ${oy} A ${arcRadius} ${arcRadius} 0 0 0 ${ax} ${ay}" fill="none" stroke="${colors[doc.id]}" stroke-opacity=".35"/><path d="M ${ox} ${oy} L ${x} ${y}" stroke="${colors[doc.id]}" stroke-width="${doc.id === selectedId ? 3 : 2}" opacity=".8"/><circle cx="${x}" cy="${y}" r="${6-index}" fill="${colors[doc.id]}"/><path d="M ${x+8} ${y} L 279 ${labelY} H 297" fill="none" stroke="${colors[doc.id]}" stroke-opacity=".35" stroke-dasharray="3 3"/><text x="307" y="${labelY-2}" fill="${colors[doc.id]}" font-size="12" font-weight="650">D${doc.id+1} ${names[doc.id]}</text><text x="307" y="${labelY+15}" fill="#8490a4" font-size="10">${(theta*180/Math.PI).toFixed(1)}° · cos θ = ${format(doc.score)}</text>`;
  });
  svg += `<path d="M ${ox} ${oy} H ${ox+radius}" stroke="#25334d" stroke-width="2.5" marker-end="url(#query-arrow)"/><circle cx="${ox}" cy="${oy}" r="4" fill="#25334d"/><text x="${ox-13}" y="${oy+19}" fill="#8490a4" font-size="10">0</text><text x="${ox+radius+16}" y="${oy+4}" fill="#25334d" font-size="12" font-weight="650">질문 벡터</text></svg>`;
  $('angle-chart').innerHTML = svg;
}
function renderTokens(target, vector, other) {
  target.replaceChildren();
  if (!vector.size) target.append(element('span', 'empty-tokens', '표시할 bigram이 없습니다.'));
  for (const [token, count] of vector) {
    const chip = element('span', `token${other.has(token) ? ' shared' : ''}`, token);
    chip.title = `${token}: ${count}회${other.has(token) ? ' · 공통 bigram' : ''}`;
    if (count > 1) chip.append(element('small', '', `×${count}`));
    target.append(chip);
  }
}
function renderDetails() {
  const doc = ranked.find(doc => doc.id === selectedId);
  $('document-select').value = String(selectedId);
  $('selected-document').textContent = doc.text;
  $('document-token-title').textContent = `D${selectedId+1} 문서를 쪼개면`;
  $('query-token-count').textContent = `${queryVector.size}차원 사용`;
  $('doc-token-count').textContent = `${doc.vector.size}차원 사용`;
  renderTokens($('query-tokens'), queryVector, doc.vector);
  renderTokens($('document-tokens'), doc.vector, queryVector);
  const shared = [...queryVector].filter(([token]) => doc.vector.has(token));
  const dot = shared.reduce((sum, [token, count]) => sum + count * doc.vector.get(token), 0);
  $('overlap-count').textContent = `공통 ${shared.length}개`;
  $('formula-value').replaceChildren();
  if (norm(queryVector) === 0) {
    $('formula-value').append(element('span', '', '빈 벡터 → 원본의 반환값'), element('strong', '', '0.000'));
  } else {
    $('formula-value').append(element('span', '', `${dot} / (${norm(queryVector).toFixed(2)} × ${norm(doc.vector).toFixed(2)}) =`), element('strong', '', format(doc.score)));
  }
}
function selectDocument(id) {
  selectedId = id;
  selectionPinned = true;
  // Keep the focused ranking button in place for keyboard interaction.
  document.querySelectorAll('.rank-card').forEach((card, i) => {
    card.classList.toggle('selected', ranked[i].id === id);
    card.setAttribute('aria-pressed', String(ranked[i].id === id));
  });
  renderChart();
  renderDetails();
}
function update() {
  const query = $('query').value;
  queryVector = embed(query);
  ranked = retrieve(query);
  if (!selectionPinned) selectedId = ranked[0].id;
  renderRankings(); renderChart(); renderDetails();
  const top = ranked[0];
  $('query-status').textContent = !queryVector.size ? '공백을 제외하고 문자 2개 이상을 입력해 주세요.' : top.score === 0 ? '3개 문서 비교 완료 · 겹치는 문자 묶음이 없습니다.' : `3개 문서 비교 완료 · 최고 유사도 ${format(top.score)} · ${names[top.id]}`;
  document.querySelectorAll('[data-query]').forEach(button => {
    const active = button.dataset.query === query;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}
$('query').addEventListener('input', update);
$('query-form').addEventListener('submit', event => { event.preventDefault(); selectionPinned = false; update(); });
$('document-select').addEventListener('change', event => selectDocument(Number(event.target.value)));
document.querySelectorAll('[data-query]').forEach(button => button.addEventListener('click', () => {
  $('query').value = button.dataset.query;
  selectionPinned = false;
  update();
}));
update();
