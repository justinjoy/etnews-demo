"""Real Ollama vision → validated JSON → isolated RDFLib graph → SPARQL."""
import base64
import copy
from datetime import datetime
import json
from pathlib import Path
import re
import threading
import time
import uuid

import requests
from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS, XSD
from ollama_client import generate

HERE = Path(__file__).resolve().parent
CCTV = Namespace('http://k.fc/onto/cctv#')
BASE = Namespace('http://k.fc/onto/')
SOSA = Namespace('http://www.w3.org/ns/sosa/')
GEO = Namespace('http://www.opengis.net/ont/geosparql#')
SENSOR = CCTV.L010009
DEFAULT_TIME = '2025-10-17T14:30:00Z'
ENUMS = {'weather': ['Clear', 'Cloudy', 'Rain', 'Snow', 'Fog', 'Unknown'],
         'pedestrian_congestion': ['VeryCrowded', 'Crowded', 'Normal', 'Sparse', 'Unknown'],
         'traffic_volume': ['Heavy', 'Moderate', 'Light', 'Unknown']}
PROPS = {'weather': 'WeatherCondition', 'pedestrian_congestion': 'Congestion', 'traffic_volume': 'TrafficVolume'}
SCHEMA = '\n'.join(f'- {key}: one of [{", ".join(values)}]' for key, values in ENUMS.items())
DIRECT = 'You are an expert traffic and pedestrian analyst. Given these five sequential CCTV snapshots, extract exactly three fields as compact JSON.\nAllowed values:\n' + SCHEMA + '\nDecide only from visible evidence. Do not invent facts. Return JSON only, no prose.'
DESCRIBE = ('You are an expert traffic and pedestrian analyst. Given the following five sequential CCTV snapshots, write ONE concise paragraph in English. '
            'Cover exactly these aspects: (1) weather, (2) pedestrian congestion, (3) road traffic volume, '
            '(4) brief visual evidence you relied on (e.g., shadows, umbrellas, wet roads, density, speed cues). '
            'State only observable facts. No lists, no JSON, no headings—just one paragraph.')
EXTRACT = 'Convert this English paragraph about a city scene into compact JSON with exactly three fields.\nAllowed values:\n' + SCHEMA + '\nDecide only from the paragraph. Do not invent facts. Return JSON only, no prose.\nParagraph:\n'
TRANSLATE = '다음 CCTV 영문 설명을 자연스러운 한국어로 번역하라. 요약하거나 덧붙이지 말고 번역문만 출력하라.\n\n'
QUERY = '''PREFIX cctv: <http://k.fc/onto/cctv#>
PREFIX sosa: <http://www.w3.org/ns/sosa/>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?sensor ?label ?location ?stream ?observation ?property ?result ?time
WHERE {
  VALUES ?sensor { cctv:L010009 }
  ?sensor rdfs:label ?label ; geo:hasGeometry/geo:asWKT ?location .
  OPTIONAL { ?sensor cctv:hasStreamUrl ?stream }
  ?observation a sosa:Observation ; sosa:madeBySensor ?sensor ;
    sosa:observedProperty ?property ; sosa:hasResult ?result ;
    sosa:resultTime ?time .
}
ORDER BY ?property'''


def clean(text):
    return re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()


def parse_result(text):
    text = clean(text)
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError('모델이 유효한 JSON을 반환하지 않았습니다. 실제 응답을 확인하세요.') from exc
    if not isinstance(value, dict) or set(value) != set(ENUMS):
        raise ValueError('모델 응답의 세 필드가 올바르지 않습니다.')
    for key, allowed in ENUMS.items():
        if not isinstance(value[key], str) or value[key] not in allowed:
            raise ValueError(f'허용하지 않는 분류값: {key}={value[key]!r}')
    return value


def observations(data, stamp):
    graph = Graph()
    for name, ns in [('cctv', CCTV), ('sosa', SOSA), ('scene', BASE), ('geo', GEO)]:
        graph.bind(name, ns)
    for key, prop in PROPS.items():
        obs = BASE['Obs_' + uuid.uuid4().hex]
        for pred, obj in [(RDF.type, SOSA.Observation), (SOSA.madeBySensor, SENSOR),
                          (SOSA.observedProperty, BASE[prop]), (SOSA.hasResult, BASE[data[key]]),
                          (SOSA.resultTime, Literal(stamp, datatype=XSD.dateTime))]:
            graph.add((obs, pred, obj))
    return graph


