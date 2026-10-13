(function (root) {
  'use strict';
  const DOCS = [
    '환불은 구매 후 14일 이내에 가능합니다.',
    '배송은 결제 완료 후 보통 2~3일 걸립니다.',
    '회원 등급은 최근 1년 구매 금액으로 결정됩니다.',
  ];
  function embed(text) {
    const chars = Array.from(text.replaceAll(' ', ''));
    const vector = new Map();
    for (let i = 0; i < chars.length - 1; i++) {
      const token = chars[i] + chars[i + 1];
      vector.set(token, (vector.get(token) || 0) + 1);
    }
    return vector;
  }
  const norm = vector => Math.hypot(...vector.values());
  function cosine(a, b) {
    const denominator = norm(a) * norm(b);
    return denominator ? [...a].reduce((sum, [token, count]) => sum + count * (b.get(token) || 0), 0) / denominator : 0;
  }
  // Match Python's Unicode code-point ordering for score ties.
  function compareText(a, b) {
    const left = Array.from(a, char => char.codePointAt(0));
    const right = Array.from(b, char => char.codePointAt(0));
    for (let i = 0; i < Math.min(left.length, right.length); i++) {
      if (left[i] !== right[i]) return left[i] - right[i];
    }
    return left.length - right.length;
  }
  const index = DOCS.map((text, id) => ({ id, text, vector: embed(text) }));
  function retrieve(query) {
    const vector = embed(query);
    return index.map(doc => ({ ...doc, score: cosine(vector, doc.vector) }))
      .sort((a, b) => b.score - a.score || compareText(b.text, a.text));
  }
  const api = { DOCS, embed, norm, cosine, retrieve };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MiniRag = api;
})(globalThis);
