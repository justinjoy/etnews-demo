import os
import json
import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from typing import Tuple, Optional
from rdflib import Graph, Literal

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate

# 설정
FUSEKI_SPARQL_ENDPOINT = os.environ.get("SPARQL_ENDPOINT", "http://localhost:3030/fc/sparql")
OLLAMA_MODEL_NAME = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

def get_llm(temperature: float = 0.0, json_mode: bool = False) -> ChatOllama:
    options = {"format": {
        "type": "object",
        "properties": {"region": {"type": "string"}, "as_of_now": {"type": "boolean"}},
        "required": ["region", "as_of_now"],
        "additionalProperties": False,
    }} if json_mode else {}
    return ChatOllama(model=OLLAMA_MODEL_NAME, base_url=OLLAMA_BASE_URL,
                      temperature=temperature, reasoning=False, **options)

def extract_area_name_and_now_flag(question: str) -> Tuple[str, bool]:
    llm = get_llm(json_mode=True)
    template = """
당신은 사용자의 자연어 질문에서 다음 정보를 JSON으로 추출합니다:
- region : 한국어로 된 장소명 (예: "강남역") 또는 빈 문자열
- as_of_now : 사용자가 "지금", "현재", "최근" 등의 키워드를 사용했는지 여부 (true 또는 false)
- 저장된 기록, 과거 관측 시각을 묻는 질문은 as_of_now=false입니다.

질문: "{question}"
출력 형식: {{"region": "<지역명>", "as_of_now": <true 또는 false>}}
"""
    prompt = ChatPromptTemplate.from_template(template)
    messages = prompt.format_messages(question=question)
    resp = llm.invoke(messages)
    try:
        data = json.loads(resp.content.strip())
        region = data.get("region", "").strip()
        as_of_now = bool(data.get("as_of_now", False))
    except json.JSONDecodeError:
        region = ""
        as_of_now = False
    return region, as_of_now

def query_status_for_area(area_name: str, recent_only: bool = False,
                          ttl_path: Optional[str] = None) -> dict:
    ns = "http://k.fc/onto/citydata#"
    area_literal = Literal(area_name).n3()

    traffic_query = f"""
PREFIX ns1: <{ns}>
PREFIX city: <http://k.fc/onto/city#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?areaName ?time ?speed ?index
WHERE {{
  {{
  ?area a city:Area ;
        rdfs:label ?areaName ;
        city:hasRoadTrafficSummary ?status .
  ?status a city:RoadTrafficSummary ;
          city:observedAt ?time ;
          city:roadTrafficSpeed ?speed ;
          city:roadTrafficIndex ?index .
  }} UNION {{
  ?area a ns1:Area ;
        ns1:name ?areaName ;
        ns1:hasStatus ?status .

  ?status a ns1:TrafficStatus ;
          ns1:observationTime ?time ;
          ns1:speed ?speed ;
          ns1:trafficIndex ?index .
  }}
  FILTER(STR(?areaName) = {area_literal})
}}
ORDER BY DESC(?time)
LIMIT 1
"""

    population_query = f"""
PREFIX ns1: <{ns}>
PREFIX city: <http://k.fc/onto/city#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?areaName ?time ?congestion ?pmin ?pmax
WHERE {{
  {{
  ?area a city:Area ;
        rdfs:label ?areaName ;
        city:hasPopulationStatus ?status .
  ?status a city:PopulationStatus ;
          city:observedAt ?time ;
          city:congestionLevel ?congestion ;
          city:area_ppltn_min ?pmin ;
          city:area_ppltn_max ?pmax .
  }} UNION {{
  ?area a ns1:Area ;
        ns1:name ?areaName ;
        ns1:hasStatus ?status .

  ?status a ns1:PopulationStatus ;
          ns1:observationTime ?time ;
          ns1:congestionLevel ?congestion ;
          ns1:populationMin ?pmin ;
          ns1:populationMax ?pmax .
  }}
  FILTER(STR(?areaName) = {area_literal})
}}
ORDER BY DESC(?time)
LIMIT 1
"""

    graph = Graph().parse(ttl_path, format="turtle") if ttl_path else None

    def run_sparql(q: str) -> dict:
        if graph is not None:
            return json.loads(graph.query(q).serialize(format="json"))
        resp = requests.get(
            FUSEKI_SPARQL_ENDPOINT,
            params={"query": q, "format": "application/sparql-results+json"},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    result = {"area_name": area_name, "traffic": None, "population": None}

    # 교통 상태 조회
    traffic_data = run_sparql(traffic_query)
    t_bindings = traffic_data.get("results", {}).get("bindings", [])
    if t_bindings:
        row = t_bindings[0]
        result["traffic"] = {
            "observationTime": row["time"]["value"],
            "speed": float(row["speed"]["value"]),
            "trafficIndex": row["index"]["value"],
        }

    # 인구 혼잡 조회
    pop_data = run_sparql(population_query)
    p_bindings = pop_data.get("results", {}).get("bindings", [])
    if p_bindings:
        row = p_bindings[0]
        result["population"] = {
            "observationTime": row["time"]["value"],
            "congestionLevel": row["congestion"]["value"],
            "populationMin": int(float(row["pmin"]["value"])),
            "populationMax": int(float(row["pmax"]["value"])),
        }

    if recent_only:
        # ch08의 시간대 없는 서울 관측 시각은 KST로 해석합니다.
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        for kind in ("traffic", "population"):
            if result[kind]:
                observed = datetime.fromisoformat(result[kind]["observationTime"])
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=ZoneInfo("Asia/Seoul"))
                if not now - timedelta(minutes=15) <= observed <= now:
                    result[kind] = None
    return result

