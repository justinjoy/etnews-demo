04-cctv — CCTV 영상에서 지식 그래프로
====================================

이 문서의 경로는 etnews-demo 저장소 루트 기준 상대 경로입니다.
원본 cctv 데모의 데이터와 5장 스냅샷을 가져와 웹 UI로 다시 구성했습니다.
원본 폴더는 수정하지 않았습니다.

1. 준비
-------
Python 3.10 이상, Ollama, 이미지 입력을 지원하는 모델이 필요합니다.
Java, Apache Jena, Fuseki, Streamlit, Node.js는 필요하지 않습니다.
Python 내부의 RDFLib가 Turtle을 읽고 SPARQL을 실행합니다.

저장소 루트에서 최초 한 번:

  python3 -m venv 04-cctv/.venv
  04-cctv/.venv/bin/python -m pip install -r 04-cctv/requirements.txt

Ollama가 실행 중인지 확인:

  ollama list

연결되지 않으면 별도 터미널에서 실행하고 유지:

  ollama serve

기본 모델은 원본과 같은 qwen3.8:27b-mlx입니다.
이미 설치되어 있으면 추가 다운로드가 필요 없습니다.
이 모델은 MLX를 지원하는 Ollama 및 Apple Silicon 환경이 필요합니다.
다른 환경에서는 설치된 vision 모델을 --model로 지정하세요.
가벼운 대안 예시(처음에는 모델 다운로드가 필요함):

  ollama pull qwen2.5vl:3b

2. 실행과 접속
--------------
저장소 루트에서:

  04-cctv/.venv/bin/python 04-cctv/server.py

브라우저에서:

  http://localhost:8005/04-cctv/

다른 이미지 모델로 실행하려면:

  04-cctv/.venv/bin/python 04-cctv/server.py --model qwen2.5vl:3b

8005 포트가 사용 중이면:

  04-cctv/.venv/bin/python 04-cctv/server.py --port 8015

이때 접속 주소는 http://localhost:8015/04-cctv/ 입니다.
Ollama 주소는 --ollama-host http://localhost:11434 로 지정할 수 있습니다.
모델 요청의 읽기 타임아웃은 기본 600초이며 --timeout 900 등으로 변경합니다.
웹 서버는 127.0.0.1에서만 열립니다.
index.html을 더블클릭하거나 python -m http.server로 열면 분석 API가 작동하지 않습니다.
반드시 위 server.py를 실행하세요.

3. 시연 순서
------------
(1) 상단의 Ollama 연결 상태와 실제 사용할 모델 이름을 확인합니다.
(2) 다섯 썸네일을 눌러 입력 영상을 확인합니다. 분석은 항상 5장 전부 사용합니다.
(3) 분석 설정에서 모드를 선택합니다.
    - 이미지 → JSON: 실제 vision 모델 호출 1회로 3가지 관측을 추출합니다.
    - 이미지 → 설명 → JSON:
      실제 모델로 영문 설명을 생성하고 한국어로 번역합니다(2회).
      설명을 읽고 '이 영문 설명으로 JSON → RDF 변환'을 누르면
      영문 원문에서 실제 모델이 JSON을 추출합니다(추가 1회).
      한국어 번역은 표시용이며 구조화의 입력으로 사용하지 않습니다.
(4) '실제 영상 모델로 분석'을 누릅니다. 모델에 따라 수 분 걸릴 수 있습니다.
(5) 날씨·보행 혼잡도·교통량 카드, SOSA 그래프, SPARQL 결과 3행을 확인합니다.
    막대는 분류의 순서를 나타내며 확률 또는 신뢰도 점수가 아닙니다.
(6) 화면 하단의 실제 Ollama 호출 기록에서 프롬프트·이미지 수·원문 응답·
    소요 시간·토큰 수를 확인합니다. SPARQL과 생성 Turtle도 펼쳐볼 수 있습니다.
(7) 실행 기록 JSON 및 그래프 TTL을 내려받을 수 있습니다.

실제 호출 흐름:
브라우저 → Python HTTP API → Ollama /api/generate → 실제 모델 응답
→ JSON 형식/열거값 검사 → SOSA Observation 3개 생성 → RDFLib SPARQL → 화면

