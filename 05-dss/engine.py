"""ch08 RDF observations + ch07 evidence-based DSS, with executable SPARQL rules."""
from datetime import datetime
import json
from pathlib import Path
import threading
import time
import uuid
from rdflib import Graph, Namespace, RDF, RDFS, Literal

HERE = Path(__file__).resolve().parent
CITY = Namespace('http://k.fc/onto/city#')
DSS = Namespace('https://example.org/dss#')
LEVELS = ['여유', '보통', '약간 붐빔', '붐빔', '미기록']
FILES = {'observation': 'queries/01-observation.rq', 'R1': 'rules/01-crowded.rq',
         'R2': 'rules/02-recommend.rq', 'decision': 'queries/02-decision.rq'}

def rows(result):
    return [{str(k): str(v) for k, v in row.asdict().items()} for row in result]

def new_graph():
    graph = Graph()
    for prefix, ns in [('city', CITY), ('dss', DSS), ('rdfs', RDFS)]:
        graph.bind(prefix, ns)
    return graph

def triple_rows(graph):
    return [{'subject': str(s), 'predicate': str(p), 'object': str(o),
             'literal': isinstance(o, Literal)} for s, p, o in sorted(graph, key=lambda t: tuple(map(str, t)))]


class Engine:
    def __init__(self):
        self.queries = {key: (HERE / path).read_text(encoding='utf-8') for key, path in FILES.items()}
        self.source = Graph().parse(HERE / 'data/gangnam-source.ttl', format='turtle')
        self.schema = Graph().parse(HERE / 'data/city-tbox.ttl', format='turtle')
        self.policy = Graph().parse(HERE / 'data/policy.ttl', format='turtle')
        self.demo = Graph().parse(HERE / 'data/demo-observation.ttl', format='turtle')
        self.lock = threading.RLock()
        self.latest = None
        with self.lock:
            self.original = rows(self.source.query(self.queries['observation']))[0]

    def status(self):
        return {'queries': self.queries, 'source': self.original, 'levels': LEVELS,
                'source_triples': len(self.source), 'schema_triples': len(self.schema),
                'rules': [{'id': str(rule).split('#')[-1], 'label': str(self.policy.value(rule, RDFS.label)),
                           'description': str(self.policy.value(rule, RDFS.comment))} for rule in (DSS.R1, DSS.R2)],
                'latest': self.latest}

    def evaluate(self, config):
        if not isinstance(config, dict):
            raise ValueError('JSON 객체가 필요합니다.')
        source = config.get('source', 'demo')
        level = config.get('level', '붐빔')
        enabled = config.get('rules', {'R1': True, 'R2': True})
        if source not in ('demo', 'original') or level not in LEVELS:
            raise ValueError('지원하지 않는 관측 설정입니다.')
        if not isinstance(enabled, dict) or set(enabled) != {'R1','R2'} or any(type(v) is not bool for v in enabled.values()):
            raise ValueError('R1, R2 규칙의 사용 여부를 지정하세요.')
        with self.lock:
            return self._evaluate(source, level, enabled)

    def _evaluate(self, source, level, enabled):
        started = time.perf_counter()
        data = self.source if source == 'original' else self.demo
        if source == 'demo':
            data = self.demo + Graph()
            subject = DSS.DemoPopulation_POI014
            data.remove((subject, CITY.congestionLevel, None))
            if level != '미기록':
                data.add((subject, CITY.congestionLevel, Literal(level)))
        query_started = time.perf_counter()
        selected = list(data.query(self.queries['observation']))
        evidence = rows(selected)
        query_ms = round((time.perf_counter() - query_started) * 1000, 2)
        # Only the selected latest observation enters rule working memory.
        # Forecasts and earlier statuses cannot accidentally trigger the rules.
        facts = new_graph()
        if selected:
            row = selected[0].asdict()
            area, obs = row['area'], row['observation']
            for pred in (RDF.type, RDFS.label, CITY.areaCode):
                for obj in data.objects(area, pred): facts.add((area, pred, obj))
            facts.add((area, CITY.hasPopulationStatus, obs))
            for pred in (RDF.type, CITY.observedAt, CITY.congestionLevel):
                for obj in data.objects(obs, pred): facts.add((obs, pred, obj))
        working = new_graph()
        for graph in (facts, self.schema, self.policy):
            for triple in graph: working.add(triple)
        derived = new_graph()
        trace = []
        for name in ('R1', 'R2'):
            before = time.perf_counter()
            additions = new_graph()
            if enabled[name]:
                result = working.query(self.queries[name]).graph
                for triple in result:
                    if triple not in working: additions.add(triple)
                for triple in additions:
                    working.add(triple)
                    derived.add(triple)
            trace.append({'id': name, 'enabled': enabled[name],
                          'status': 'disabled' if not enabled[name] else ('fired' if additions else 'unmatched'),
                          'count': len(additions), 'triples': triple_rows(additions),
                          'ttl': additions.serialize(format='turtle') if additions else '',
                          'query': self.queries[name], 'ms': round((time.perf_counter()-before)*1000,2)})
        decision_rows = rows(working.query(self.queries['decision']))
        recommendation = decision_rows[0] if decision_rows else None
        if recommendation:
            title = recommendation['actionLabel']
            explanation = (f"{recommendation['name']} 관측이 ‘{recommendation['level']}’으로 기록되어 있기 때문에, "
                           '혼잡한 장소에는 방문 자제를 안내한다는 예시 규칙에 따라, 방문 자제 권고를 제시합니다.')
            reason = 'R1에서 혼잡 관측으로 분류하고, R2에서 해당 관측을 근거로 권고를 도출했습니다.'
        else:
            title = '방문 자제 권고 미도출'
            if not evidence or 'level' not in evidence[0]:
                reason = '혼잡도 관측값이 없어 규칙의 조건을 확인할 수 없습니다.'
            elif not enabled['R1']:
                reason = 'R1이 꺼져 있어 혼잡 관측 분류가 도출되지 않았습니다.'
            elif trace[0]['status'] == 'unmatched':
                reason = f"기록된 혼잡도는 ‘{evidence[0]['level']}’이며, R1의 조건 ‘붐빔’과 일치하지 않습니다."
            else:
                reason = 'R2가 꺼져 있어 혼잡 관측에서 방문 자제 권고를 도출하지 않았습니다.'
            explanation = reason + ' 권고가 없다는 사실을 방문이 안전하다는 판단으로 해석하지 않습니다.'
        display_graph = facts + derived
        run = {'id': uuid.uuid4().hex[:12], 'created': datetime.now().astimezone().isoformat(),
               'config': {'source': source, 'level': level, 'rules': dict(enabled)},
               'source_label': '예시 관측' if source == 'demo' else '원본 관측',
               'evidence': evidence, 'trace': trace, 'decisions': decision_rows,
               'decision': {'recommended': bool(recommendation), 'title': title,
                            'explanation': explanation, 'reason': reason},
               'facts': triple_rows(facts), 'inferred': triple_rows(derived),
               'facts_ttl': facts.serialize(format='turtle'),
               'inferred_ttl': derived.serialize(format='turtle') if derived else '',
               'ttl': display_graph.serialize(format='turtle'),
               'queries': self.queries,
               'counts': {'facts': len(facts), 'inferred': len(derived), 'rows': len(evidence),
                          'recommendations': len(decision_rows)},
               'query_ms': query_ms, 'total_ms': round((time.perf_counter()-started)*1000,2)}
        self.latest = run
        return run
