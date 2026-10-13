const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const data = require('../data.js');
const { TfidfRetriever, buildPrompt, scoreCriteria } = require('../rag.js');
const MEMORY = '- 테스트 기준: 30분 걷기, 커피 지출 5천원 이하, 야식 금지';
const retriever = new TfidfRetriever(data.docs);

test('TF-IDF scores, corpus, prompts and criteria checks agree with original Python', () => {
  const queries = [...data.queries, '', ' ', '😀', '커피 커피 지출 5000원', '야식\n금지\t걷기'];
  const script = `import json, sys
from rag_amnesia import TfidfRetriever, load_corpus, build_prompt, score_criteria
payload=json.load(sys.stdin)
docs=load_corpus()
r=TfidfRetriever(docs)
rows=[]
for q in payload['queries']:
    results=r.search(q,2)
    rows.append({'scores':[(d['id'],s) for d,s in results], 'prompt':build_prompt(q,results,payload['memory'])})
print(json.dumps({'docs':docs,'rows':rows,'criteria':score_criteria(payload['memory'])}))`;
  const expected = JSON.parse(execFileSync('python3', ['-B', '-c', script], {
    cwd: path.join(__dirname, '..'), encoding: 'utf8',
    input: JSON.stringify({ queries, memory: MEMORY }),
  }));
  assert.deepEqual(data.docs, expected.docs);
  queries.forEach((query, index) => {
    const results = retriever.search(query, 2);
    results.forEach(({ doc, score }, rank) => {
      assert.equal(doc.id, expected.rows[index].scores[rank][0]);
      assert.ok(Math.abs(score - expected.rows[index].scores[rank][1]) < 1e-12);
    });
    assert.equal(buildPrompt(query, results, MEMORY), expected.rows[index].prompt);
  });
  assert.deepEqual(scoreCriteria(MEMORY, data.criteria), expected.criteria);
});

test('Top K changes retrieved context, without inventing any answer', () => {
  const one = retriever.search(data.queries[1], 1);
  const two = retriever.search(data.queries[1], 2);
  assert.equal(retriever.search(data.queries[0], 1)[0].doc.id, '01-diary-2026-07-01.md');
  assert.equal(one[0].doc.id, '02-diary-2026-07-02.md');
  assert.equal(two.length, 2);
  assert.equal(one[0].score, two[0].score);
  assert.ok(!buildPrompt(data.queries[1], one, null).includes('[이전에 확정한'));
  assert.ok(buildPrompt(data.queries[1], one, MEMORY).includes(MEMORY));
  assert.ok(!('EXAMPLES' in require('../rag.js')));
});
