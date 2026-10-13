(function (root) {
  'use strict';
  function tokenize(text) {
    const lower = text.toLowerCase();
    const words = lower.match(/[a-z0-9]+/g) || [];
    const chars = Array.from(lower.replace(/\s+/gu, ''));
    return words.concat(chars.slice(0, -1).map((char, i) => char + chars[i + 1]));
  }
  class TfidfRetriever {
    constructor(docs) {
      this.docs = docs;
      const tokens = docs.map(doc => tokenize(doc.text));
      const df = new Map();
      tokens.forEach(list => new Set(list).forEach(token => df.set(token, (df.get(token) || 0) + 1)));
      this.idf = new Map([...df].map(([token, count]) => [token, Math.log((1 + docs.length) / (1 + count)) + 1]));
      this.vectors = tokens.map(list => this.vectorize(list));
    }
    vectorize(tokens) {
      const tf = new Map();
      tokens.forEach(token => tf.set(token, (tf.get(token) || 0) + 1));
      const vector = new Map([...tf].map(([token, count]) => [token, count / tokens.length * (this.idf.get(token) || 0)]));
      const norm = Math.sqrt([...vector.values()].reduce((sum, value) => sum + value * value, 0)) || 1;
      return new Map([...vector].map(([token, value]) => [token, value / norm]));
    }
    search(query, topK = 1) {
      const q = this.vectorize(tokenize(query));
      return this.docs.map((doc, i) => ({ doc, score: [...q].reduce((sum, [token, value]) => sum + value * (this.vectors[i].get(token) || 0), 0) }))
        .sort((a, b) => b.score - a.score).slice(0, topK);
    }
  }
  function buildPrompt(query, retrieved, memory) {
    const parts = [];
    if (memory) parts.push('[이전에 확정한 생활 체크 기준 — 반드시 적용할 것]\n' + memory.trim());
    parts.push('[제공된 컨텍스트]');
    for (const { doc, score } of retrieved) parts.push(`\n## ${doc.title} (유사도 ${score.toFixed(3)})\n${doc.text.trim()}`);
    parts.push('\n[질문]\n' + query);
    return parts.join('\n');
  }
  function scoreCriteria(answer, criteria) {
    return Object.fromEntries(Object.entries(criteria).map(([name, keywords]) => [name, keywords.some(keyword => answer.includes(keyword))]));
  }
  const api = { tokenize, TfidfRetriever, buildPrompt, scoreCriteria };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AmnesiaRag = api;
})(globalThis);