def build_context_from_ontology(result: dict) -> str:
    area = result.get("area_name", "알 수 없는 지역")
    lines = [f"[온톨로지 기반 도시데이터 요약] 지역: {area}"]

    traffic = result.get("traffic")
    if traffic:
        lines.append(
            f"- 교통 상태(TrafficStatus): {traffic['observationTime']} 기준, 속도 {traffic['speed']} km/h, 지수 {traffic['trafficIndex']}"
        )
    else:
        lines.append("- 교통 상태(TrafficStatus): 최근 데이터 없음")

    population = result.get("population")
    if population:
        lines.append(
            f"- 인구 혼잡(PopulationStatus): {population['observationTime']} 기준, 혼잡도 {population['congestionLevel']}, 추정 인구 {population['populationMin']}~{population['populationMax']}명"
        )
    else:
        lines.append("- 인구 혼잡(PopulationStatus): 최근 데이터 없음")

    return "\n".join(lines)

def answer_question_with_ontology_rag(question: str, ttl_path: Optional[str] = None) -> str:
    area_name, as_of_now = extract_area_name_and_now_flag(question)
    if not area_name:
        return "질문에서 조회할 지역명을 인식하지 못했습니다."

    retrieved = query_status_for_area(area_name, recent_only=as_of_now, ttl_path=ttl_path)
    # 최근 데이터가 없는 경우
    if not retrieved["traffic"] and not retrieved["population"] and as_of_now:
        latest = query_status_for_area(area_name, ttl_path=ttl_path)
        return (f"‘{area_name}’에 대해 최근 15분 이내 데이터가 없습니다. "
                "아래는 저장된 마지막 기록이며 현재 상태가 아닙니다.\n"
                + build_context_from_ontology(latest))

    context = build_context_from_ontology(retrieved)

    llm = get_llm()
    template = """
당신은 서울시 실시간 도시데이터 온톨로지를 기반으로 답변하는 도우미입니다.
아래는 SPARQL로 조회한 저장된 관측 기록입니다.
반드시 관측 날짜와 시각을 명시하고, 과거 기록을 현재 상태로 표현하지 마세요.
조회되지 않은 항목은 추측하지 마세요.

[사용자 질문]
{question}

[조회 결과 요약]
{context}

위 정보를 바탕으로 답변하세요.
"""
    prompt = ChatPromptTemplate.from_template(template)
    messages = prompt.format_messages(question=question, context=context)
    resp = llm.invoke(messages)
    return resp.content.strip()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="교통·인구 온톨로지 RAG (ch08 TTL 지원)")
    parser.add_argument("--question", help="질문 (생략하면 대화형 입력)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--ttl", help="조회할 Turtle 파일 (예: ch08/gangnam.ttl)")
    source.add_argument("--endpoint", default=FUSEKI_SPARQL_ENDPOINT)
    parser.add_argument("--ollama-model", default=OLLAMA_MODEL_NAME)
    args = parser.parse_args()
    FUSEKI_SPARQL_ENDPOINT = args.endpoint
    OLLAMA_MODEL_NAME = args.ollama_model
    user_question = args.question or input("질문을 입력하세요: ")
    answer = answer_question_with_ontology_rag(user_question, ttl_path=args.ttl)
    print("답변:", answer)