class Manager:
    def __init__(self, model='qwen3.8:27b-mlx', host='http://localhost:11434', timeout=600):
        self.model, self.host, self.timeout = model, host.rstrip('/'), timeout
        self.lock = threading.RLock()
        self.runs = {}
        self.latest = self.active = None
        self.base = Graph().parse(HERE / 'data/cctv.ttl', format='turtle')
        self.sensor_count = sum(1 for _ in self.base.subjects(RDF.type, CCTV.CCTV))
        self.source_triples = len(self.base)
        self.context = Graph()
        for triple in self.base.triples((SENSOR, None, None)):
            self.context.add(triple)
        for geom in self.base.objects(SENSOR, GEO.hasGeometry):
            for triple in self.base.triples((geom, None, None)):
                self.context.add(triple)
        self.context.add((SENSOR, CCTV.hasStreamUrl, URIRef('https://topiscctv1.eseoul.go.kr/sd2/ch98.stream/playlist.m3u8')))
        self.sensor = {'id': 'L010009', 'label': str(self.base.value(SENSOR, RDFS.label)),
                       'wkt': str(next(self.base.objects(self.base.value(SENSOR, GEO.hasGeometry), GEO.asWKT)))}

    def status(self):
        try:
            r = requests.get(self.host + '/api/tags', timeout=3)
            r.raise_for_status()
            models = [m['name'] for m in r.json()['models']]
            error = '' if self.model in models else '선택한 모델이 설치되어 있지 않습니다.'
        except (requests.RequestException, ValueError, KeyError) as exc:
            models, error = [], 'Ollama에 연결할 수 없습니다: ' + str(exc)
        with self.lock:
            return {'model': self.model, 'host': self.host, 'ready': not error, 'error': error,
                    'models': models, 'latest': self.latest, 'active': self.active,
                    'sensor': self.sensor, 'sensors': self.sensor_count, 'triples': self.source_triples,
                    'query': QUERY, 'default_time': DEFAULT_TIME}

    def snapshot(self, rid):
        with self.lock:
            return copy.deepcopy(self.runs[rid])

    def update(self, rid, **values):
        with self.lock:
            self.runs[rid].update(values)

    def start(self, mode, stamp):
        if mode not in ('direct', 'describe'):
            raise ValueError('분석 모드가 올바르지 않습니다.')
        if not isinstance(stamp, str) or len(stamp) > 50:
            raise ValueError('ISO 8601 시각을 입력하세요.')
        dt = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError('관측 시각에 시간대(Z 또는 +09:00)를 포함하세요.')
        with self.lock:
            if self.active:
                raise RuntimeError('현재 분석이 끝난 뒤 다시 실행하세요.')
            rid = uuid.uuid4().hex[:12]
            self.runs[rid] = {'id': rid, 'mode': mode, 'time': stamp, 'model': self.model,
                              'state': 'running', 'phase': 'vision', 'calls': [], 'description': '',
                              'translation': '', 'warnings': [], 'data': None, 'rows': [], 'ttl': '',
                              'error': '', 'created': datetime.now().astimezone().isoformat()}
            self.latest = self.active = rid
            threading.Thread(target=self.work, args=(rid, False), daemon=True).start()
            return rid

    def resume(self, rid):
        with self.lock:
            if self.active or self.runs[rid]['state'] != 'waiting':
                raise RuntimeError('영문 설명 생성이 완료된 실행에서 변환하세요.')
            self.active = rid
            self.update(rid, state='running', phase='extract')
            threading.Thread(target=self.work, args=(rid, True), daemon=True).start()

    def call(self, rid, name, prompt, images=False):
        payload = {'model': self.model, 'prompt': prompt, 'stream': False, 'think': False,
                   'options': {'temperature': 0, 'num_predict': 2048}}
        if images:
            payload['images'] = [base64.b64encode(p.read_bytes()).decode('ascii') for p in sorted((HERE / 'shots').glob('*.jpg'))]
        record = {'name': name, 'prompt': prompt, 'images': len(payload.get('images', [])),
                  'state': 'running', 'response': '', 'seconds': None}
        with self.lock:
            self.runs[rid]['calls'].append(record)
        started = time.monotonic()
        try:
            response = generate(self.host + '/api/generate', payload, timeout=self.timeout).json()
            raw = response.get('response', '')
            if response.get('error') or not isinstance(raw, str) or not clean(raw):
                raise ValueError(response.get('error') or '모델의 응답이 비어 있습니다.')
            if response.get('done_reason') == 'length':
                raise ValueError('모델의 출력 길이 제한에 도달했습니다.')
            with self.lock:
                record.update(state='done', response=raw, tokens=response.get('eval_count'))
            return clean(raw)
        except Exception as exc:
            with self.lock:
                record.update(state='error', error=str(exc))
            raise
        finally:
            with self.lock:
                record['seconds'] = round(time.monotonic() - started, 2)

    def work(self, rid, resume):
        try:
            run = self.snapshot(rid)
            if run['mode'] == 'describe' and not resume:
                description = self.call(rid, '영상 → 영문 설명', DESCRIBE, True)
                self.update(rid, description=description, phase='translate')
                try:
                    translated = self.call(rid, '영문 → 한국어 표시', TRANSLATE + description)
                    self.update(rid, translation=translated)
                except Exception as exc:
                    self.update(rid, warnings=['번역 실패. 영문 설명으로 계속할 수 있습니다: ' + str(exc)])
                self.update(rid, state='waiting', phase='extract')
            else:
                raw = self.call(rid, '영문 → JSON' if resume else '영상 → JSON',
                                EXTRACT + run['description'] if resume else DIRECT, not resume)
                data = parse_result(raw)
                self.update(rid, data=data, phase='rdf')
                graph = observations(data, run['time'])
                for triple in self.context:
                    graph.add(triple)
                self.update(rid, phase='query')
                rows = [{str(key): str(value) for key, value in row.asdict().items()} for row in graph.query(QUERY)]
                if len(rows) != 3:
                    raise ValueError('SPARQL 결과가 3개 관측과 일치하지 않습니다.')
                self.update(rid, rows=rows, ttl=graph.serialize(format='turtle'), state='done', phase='done')
        except Exception as exc:
            self.update(rid, state='error', error=str(exc))
        finally:
            try:
                folder = HERE / 'runtime' / rid
                folder.mkdir(parents=True, exist_ok=True)
                snapshot = self.snapshot(rid)
                (folder / 'run.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
                if snapshot['ttl']:
                    (folder / 'graph.ttl').write_text(snapshot['ttl'], encoding='utf-8')
            except OSError as exc:
                with self.lock:
                    self.runs[rid]['warnings'].append('실행 파일 저장 실패: ' + str(exc))
            finally:
                with self.lock:
                    self.active = None
