const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const { DOCS, embed, cosine, retrieve } = require('../rag.js');

test('browser retrieval matches the original Python implementation', () => {
  const queries = ['환불 배송 회원', '환불은 언제까지 가능한가요?', '배송은 며칠 걸리나요?', '회원 등급은 어떻게 결정되나요?', '오늘 날씨 어때요?', '', ' ', '가', '환불환불환불', '환불\t배송\n회원', '😀환불😀', ...DOCS];
  const script = 'import json, sys\nfrom mini_rag import retrieve\nprint(json.dumps([retrieve(q) for q in json.load(sys.stdin)]))';
  const expected = JSON.parse(execFileSync('python3', ['-B', '-c', script], {
    cwd: path.join(__dirname, '..'), input: JSON.stringify(queries), encoding: 'utf8'
  }));
  queries.forEach((query, i) => {
    retrieve(query).forEach((actual, j) => {
      assert.equal(actual.text, expected[i][j][1], query);
      assert.ok(Math.abs(actual.score - expected[i][j][0]) < 1e-12, query);
    });
  });
});
test('frequency vectors preserve repeats and Unicode characters', () => {
  assert.deepEqual([...embed('가 가가😀')], [['가가', 2], ['가😀', 1]]);
  assert.equal(cosine(embed(''), embed('환불')), 0);
  for (const doc of DOCS) assert.ok(Math.abs(retrieve(doc)[0].score - 1) < 1e-12);
  assert.ok(retrieve('오늘 날씨 어때요?').every(doc => doc.score === 0));
});
