05-dss — SPARQL과 RULE로 설명하는 방문 자제 권고
================================================

아래 실행 명령은 etnews-demo 저장소 루트 기준 상대 경로입니다.

1. 설치와 실행
--------------
Python 3.10 이상이 필요합니다. 최초 한 번:

  python3 -m venv 05-dss/.venv
  05-dss/.venv/bin/python -m pip install -r 05-dss/requirements.txt

실행:

  05-dss/.venv/bin/python 05-dss/server.py

브라우저 접속:

  http://localhost:8006/05-dss/

포트가 사용 중일 때:

  05-dss/.venv/bin/python 05-dss/server.py --port 8016

이 경우 http://localhost:8016/05-dss/ 로 접속합니다.
종료는 서버를 실행한 터미널에서 Ctrl+C를 누릅니다.

Python 안에서 RDFLib가 SPARQL SELECT / CONSTRUCT를 실행합니다.
Jena, Fuseki, Ollama, Claude CLI, API 키, Node.js가 필요하지 않습니다.
LLM을 호출하는 데모가 아닙니다. 규칙으로 권고를 도출하고, 조회된
근거값을 넣는 문장 템플릿으로 설명합니다.
HTML을 직접 열거나 python -m http.server로 열면 분석 API가 작동하지 않습니다.
반드시 server.py로 실행하세요. 웹 서버는 로컬 127.0.0.1에만 바인딩됩니다.

2. 기본 시연
------------
(1) 예시 관측의 혼잡도 ‘붐빔’, R1/R2 모두 켜짐을 확인합니다.
(2) ‘SPARQL + RULE 실행’을 누릅니다.
(3) 관측 조회 1행, R1 추가 2트리플, R2 추가 6트리플을 확인합니다.
(4) 방문 자제 권고와 다음 근거 설명을 확인합니다.

  강남역 관측이 ‘붐빔’으로 기록되어 있기 때문에,
  혼잡한 장소에는 방문 자제를 안내한다는 예시 규칙에 따라,
  방문 자제 권고를 제시합니다.

그래프는 입력 사실(파랑), 규칙으로 얻은 혼잡 분류(보라),
권고(초록)를 구분합니다. 도출되지 않은 부분은 회색 점선으로 표시합니다.
각 규칙 아래에서 실제 실행 질의와 생성된 트리플을 펼쳐 볼 수 있습니다.
하단에는 입력 RDF, 도출 RDF, 권고 SPARQL, 실제 조회 결과가 있습니다.
실행 기록 JSON과 근거 그래프 TTL을 다운로드할 수 있습니다.

3. 결과를 비교하는 시연
----------------------
‘비교해 보기’ 버튼은 설정을 바꾸고 바로 서버에서 다시 실행합니다.
입력 폼/규칙 체크박스만 바꾼 경우에는 실행 버튼을 눌러야 반영됩니다.
화면은 설정 변경 시 이전 실행 결과임을 표시합니다.

- 붐빔 + R1/R2 켜기: 혼잡 관측 → 방문 자제 권고 1개
- 여유/보통/약간 붐빔: R1 조건 불일치 → 권고 미도출
- 붐빔 + R2 끄기: 혼잡 분류만 도출 → 권고 미도출
- 붐빔 + R1 끄기: R2의 혼잡 전제 없음 → 권고 미도출
- 혼잡도 미기록: 조건을 확인할 근거 없음 → 권고 미도출
- ch08 원본 관측: 저장된 값 ‘여유’ → 권고 미도출

권고가 도출되지 않는 것을 ‘방문 권장’ 또는 ‘안전함’으로 바꾸지 않습니다.
‘약간 붐빔’도 이 예시의 정확한 조건 ‘붐빔’과는 다릅니다.
새 실행마다 작업 그래프를 생성하므로 이전 권고가 남지 않습니다.

4. 실제 동작 구조
-----------------
브라우저 POST /api/evaluate
  → 선택된 RDF 데이터에 관측 SPARQL SELECT 실행
  → 강남역의 최신 관측 1건을 작업 그래프로 선택
  → R1 CONSTRUCT 실행, 도출 트리플을 작업 그래프에 추가
  → R2 CONSTRUCT 실행, 도출 트리플을 작업 그래프에 추가
  → 최종 SPARQL SELECT로 권고 및 관측 근거 조회
  → 실제 결과로 그래프·표·설명 렌더링

R1: PopulationStatus의 congestionLevel이 정확히 ‘붐빔’이면
    그 관측을 dss:CrowdedObservation으로 분류합니다.
    어떤 규칙으로 분류했는지 classifiedBy R1도 기록합니다.

R2: 선택된 장소에 CrowdedObservation이 있으면
    Recommendation을 만들고 aboutArea, basedOn, action AvoidVisit,
    derivedBy R2, observedAt을 함께 기록합니다.

