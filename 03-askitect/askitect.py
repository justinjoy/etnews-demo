#!/usr/bin/env python3
"""실습 5 / Askitect 루프: 확률 모델(LLM)과 기호 판단(SPARQL+규칙)의 역할 분리.

    자연어 요청 --[LLM 추출]--> 구조화 --[SPARQL 스키마 검증]--> 누락이 있으면
              --[누락 정보 재질의]--> 채움 --(반복)--> 조건 충족 시
              --[SPARQL 규칙 검증: 금액>임계 + 위임]--> 승인권자 --[LLM 답변]

핵심:
  - '직관'(LLM)은 자연어에서 값을 뽑고, 마지막에 답을 문장으로 씁니다.
  - '논리'(SPARQL/규칙)는 누락을 가려내고, 승인권자를 *근거 경로와 함께* 확정합니다.
  - 누락된 필수 정보는 임의로 추론하지 않고 *사용자에게 다시 물어* 채웁니다.
  - 판단의 책임은 LLM이 아니라 지식 그래프에 있습니다 (Extract -> Validate -> Commit).

LLM 단계는 실제 Claude Code(claude CLI, headless `-p`)로 실행됩니다.
이미 로그인된 구독 인증을 쓰므로 API 키가 필요 없습니다. claude CLI가 없으면
규칙 기반 폴백으로 자동 전환되어 기호 판단만으로도 끝까지 실행됩니다.

실행:
    cd demo/ex05_askitect
    pip install -r requirements.txt
    python askitect.py                          # 기본: Claude Code로 LLM 단계 실행
    python askitect.py --no-llm                 # claude code 없이 기호 판단만
    python askitect.py --request "..." --question "..." [--model sonnet]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

from rdflib import Graph, Literal, Namespace, RDF

EX = Namespace("https://example.com/org#")
TTL = os.path.join(os.path.dirname(__file__), "askitect.ttl")
HDR = ("PREFIX ex: <https://example.com/org#>\n"
       "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n")

# 부서 표기(자연어) -> 그래프 URI 로컬명
DEPT_ALIAS = {"마케팅": "Marketing", "marketing": "Marketing"}

# 실제 Claude Code 실행 파일 경로 (없으면 None -> 폴백)
CLAUDE = shutil.which("claude")


# --- 실제 Claude Code(claude CLI) 호출. 미설치/실패 시 None 반환. -------------
def claude_code(prompt: str, model: str | None = None) -> str | None:
    if not CLAUDE:
        return None
    cmd = [CLAUDE, "-p", prompt, "--allowed-tools", "", "--output-format", "text"]
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print(f"  ! Claude Code 오류(rc={r.returncode}): {r.stderr.strip()[:200]}",
                  file=sys.stderr)
            return None
        return r.stdout.strip() or None
    except subprocess.SubprocessError as e:
        print(f"  ! Claude Code 호출 실패: {e}", file=sys.stderr)
        return None


# === 1) Extract: 직관(LLM)으로 자연어 -> 구조화 값 ===========================
def extract(request_text: str, use_llm: bool, model: str | None) -> tuple[dict, str]:
    if use_llm:
        prompt = (
            "다음 결재 요청 문장에서 department(부서명), amount(금액, 원 단위 정수), "
            "purpose(목적)를 추출해 JSON으로만 답하세요. 모르면 null.\n"
            f"문장: {request_text}\n"
            '예: {"department": "Marketing", "amount": 12000000, "purpose": null}'
        )
        raw = claude_code(prompt, model)
        if raw:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    d = json.loads(m.group(0))
                    d["department"] = DEPT_ALIAS.get(str(d.get("department")).lower(),
                                                     d.get("department"))
                    return d, "claude code"
                except json.JSONDecodeError:
                    pass
    # 폴백: 데모 문장을 규칙으로 파싱 (claude 미사용/실패 시에도 실행되도록)
    dept = next((v for k, v in DEPT_ALIAS.items() if k in request_text), None)
    won = re.search(r"([\d,]+)\s*만\s*원", request_text)
    amount = int(won.group(1).replace(",", "")) * 10000 if won else None
    return {"department": dept, "amount": amount, "purpose": None}, "규칙 폴백"


# === 2) Commit: 추출 결과를 지식 그래프에 새 요청으로 적재 ====================
def commit_request(g: Graph, fields: dict, req_id: str = "Request_demo"):
    req = EX[req_id]
    g.add((req, RDF.type, EX.ApprovalRequest))
    if fields.get("department"):
        g.add((req, EX.hasDepartment, EX[fields["department"]]))
    if fields.get("amount") is not None:
        g.add((req, EX.hasAmount, Literal(int(fields["amount"]))))
    if fields.get("purpose"):
        g.add((req, EX.hasPurpose, Literal(fields["purpose"])))
    return req


# === 3) Validate: 스키마(rdfs:domain)로 누락된 필수 속성 찾기 =================
Q_MISSING = HDR + """
SELECT ?prop ?label WHERE {
  ?prop rdfs:domain ex:ApprovalRequest ; rdfs:label ?label .
  FILTER NOT EXISTS { ?req ?prop ?value . }
}"""


def find_missing(g: Graph, req):
    """누락된 필수 속성을 (property URI, 한글 라벨) 목록으로 반환."""
    return [(prop, str(label)) for prop, label in g.query(Q_MISSING, initBindings={"req": req})]


# === 4) Rule check: 금액>임계 규칙 + 위임으로 승인권자 확정 ===================
Q_APPROVER = HDR + """
SELECT ?approver ?dept ?amount ?threshold ?default ?delegate WHERE {
  ?req ex:hasDepartment ?dept ; ex:hasAmount ?amount .
  ?rule ex:forDepartment ?dept ; ex:threshold ?threshold ; ex:defaultApprover ?default .
  FILTER (?amount > ?threshold)
  OPTIONAL { ?default ex:delegatesTo ?delegate . }
  BIND(COALESCE(?delegate, ?default) AS ?approver)
}"""


def decide_approver(g: Graph, req):
    rows = list(g.query(Q_APPROVER, initBindings={"req": req}))
    return rows[0] if rows else None


def local(uri) -> str:
    return str(uri).split("#")[-1] if uri is not None else "-"


# === 5) 직관(LLM)으로 최종 답변 문장화 (폴백 포함) ===========================
def phrase_answer(question: str, approver: str, path: str,
                  use_llm: bool, model: str | None) -> tuple[str, str]:
    if use_llm:
        out = claude_code(
            "아래 근거로만 한국어 한 문장으로 답하세요.\n"
            f"근거: 승인권자={approver}; 근거경로={path}\n질문: {question}", model)
        if out:
            return out, "claude code"
    return f"승인권자는 {approver}입니다. (근거: {path})", "규칙 폴백"


# === 재질의(clarification): 누락 정보를 다시 물어 그래프에 채운다 ============
MAX_ASK_ROUNDS = 3


def fill_value(g: Graph, req, prop, raw):
    """응답 값을 속성 종류에 맞게 그래프에 추가한다."""
    if prop == EX.hasDepartment:
        g.add((req, prop, EX[DEPT_ALIAS.get(str(raw).lower(), str(raw))]))
    elif prop == EX.hasAmount:
        digits = re.sub(r"[^\d]", "", str(raw))
        if digits:
            g.add((req, prop, Literal(int(digits))))
    else:
        g.add((req, prop, Literal(str(raw))))


def ask_for(label: str, prop, answers: dict, use_llm: bool, model: str | None):
    """누락된 한 속성에 대해 사용자에게 다시 물어 값을 얻는다."""
    # 1) --answer 로 미리 제공된 응답을 우선 사용 (비대화형 실행 지원)
    preset = answers.get(label, answers.get(local(prop)))
    if preset is not None:
        return preset, "제공된 응답(--answer)"
    # 2) 물어볼 질문 문장 — Claude Code로 자연스럽게, 없으면 기본형
    q = claude_code(f"결재 요청에 '{label}' 정보가 빠졌습니다. "
                    f"사용자에게 물어볼 짧고 정중한 질문 한 문장만 출력하세요.", model) if use_llm else None
    q = q or f"'{label}' 값을 입력해 주세요"
    # 3) 실제 입력 받기 (대화형/파이프 모두). 입력이 없으면 None
    try:
        return (input(f"   ❓ {q} > ").strip() or None), "사용자 입력"
    except EOFError:
        return None, "응답 없음"


def run(request_text: str, question: str, use_llm: bool = True,
        model: str | None = None, answers: dict | None = None):
    answers = answers or {}
    g = Graph(); g.parse(TTL, format="turtle")

    if use_llm and CLAUDE:
        print(f"[env] Claude Code 사용: {CLAUDE}")
    elif use_llm:
        print("[env] claude CLI를 찾지 못해 규칙 폴백으로 실행합니다.")
    else:
        print("[env] --no-llm: 기호 판단만 실행합니다.")
    print(f"[요청]   {request_text}\n")

    fields, src1 = extract(request_text, use_llm, model)
    print(f"[1·Extract ] ({src1}) → {fields}")

    req = commit_request(g, fields)
    print(f"[2·Commit  ] 그래프에 {local(req)} 적재")

    # 3) Validate + 재질의: 누락 정보가 없어질 때까지 다시 물어 채운다.
    for _ in range(MAX_ASK_ROUNDS):
        missing = find_missing(g, req)
        if not missing:
            print("[3·Validate] (SPARQL) 누락된 필수 속성 → 없음 (모든 조건 충족)")
            break
        print(f"[3·Validate] (SPARQL) 누락 → {[lbl for _, lbl in missing]} · 다시 물어봅니다")
        got = False
        for prop, label in missing:
            val, src = ask_for(label, prop, answers, use_llm, model)
            if val is None:
                print(f"   · {label}: 응답을 받지 못해 보류")
                continue
            fill_value(g, req, prop, val)
            print(f"   · {label} ← \"{val}\"  ({src})")
            got = True
        if not got:
            print("[3·Validate] 추가 정보를 받지 못해 중단합니다.")
            return
    else:
        print("[3·Validate] 재질의 한도를 초과해 중단합니다.")
        return

    # 규칙 검증 전에 '결과 없음'의 의미를 구분한다 (OWA/CWA):
    #   규칙 자체가 없으면 → 판단 보류(데이터 누락), '승인 불필요'로 단정하지 않는다.
    dept = g.value(req, EX.hasDepartment)
    has_rule = any(g.triples((None, EX.forDepartment, dept)))
    row = decide_approver(g, req)
    if row is None:
        if not has_rule:
            print(f"[4·Rule    ] (SPARQL) '{local(dept)}' 부서의 결재 규칙이 그래프에 없습니다 "
                  f"— 판단 보류(규칙 등록 필요). '결과 없음'을 '승인 불필요'로 단정하지 않습니다.")
        else:
            print("[4·Rule    ] (SPARQL) 규칙은 있으나 금액이 임계값 이하 — 별도 승인 불필요")
        return
    delegated = row.delegate is not None
    path = (f"{local(row.dept)} 부서 · 금액 {int(row.amount):,} > 임계 {int(row.threshold):,} "
            f"→ 기본 승인자 {local(row.default)}"
            + (f" → 휴가/위임 → {local(row.delegate)}" if delegated else ""))
    print(f"[4·Rule    ] (SPARQL) 승인권자 = {local(row.approver)}  "
          f"({'위임 적용' if delegated else '본인 승인'})")
    print(f"             근거 경로: {path}\n")

    answer, src5 = phrase_answer(question, local(row.approver), path, use_llm, model)
    print(f"[5·Answer  ] ({src5}) {answer}")


def main():
    ap = argparse.ArgumentParser(description="Askitect: LLM(직관) + SPARQL/규칙(논리) 결재 승인 루프")
    ap.add_argument("--request", default="마케팅팀에서 1,200만 원짜리 집행 건을 올렸습니다.",
                    help="자연어 결재 요청")
    ap.add_argument("--question", default="이 결재 건의 승인권자는 누구인가요?",
                    help="최종 질문")
    ap.add_argument("--no-llm", action="store_true",
                    help="Claude Code 없이 기호 판단(SPARQL/규칙)만 실행")
    ap.add_argument("--model", default=None,
                    help="claude --model 값 (예: sonnet, opus). 생략 시 기본 모델")
    ap.add_argument("--answer", action="append", default=[], metavar="라벨=값",
                    help="누락 정보 응답을 미리 제공 (예: --answer 목적=마케팅캠페인). 반복 가능")
    a = ap.parse_args()
    answers = dict(item.split("=", 1) for item in a.answer if "=" in item)
    run(a.request, a.question, use_llm=not a.no_llm, model=a.model, answers=answers)


if __name__ == "__main__":
    main()
