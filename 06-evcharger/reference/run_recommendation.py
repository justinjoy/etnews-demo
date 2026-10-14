#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
-i JSON 입력을 받아 EV 충전소 추천을 위한 SPARQL을 생성하고,
Fuseki SPARQL endpoint에 질의하는 실행기.

JSON 입력은 질문 해석 단계(LLM → JSON)가 완료된 형태로 가정:
{
  "intent": "ev_charger_recommendation",
  "area": { "name": "강남역", "area_code": "POI014" },
  "time": { "time_of_day": "20:00", "iso_datetime": null },
  "constraints": {
    "ev_charger": {
      "need_ev_charger": true,
      "min_available": 1,
      "max_busy_ratio": 0.5
    }
  },
  "original_question": "오후 8시..."
}
"""

import json
import argparse
from dataclasses import dataclass
from typing import Optional, Dict, Any
import requests


# ---------------------------------------------------------------------
# Dataclass definitions
# ---------------------------------------------------------------------

@dataclass
class EVChargerConstraints:
    need_ev_charger: bool = True
    min_available: int = 1
    max_busy_ratio: Optional[float] = None


@dataclass
class AreaSelection:
    name: Optional[str] = None
    area_code: Optional[str] = None


@dataclass
class TimeSlot:
    time_of_day: Optional[str] = None
    iso_datetime: Optional[str] = None


@dataclass
class QueryInterpretation:
    original_question: str
    intent: str
    area: AreaSelection
    time: TimeSlot
    constraints: EVChargerConstraints


# ---------------------------------------------------------------------
# Convert JSON → QueryInterpretation
# ---------------------------------------------------------------------

def load_interpretation(json_path: str) -> QueryInterpretation:
    with open(json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    area = raw.get("area", {})
    time = raw.get("time", {})
    constraints = raw.get("constraints") or {}
    cons = constraints.get("ev_charger", constraints) or {}

    return QueryInterpretation(
        original_question=raw.get("original_question", ""),
        intent=raw.get("intent", "ev_charger_recommendation"),
        area=AreaSelection(
            name=area.get("name"),
            area_code=area.get("area_code"),
        ),
        time=TimeSlot(
            time_of_day=time.get("time_of_day"),
            iso_datetime=time.get("iso_datetime"),
        ),
        constraints=EVChargerConstraints(
            need_ev_charger=cons.get("need_ev_charger", True),
            min_available=cons.get("min_available", 1),
            max_busy_ratio=cons.get("max_busy_ratio"),
        ),
    )


# ---------------------------------------------------------------------
# Build SPARQL from QueryInterpretation
# ---------------------------------------------------------------------

def build_ev_charger_sparql(interp: QueryInterpretation) -> str:
    """QueryInterpretation → SPARQL 쿼리로 변환"""

    area_code = interp.area.area_code or "POI014"
    min_available = interp.constraints.min_available
    max_busy_ratio = interp.constraints.max_busy_ratio

    busy_filter = ""
    if max_busy_ratio is not None:
        busy_filter = f"  FILTER(?busyRatio <= {max_busy_ratio})\n"

    query = f"""
PREFIX ex:  <http://k.fc/onto/city#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT
  ?station
  ?stationLabel
  ?addr
  ?total
  ?busy
  ?available
  ?busyRatio
  ?maxOutput
WHERE {{
  {{
    SELECT
      ?station
      ?stationLabel
      ?addr
      (COUNT(?charger) AS ?total)
      (SUM(IF(?status = "충전중", 1, 0)) AS ?busy)
      (SUM(IF(?status = "사용가능", 1, 0)) AS ?available)
      (MAX(?out) AS ?maxOutput)
    WHERE {{
      ?area a ex:Area ;
            ex:areaCode "{area_code}" ;
            ex:hasEVChargerStation ?station .

      OPTIONAL {{ ?station rdfs:label ?stationLabel . }}
      OPTIONAL {{ ?station ex:stat_addr ?addr . }}

      ?station ex:hasEVCharger ?charger .
      ?charger ex:charger_stat ?status .
      OPTIONAL {{ ?charger ex:output ?outStr . }}
      BIND(
        IF(BOUND(?outStr) && ?outStr != "",
           xsd:decimal(?outStr),
           0.0
        ) AS ?out
      )
    }}
    GROUP BY ?station ?stationLabel ?addr
  }}

  BIND(
    IF(?total > 0,
       xsd:decimal(?busy) / xsd:decimal(?total),
       1.0
    ) AS ?busyRatio
  )

  FILTER(?available >= {min_available})
{busy_filter}}}
ORDER BY ?busyRatio DESC(?maxOutput) ?stationLabel
""".strip()

    return query


# ---------------------------------------------------------------------
# SPARQL 실행
# ---------------------------------------------------------------------

def run_sparql(query: str, endpoint: Optional[str] = None, timeout: int = 10,
               ttl_path: Optional[str] = None) -> Dict[str, Any]:
    if ttl_path:
        from rdflib import Graph
        graph = Graph().parse(ttl_path, format="turtle")
        return json.loads(graph.query(query).serialize(format="json"))
    if not endpoint:
        raise ValueError("endpoint 또는 ttl_path가 필요합니다.")
    params = {
        "query": query,
        "format": "application/sparql-results+json",
    }
    res = requests.get(endpoint, params=params, timeout=timeout)
    res.raise_for_status()
    return res.json()


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="EV 충전소 추천 SPARQL 실행기")
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="질문 해석 결과 JSON 파일 경로"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ttl", help="조회할 Turtle 파일 (예: ch08/gangnam.ttl)")
    source.add_argument(
        "--endpoint",
        help="Fuseki SPARQL endpoint (예: http://localhost:3030/seoul/sparql)"
    )

    args = parser.parse_args()

    # (1) 질문 해석 JSON 로드
    interp = load_interpretation(args.input)

    # (2) SPARQL 생성
    sparql = build_ev_charger_sparql(interp)
    print("\n=== Generated SPARQL ===")
    print(sparql)

    # (3) Fuseki 질의 실행
    print("\n=== Querying RDF ===")
    if args.ttl:
        print(f"저장된 데이터 기준: {args.ttl} (현재 상태나 지정 시각의 예측이 아닙니다.)")
    result = run_sparql(sparql, args.endpoint, ttl_path=args.ttl)

    # (4) 결과 출력(JSON)
    print("\n=== SPARQL RESULT ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