규칙 파일(.rq)이 실제 실행되는 규칙입니다.
Python의 if level == '붐빔' 분기로 권고를 미리 채우지 않습니다.
R1 → R2의 순서가 명시된 2단계 전방 추론입니다.
임의의 규칙에 대한 범용 고정점 추론기나 OWL 추론기는 아닙니다.
TBox는 관측 구조를 정의하며 자체적으로 방문 자제를 추론하지 않습니다.
이 데모의 방문 자제는 명시한 R1·R2에서만 도출됩니다.

최신 PopulationStatus 관측만 선택하고 그 관측의 트리플만 규칙에 전달합니다.
PopulationForecast는 입력 관측으로 쓰지 않습니다.
강남역의 코드 POI014에 고정된 예제이며 실시간 조회/예측을 수행하지 않습니다.
시각에 시간대가 없는 원본 값은 서울 관측 시각으로 표시하며 변환하지 않습니다.

5. ch07 / ch08 결합과 데이터 출처
--------------------------------
원본 저장소는 수정하지 않았습니다.

ch08에서 가져온 것:
- gangnam.ttl → data/gangnam-source.ttl (원본 그대로 복사)
- tbox/02-tbox.ttl → data/city-tbox.ttl (원본 그대로 복사)
- Area → hasPopulationStatus → PopulationStatus 구조
- congestionLevel, observedAt, areaCode 및 rdfs:label

ch07에서 가져온 설계:
- rag/rag_traffic.py의 장소별 관측 SPARQL 및 최신 관측 선택
- dss/run_dss.py의 질의 → 조건 적용 → 결과와 근거 설명 흐름
원본은 reference/ch07/에 참고용으로 보존했습니다.
EV 충전소 필터 예제를 혼잡 관측 기반 방문 안내로 재구성했습니다.
참고용 원본 스크립트는 웹 서버에서 실행/임포트하지 않습니다.

중요한 데이터 차이:
원본 ch08/gangnam.ttl의 강남역 PopulationStatus는
2025-11-21 01:10:00 기준 ‘여유’입니다.
‘붐빔’은 같은 파일의 13:00 예측(PopulationForecast)에 포함되어 있습니다.
이 예측을 실제 관측이라고 바꾸어 사용하지 않았습니다.

요청한 ‘붐빔 관측’은 data/demo-observation.ttl에 별도로 작성했습니다.
관측 IRI도 DemoPopulation_POI014로 분리했습니다.
기본 시각은 원본 스냅샷 값을 빌린 예시이며 새 실측 사실이 아닙니다.
화면에서 ‘예시 관측’과 ‘ch08 원본 관측’을 선택할 수 있습니다.
혼잡 분류 및 방문 자제 규칙은 이 시연에 추가한 예시 정책이며
서울시의 공식 방문 지침이 아닙니다.

6. 파일 구성 (05-dss/ 안의 상대 경로)
------------------------------------
server.py                       로컬 HTTP 서버
engine.py                       데이터 조회 및 규칙 실행
index.html/styles.css/app.js     웹 화면과 시각화
queries/01-observation.rq        최신 강남역 관측 SELECT
rules/01-crowded.rq              R1: 혼잡 분류 CONSTRUCT
rules/02-recommend.rq            R2: 방문 자제 권고 CONSTRUCT
queries/02-decision.rq           권고와 근거 SELECT
data/gangnam-source.ttl          ch08 원본 스냅샷
data/city-tbox.ttl               ch08 원본 TBox
data/demo-observation.ttl        별도의 붐빔 예시 관측
data/policy.ttl                  추가한 규칙/권고 개념의 이름과 설명
reference/ch07/                  참고용 원본 코드
tests/test_dss.py               규칙/질의/HTTP 검증

서버가 살아 있는 동안 마지막 실행을 새로고침 후 복원합니다.
서버 재시작 시 실행 상태는 초기화됩니다. 보존하려면 JSON/TTL을 다운로드하세요.
원본/예시 파일이나 규칙 파일을 수정한 경우 서버를 재시작해야 로드됩니다.

7. 테스트
---------
저장소 루트에서:

  05-dss/.venv/bin/python -m unittest discover -s 05-dss/tests -v

실제 RDFLib 질의로 다음을 검사합니다.
붐빔 권고, 비혼잡 값, 누락, R1/R2 비활성화, 원본과 예측 구분,
원본 불변성, 실행 간 격리, 최신 관측 선택, 규칙 변경 시 결과 변화,
HTTP API에서 실제 규칙 실행 및 요청 보호.

문제 해결:
- 연결 실패: 위 Python 서버가 실행 중인지, 접속 포트가 같은지 확인합니다.
- 포트 충돌: --port로 다른 포트를 선택합니다.
- 설정 변경 후 결과 그대로: ‘SPARQL + RULE 실행’을 누릅니다.
- 원본 관측에서 권고 없음: 원본은 ‘여유’이므로 정상 동작입니다.
- 모듈 오류: .venv/bin/python으로 실행하고 requirements.txt를 설치합니다.