잘못된 JSON, 잘못된 분류값, 모델 연결 실패를 임의의 정상 결과로 대체하지 않습니다.
Unknown은 모델이 명시적으로 반환했을 때만 '판단 불가'로 표시합니다.
번역만 실패하면 영문 설명을 보존하고 변환을 계속할 수 있습니다.
원본의 MLX 모델용 logprobs:true 및 think:false 설정을 유지했습니다.

4. 데이터와 결과
----------------
data/cctv.ttl            원본 CCTV 위치 그래프 (79,482 트리플 / 13,247 센서)
data/OpenDataCCTV.csv    원본 위치 CSV
data/csv_to_cctv_ttl.py  원본 CSV → Turtle 변환 스크립트
shots/shot_01~05.jpg     원본 데모 이미지 5장
engine.py               모델 호출, 검증, RDF 생성, SPARQL 및 실행 상태
ollama_client.py        원본 Ollama 전송 어댑터 (MLX 대응 포함)
server.py               로컬 HTTP 서버
index.html/styles.css/app.js  웹 화면
runtime/<실행ID>/run.json     실제 실행 기록
runtime/<실행ID>/graph.ttl    성공한 실행의 그래프

위 경로들은 04-cctv/ 안에 있습니다.
원본의 사전 생성 result 파일은 분석 결과로 사용하지 않습니다.
위치 그래프 전체를 로드한 뒤 해당 센서의 위치 관계를 이번 실행의 관측과 합칩니다.
질의용 그래프는 실행별 22트리플(센서/위치 6 + 영상 URL 1 + 관측 15)입니다.
원본 79,482트리플은 수정하지 않으며 이전 실행의 관측과 섞이지 않습니다.
SOSA RDF 구조와 SPARQL 조회를 지원하며 GeoSPARQL 공간 함수 엔진은 아닙니다.

센서는 원본 데모의 L010009(강남역) 연결 설정을 유지합니다.
영상 스트림 URL도 RDF 메타데이터로 유지하지만 네트워크 재생하지 않습니다.
이미지는 첨부된 예시이며 실시간 CCTV가 아닙니다.
기본 관측 시각 2025-10-17T14:30:00Z는 원본 예시값입니다.
이미지 촬영 시각 또는 위치를 별도로 검증한 값이 아닙니다.
위치 WKT는 원본 값을 유지합니다.
서버 재시작 시 화면의 실행 상태는 초기화되며 저장 파일은 runtime/에 남습니다.

5. 종료와 오류 해결
-------------------
서버를 실행한 터미널에서 Ctrl+C를 누르면 웹 서버가 종료됩니다.
별도 실행한 ollama serve는 해당 터미널에서 Ctrl+C로 종료합니다.
웹 서버 종료는 이미 Ollama가 수행 중인 추론의 즉각 취소를 보장하지 않습니다.

- 연결 불가: ollama list / ollama serve와 --ollama-host 설정을 확인합니다.
- 모델 없음: ollama list로 확인하고 설치된 vision 모델을 --model로 지정합니다.
- 모델 오류/메모리 부족: 호출 기록의 오류를 확인하고 더 작은 vision 모델을 사용합니다.
- JSON 오류: 모델 원문을 확인합니다. 재실행하거나 다른 vision 모델로 실행합니다.
- 시간 오류: ISO 8601 형식과 시간대(Z 또는 +09:00)를 포함합니다.
- 포트 충돌: --port로 포트를 바꾸고 접속 주소도 같은 포트로 바꿉니다.
- 원격 Ollama를 지정하면 이미지와 프롬프트가 그 서버로 전송됩니다.

6. 검증
-------
저장소 루트에서:

  04-cctv/.venv/bin/python -m unittest discover -s 04-cctv/tests -v

자동 테스트는 모델 전송을 대역으로 격리하고 실제 RDFLib/SPARQL을 실행합니다.
직접 모드, 설명 후 변환, 실행별 그래프 격리, 형식 오류, 번역 실패,
HTTP 요청 보호 및 MLX 옵션을 검사합니다.
실제 모델 연결 검증은 웹 화면에서 별도로 분석을 실행하여 확인하세요.
