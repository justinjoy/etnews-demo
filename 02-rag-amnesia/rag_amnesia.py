#!/usr/bin/env python3
"""
일반 RAG에서 '단기 기억 상실'을 재현하는 데모.

핵심 주장
---------
일반 RAG는 매 질문이 상태 없는(stateless) "검색 -> 답변"이다.
따라서 1턴에서 정한 판단 기준은
  (1) 어디에도 영구 저장되지 않고,
  (2) 다음 질문의 검색 문맥에 잡히지 않으면
구조적으로 사라진다. 이 덱에서는 슬라이드 15가 이 현상을 그대로 보인다.

이 스크립트는 동일한 2턴 시나리오를 두 가지 모드로 실행한다.
  - naive : 턴 사이에 아무것도 보존하지 않는 순수 RAG  -> 기준이 사라짐
  - fixed : 1턴 기준을 파일로 남기고 2턴에 다시 읽힘(슬라이드 15 모드 B) -> 기준 유지

그리고 각 답변이 '30분 걷기 / 커피 지출 5천원 이하 / 야식 금지' 세 기준을
얼마나 지켰는지 '기준 유지율'로 정량 측정한다.

검색기(임베딩)는 외부 의존성 없는 로컬 TF-IDF(문자 bigram 기반)이고,
생성은 이미 로그인된 Claude Code의 `claude -p` 명령으로 호출한다.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

# --------------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
CORPUS_DIR = HERE / "corpus"
MEMORY_DIR = HERE / "memory"          # fixed 모드에서 기준을 남기는 곳
MEMORY_FILE = MEMORY_DIR / "criteria.md"

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
TOP_K = int(os.environ.get("RAG_TOP_K", "1"))

# 1턴: 어제 일기에서 내일 체크할 기준을 정리 / 2턴: 오늘 일기에 그 기준 적용
TURN1_QUERY = "2026-07-01 일기에서 내일도 지켜야 할 생활 체크 기준을 정리해줘."
TURN2_QUERY = "제품 회고 회의와 편의점 생수가 나온 2026-07-02 일기를 보고 오늘 하루를 평가해줘."

SYSTEM_PROMPT = (
    "너는 개인 일기 회고 어시스턴트다. "
    "반드시 아래 '제공된 컨텍스트'에 있는 정보만 사용해서 답한다. "
    "컨텍스트에 없는 날짜의 일기나 생활 기준은 스스로 지어내지 말고, "
    "주어진 자료 범위 안에서만 구체적으로 안내한다. 답변은 한국어로 한다."
)

# 우리가 추적할 '생활 체크 기준' 세 가지와, 답변에서 그 기준을 찾기 위한 키워드
CRITERIA = {
    "30분 걷기": ["30분 걷", "걷기"],
    "커피 지출 5천원 이하": ["커피", "5천원", "지출"],
    "야식 금지": ["야식"],
}


# --------------------------------------------------------------------------
# 로컬 임베딩 + 검색기 (외부 의존성 없는 TF-IDF, 문자 bigram 기반)
#   - 한국어 형태소 분석 없이도 동작하도록 문자 bigram을 토큰으로 사용
# --------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    text = text.lower()
    words = re.findall(r"[a-z0-9]+", text)              # 영문/숫자 단어
    compact = re.sub(r"\s+", "", text)                  # 공백 제거 후
    bigrams = [compact[i : i + 2] for i in range(len(compact) - 1)]  # 문자 bigram
    return words + bigrams


class TfidfRetriever:
    """아주 작은 TF-IDF 코사인 유사도 검색기."""

    def __init__(self, docs: list[dict]):
        self.docs = docs
        tokenized = [tokenize(d["text"]) for d in docs]

        # 문서 빈도(df) -> idf
        df: Counter = Counter()
        for toks in tokenized:
            for t in set(toks):
                df[t] += 1
        n = len(docs)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}

        # 각 문서의 정규화된 tf-idf 벡터
        self.doc_vecs = [self._vectorize(toks) for toks in tokenized]

    def _vectorize(self, toks: list[str]) -> dict[str, float]:
        tf = Counter(toks)
        vec = {t: (c / len(toks)) * self.idf.get(t, 0.0) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        # 더 작은 쪽을 순회
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(t, 0.0) for t, v in a.items())

    def search(self, query: str, top_k: int) -> list[tuple[dict, float]]:
        qvec = self._vectorize(tokenize(query))
        scored = [
            (doc, self._cosine(qvec, dvec))
            for doc, dvec in zip(self.docs, self.doc_vecs)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


# --------------------------------------------------------------------------
# 코퍼스 로딩 / 평가 유틸
# --------------------------------------------------------------------------
def load_corpus() -> list[dict]:
    docs = []
    for path in sorted(CORPUS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = text.lstrip().splitlines()[0].lstrip("# ").strip()
        docs.append({"id": path.name, "title": title, "text": text})
    if not docs:
        sys.exit(f"코퍼스가 비어 있습니다: {CORPUS_DIR}")
    return docs


def score_criteria(answer: str) -> dict[str, bool]:
    """답변이 각 판단 기준을 언급했는지 검사."""
    return {
        name: any(kw in answer for kw in kws) for name, kws in CRITERIA.items()
    }


def fmt_criteria(result: dict[str, bool]) -> str:
    kept = sum(result.values())
    marks = "  ".join(f"{'✅' if v else '❌'} {k}" for k, v in result.items())
    return f"기준 유지율 {kept}/{len(result)}   |   {marks}"


# --------------------------------------------------------------------------
# 생성 — Claude Code CLI
#   이미 로그인된 Claude Code의 `claude -p`에 위임한다. 별도 API 키는 쓰지 않는다.
# --------------------------------------------------------------------------
def generate(prompt: str, model: str) -> str:
    claude = shutil.which("claude")
    if not claude:
        sys.exit(
            "claude CLI를 찾을 수 없습니다. Claude Code를 설치하고 `claude login` 하세요.\n"
            "  검색 동작만 보려면: python rag_amnesia.py --dry-run"
        )
    proc = subprocess.run(
        [
            claude, "-p", prompt,
            "--model", model,
            "--system-prompt", SYSTEM_PROMPT,
            "--allowed-tools", "",
            "--output-format", "text",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "(출력 없음)"
        sys.exit(f"claude CLI 호출 실패 (exit {proc.returncode}):\n{detail}")
    return proc.stdout.strip()


def build_prompt(query: str, retrieved: list[tuple[dict, float]], memory: str | None) -> str:
    parts = []
    if memory:
        # fixed 모드에서만 들어오는, 이전에 확정한 생활 체크 기준
        parts.append("[이전에 확정한 생활 체크 기준 — 반드시 적용할 것]\n" + memory.strip())
    parts.append("[제공된 컨텍스트]")
    for doc, score in retrieved:
        parts.append(f"\n## {doc['title']} (유사도 {score:.3f})\n{doc['text'].strip()}")
    parts.append("\n[질문]\n" + query)
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 한 턴 실행
# --------------------------------------------------------------------------
def run_turn(label, query, retriever, model, memory=None, dry_run=False):
    retrieved = retriever.search(query, TOP_K)
    titles = ", ".join(f"{d['title']}({s:.2f})" for d, s in retrieved)

    print(f"\n{'─' * 78}")
    print(f"[{label}]")
    print(f"  질문   : {query}")
    print(f"  검색됨 : {titles}")
    if memory:
        print(f"  메모리 : criteria.md 주입됨 ({len(memory)}자)")

    if dry_run:
        print("  (dry-run: 생성 생략)")
        return None, retrieved

    answer = generate(build_prompt(query, retrieved, memory), model)
    print(f"\n  답변:\n{indent(answer)}")
    print(f"\n  → {fmt_criteria(score_criteria(answer))}")
    return answer, retrieved


def indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


# --------------------------------------------------------------------------
# 시나리오
# --------------------------------------------------------------------------
def scenario_naive(retriever, model, dry_run):
    """순수 RAG: 턴 사이에 아무것도 보존하지 않는다."""
    print("\n" + "=" * 78)
    print("  모드 A — naive RAG (상태 없음): 단기 기억 상실 재현")
    print("=" * 78)
    run_turn("1턴 · 어제 일기에서 기준 정리", TURN1_QUERY, retriever, model, dry_run=dry_run)
    a2, _ = run_turn("2턴 · 오늘 일기에 기준 적용", TURN2_QUERY, retriever, model, dry_run=dry_run)
    return a2


def scenario_fixed(retriever, model, dry_run):
    """슬라이드 15 모드 B: 1턴 기준을 파일로 남기고 2턴에 다시 읽힌다."""
    print("\n" + "=" * 78)
    print("  모드 B — fixed (기준을 파일로 외부화): 단기 기억 상실 완화")
    print("=" * 78)
    a1, _ = run_turn("1턴 · 어제 일기에서 기준 정리", TURN1_QUERY, retriever, model, dry_run=dry_run)

    if dry_run:
        # dry-run은 생성을 건너뛰므로 저장할 기준이 없다. 메모리 파일을 건드리지 않는다.
        print(f"\n  (dry-run: {MEMORY_FILE.relative_to(HERE)} 저장 생략)")
        run_turn("2턴 · 오늘 일기에 기준 적용", TURN2_QUERY, retriever, model, dry_run=dry_run)
        return None

    # 1턴의 답변(= 확정된 생활 체크 기준)을 파일로 남긴다.
    MEMORY_DIR.mkdir(exist_ok=True)
    MEMORY_FILE.write_text(a1, encoding="utf-8")
    print(f"\n  💾 1턴 기준을 {MEMORY_FILE.relative_to(HERE)} 에 저장")

    # 2턴: 검색 결과 + 저장해 둔 기준을 함께 주입한다.
    saved = MEMORY_FILE.read_text(encoding="utf-8")
    a2, _ = run_turn("2턴 · 오늘 일기에 기준 적용", TURN2_QUERY, retriever, model, memory=saved, dry_run=dry_run)
    return a2


def main():
    ap = argparse.ArgumentParser(description="일반 RAG의 단기 기억 상실 재현 데모")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"모델 (기본 {DEFAULT_MODEL})")
    ap.add_argument("--mode", choices=["naive", "fixed", "both"], default="both")
    ap.add_argument("--dry-run", action="store_true", help="claude 호출 없이 검색 결과만 확인")
    args = ap.parse_args()

    docs = load_corpus()
    retriever = TfidfRetriever(docs)

    head = f"코퍼스 {len(docs)}개 문서 로드 · top_k={TOP_K} · model={args.model}"
    print(head + ("  [dry-run]" if args.dry_run else " · backend=claude -p"))

    naive_a2 = fixed_a2 = None
    if args.mode in ("naive", "both"):
        naive_a2 = scenario_naive(retriever, args.model, args.dry_run)
    if args.mode in ("fixed", "both"):
        fixed_a2 = scenario_fixed(retriever, args.model, args.dry_run)

    # 핵심 대비 요약
    if args.mode == "both" and not args.dry_run:
        print("\n" + "=" * 78)
        print("  결론: 동일한 2턴 질문, 2턴 답변의 기준 유지율 비교")
        print("=" * 78)
        print(f"  naive RAG : {fmt_criteria(score_criteria(naive_a2 or ''))}")
        print(f"  fixed     : {fmt_criteria(score_criteria(fixed_a2 or ''))}")
        print("\n  → 검색 문맥이 오늘 일기로 바뀌면 naive RAG는 어제 정한 기준을 잃는다(단기 기억 상실).")
        print("    기준을 파일로 외부화해 다시 읽히면 유지된다(슬라이드 15 모드 B).")


if __name__ == "__main__":
    main()
