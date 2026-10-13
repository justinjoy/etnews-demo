#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
-i JSON 입력(질문 해석 결과)을 받아:
  1) EV 충전소 추천 SPARQL 생성
  2) Fuseki SPARQL endpoint에 질의
  3) 결과를 근거와 함께 설명 형태로 출력
"""

import json
import argparse
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import requests


# ---------------------------------------------------------------------
# Dataclass definitions (질문 해석 결과 구조)
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
# JSON → QueryInterpretation
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
            min_available=int(cons.get("min_available", 1)),
            max_busy_ratio=cons.get("max_busy_ratio"),
        ),
    )


# ---------------------------------------------------------------------
# SPARQL 생성
# ---------------------------------------------------------------------

def build_ev_charger_sparql(interp: QueryInterpretation) -> str:
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
# 결과 → 설명(근거) 생성
# ---------------------------------------------------------------------

def extract_rows(result_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """SPARQL JSON 결과를 편하게 다룰 수 있는 리스트로 변환."""
    vars_ = result_json.get("head", {}).get("vars", [])
    rows = []
    for b in result_json.get("results", {}).get("bindings", []):
        row = {}
        for v in vars_:
            if v in b:
                row[v] = b[v]["value"]
            else:
                row[v] = None
        rows.append(row)
    return rows


def format_station_explanation(row: Dict[str, Any],
                               interp: QueryInterpretation) -> str:
    """한 개의 추천 결과에 대한 근거 설명 문자열 생성."""
    name = row.get("stationLabel") or "(이름 없음)"
    addr = row.get("addr") or "(주소 정보 없음)"
    total = row.get("total") or "0"
    busy = row.get("busy") or "0"
    available = row.get("available") or "0"
    busy_ratio = row.get("busyRatio") or "1"
    max_output = row.get("maxOutput") or "0"

    try:
        total_i = int(float(total))
    except Exception:
        total_i = 0
    try:
        busy_i = int(float(busy))
    except Exception:
        busy_i = 0
    try:
        avail_i = int(float(available))
    except Exception:
        avail_i = 0
    try:
        busy_r = float(busy_ratio)
    except Exception:
        busy_r = 1.0
    try:
        max_out = float(max_output)
    except Exception:
        max_out = 0.0

    busy_pct = busy_r * 100.0
    threshold_pct = (
        interp.constraints.max_busy_ratio * 100.0
        if interp.constraints.max_busy_ratio is not None else None
    )

    # 근거 조각들 조합
    reasons = []
    reasons.append(f"전체 {total_i}대 중 충전중 {busy_i}대, 사용가능 {avail_i}대")
    if threshold_pct is not None:
        reasons.append(
            f"충전중 비율 {busy_pct:.1f}% (허용 상한 {threshold_pct:.0f}% 이하 조건 충족)"
            if busy_r <= interp.constraints.max_busy_ratio
            else f"충전중 비율 {busy_pct:.1f}% (허용 상한 {threshold_pct:.0f}% 초과)"
        )
    else:
        reasons.append(f"충전중 비율 {busy_pct:.1f}% (혼잡도 상한 미설정)")

    if avail_i >= interp.constraints.min_available:
        reasons.append(f"사용가능 충전기 {avail_i}대 ≥ 최소 요구 {interp.constraints.min_available}대")

    if max_out > 0:
        reasons.append(f"최대 출력 {max_out:g} kW")

    reasons_str = " / ".join(reasons)

    return f"- {name} ({addr})\n    · {reasons_str}"


def explain_results(interp: QueryInterpretation,
                    rows: List[Dict[str, Any]]) -> str:
    """전체 결과에 대한 한국어 설명 텍스트 생성."""
    lines = []

    # 1. 질문 요약
    lines.append("【질문 요약】")
    q = interp.original_close if hasattr(interp, "original_close") else interp.original_question
    lines.append(f"- 원문 질문: {q or '(원문 미제공)'}")

    area_desc = interp.area.name or interp.area.area_code or "미지정"
    lines.append(f"- 대상 지역: {area_desc}")
    if interp.time.iso_datetime or interp.time.time_of_day:
        lines.append(f"- 관심 시각: {interp.time.iso_datetime or interp.time.time_of_day}")
        lines.append("- 시각 조건은 적용하지 않았습니다. 충전기 상태의 시간별 예측은 지원하지 않습니다.")
    else:
        lines.append("- 관심 시각: (별도 지정 없음, 조회 데이터 기준)")

    lines.append("")
    lines.append("【적용된 의사결정 규칙(필터)】")
    lines.append(f"- EV 충전소 필수 여부: {'예' if interp.constraints.need_ev_charger else '아니오'}")
    lines.append(f"- 최소 사용가능 충전기 개수: {interp.constraints.min_available}대 이상")
    if interp.constraints.max_busy_ratio is not None:
        lines.append(f"- 혼잡도 조건: 충전중 비율 ≤ {interp.constraints.max_busy_ratio * 100:.0f}%")
    else:
        lines.append("- 혼잡도 조건: 설정되지 않음")

    lines.append("")
    if not rows:
        lines.append("【결과】")
        lines.append("- 조건을 만족하는 충전소가 없습니다.")
        return "\n".join(lines)

    # 2. 결과 요약
    lines.append("【추천 결과 요약】")
    lines.append(f"- 조건을 만족하는 충전소 수: {len(rows)}개")

    # 3. 각 충전소별 상세 근거
    lines.append("")
    lines.append("【후보별 근거】")
    for row in rows:
        lines.append(format_station_explanation(row, interp))

    return "\n".join(lines)


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="EV 충전소 추천 + 근거 설명 생성기")
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

    # 1) 해석 결과 로드
    interp = load_interpretation(args.input)

    # 2) SPARQL 생성
    sparql = build_ev_charger_sparql(interp)
    print("=== 생성된 SPARQL ===")
    print(sparql)
    print("\n=== RDF 질의 실행 ===")
    if args.ttl:
        print(f"저장된 데이터 기준: {args.ttl} (현재 상태가 아닙니다.)")

    # 3) SPARQL 실행
    result_json = run_sparql(sparql, args.endpoint, ttl_path=args.ttl)

    # 4) 결과 파싱
    rows = extract_rows(result_json)

    # 5) 설명 생성
    explanation = explain_results(interp, rows)

    # 6) 출력
    print("\n=== 의사결정 지원 설명 ===")
    print(explanation)

    # 필요하다면 원본 SPARQL 결과 JSON도 함께 저장/출력 가능
    # print("\n=== 원본 SPARQL 결과(JSON) ===")
    # print(json.dumps(result_json, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
