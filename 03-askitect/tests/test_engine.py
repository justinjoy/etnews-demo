"""Deterministic contract tests. Test doubles never appear in the product UI."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
import server
from rdflib import Graph
from askitect import EX, TTL, commit_request, find_missing, decide_approver


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name)

    def make(self, fields):
        def generator(prompt, model, cancel, timeout, **kwargs):
            if '키는 department' in prompt:
                answer = json.dumps(fields, ensure_ascii=False)
            elif '한 문장을 출력' in prompt:
                answer = '테스트 재질의: 누락된 값을 입력해 주세요.'
            else:
                answer = '테스트 최종 문장: SPARQL 근거의 승인권자를 안내합니다.'
            return {'answer': answer, 'duration_seconds': 0.01, 'models': ['test-double'], 'session_id': 'test-only'}
        return engine.Manager(storage=self.storage, generator=generator)

    def settled(self, manager, rid):
        manager.worker.join(5)
        self.assertFalse(manager.worker.is_alive())
        return manager.snapshot(rid)

    def test_actual_sparql_missing_then_user_fill_then_delegated_decision(self):
        manager = self.make({'department': 'Marketing', 'amount': 40000000, 'purpose': None})
        rid = manager.start('마케팅팀 4천만원', engine.DEFAULT_QUESTION)
        first = self.settled(manager, rid)
        self.assertEqual(first['status'], 'waiting')
        self.assertEqual(first['missing'], [{'property': 'hasPurpose', 'label': '목적'}])
        self.assertIsNone(first['decision'])
        self.assertIsNone(first['fields']['purpose'])
        self.assertEqual(len(first['calls']), 2)
        with self.assertRaises(ValueError):
            manager.submit(rid, {'hasPurpose': ' '})
        self.assertEqual(manager.snapshot(rid)['status'], 'waiting')
        manager.submit(rid, {'hasPurpose': '캠페인 집행'})
        result = self.settled(manager, rid)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['decision']['approver'], 'DelegatedTeamLead')
        self.assertEqual(result['field_sources']['purpose'], 'user')
        self.assertEqual(result['fields']['purpose'], '캠페인 집행')
        self.assertEqual(result['missing'], [])
        self.assertIn(result['decision']['path'], result['calls'][-1]['prompt'])
        self.assertEqual(len(result['calls']), 3)
        graph = Graph().parse(self.storage / rid / 'graph.ttl')
        self.assertEqual(str(graph.value(EX.Request_web, EX.hasPurpose)), '캠페인 집행')
        saved = json.loads((self.storage / rid / 'result.json').read_text())
        self.assertEqual(saved['decision'], result['decision'])

    def test_threshold_boundary_and_missing_rule_are_distinct(self):
        for dept, amount, kind, approver in [('Marketing', 12000000, 'below_threshold', None), ('Marketing', 30000000, 'below_threshold', None), ('Marketing', 30000001, 'resolved', 'DelegatedTeamLead'), ('HR', 40000000, 'no_rule', None)]:
            with self.subTest(dept=dept, amount=amount):
                manager = self.make({'department': dept, 'amount': amount, 'purpose': '입력된 목적'})
                result = self.settled(manager, manager.start('테스트', engine.DEFAULT_QUESTION))
                self.assertEqual(result['decision']['kind'], kind)
                self.assertEqual(result['decision']['approver'], approver)
                self.assertEqual(len(result['calls']), 2 if approver else 1)
                if not approver:
                    self.assertIsNone(result['answer'])

    def test_disable_delegation_changes_only_run_graph(self):
        manager = self.make({'department': 'Marketing', 'amount': 40000000, 'purpose': '캠페인'})
        result = self.settled(manager, manager.start('테스트', engine.DEFAULT_QUESTION, False))
        self.assertEqual(result['decision']['approver'], 'Director')
        self.assertIsNone(result['decision']['delegate'])
        source = Graph().parse(TTL)
        self.assertIn((EX.Director, EX.delegatesTo, EX.DelegatedTeamLead), source)

    def test_multiple_missing_fields_and_strict_amount_validation(self):
        manager = self.make({'department': None, 'amount': None, 'purpose': None})
        rid = manager.start('결재 요청', engine.DEFAULT_QUESTION)
        self.settled(manager, rid)
        with self.assertRaises(ValueError):
            manager.submit(rid, {'hasDepartment': 'Marketing', 'hasAmount': '4000만원', 'hasPurpose': '목적'})
        self.assertTrue(all(value is None for value in manager.snapshot(rid)['fields'].values()))
        manager.submit(rid, {'hasDepartment': '마케팅팀', 'hasAmount': '40,000,000', 'hasPurpose': '목적'})
        result = self.settled(manager, rid)
        self.assertEqual(result['decision']['approver'], 'DelegatedTeamLead')

    def test_malformed_model_json_errors_without_rule_fallback(self):
        manager = engine.Manager(storage=self.storage, generator=lambda *args, **kwargs: {'answer': 'not JSON', 'duration_seconds': 0.01})
        result = self.settled(manager, manager.start('마케팅 4000만원', engine.DEFAULT_QUESTION))
        self.assertEqual(result['status'], 'error')
        self.assertIn('JSON', result['error'])
        self.assertIsNone(result['decision'])
        self.assertIsNone(result['answer'])

    def test_cli_failure_and_final_answer_failure_preserve_symbolic_evidence(self):
        manager = self.make({'department': 'Marketing', 'amount': 40000000, 'purpose': None})
        rid = manager.start('요청', engine.DEFAULT_QUESTION)
        self.settled(manager, rid)
        def failed(*args, **kwargs):
            raise engine.GenerationError('테스트: CLI 로그인 실패')
        manager.generator = failed
        manager.submit(rid, {'hasPurpose': '목적'})
        result = self.settled(manager, rid)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['decision']['approver'], 'DelegatedTeamLead')
        self.assertIsNone(result['answer'])
        self.assertIn('로그인 실패', result['error'])

    def test_waiting_cancel_and_conflicting_start(self):
        manager = self.make({'department': 'Marketing', 'amount': 40000000, 'purpose': None})
        rid = manager.start('요청', engine.DEFAULT_QUESTION)
        self.settled(manager, rid)
        with self.assertRaises(RuntimeError):
            manager.start('다른 요청', engine.DEFAULT_QUESTION)
        manager.cancel(rid)
        self.assertEqual(manager.snapshot(rid)['status'], 'cancelled')
        self.assertIsNone(manager.active)
        with self.assertRaises(RuntimeError):
            manager.submit(rid, {'hasPurpose': '목적'})

    def test_types_do_not_turn_invalid_extraction_into_valid_graph_data(self):
        fields, issues = engine.parse_fields('{"department":"bad uri!","amount":true,"purpose":" "}')
        self.assertTrue(all(value is None for value in fields.values()))
        self.assertEqual(len(issues), 2)
        for value in (-1, True, '1.5', '4천만원', '1,2'):
            with self.assertRaises(ValueError):
                engine.amount(value)


class HttpTests(unittest.TestCase):
    def test_missing_info_post_roundtrip_and_request_guards(self):
        with tempfile.TemporaryDirectory() as storage:
            def generator(prompt, *args, **kwargs):
                answer = '{"department":"Marketing","amount":40000000,"purpose":null}' if '키는 department' in prompt else '테스트 응답'
                return {'answer': answer, 'duration_seconds': 0.01}
            manager = engine.Manager(storage=storage, generator=generator)
            http = server.make_server(0, manager)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{http.server_port}'
            def request(path, body=None, **headers):
                req = Request(base+path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
                try:
                    response = urlopen(req, timeout=5)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, json.loads(response.read())
            try:
                with patch('server.shutil.which', return_value='/test/claude'):
                    _, info = request('/api/status')
                    headers = {'X-Run-Token': info['token']}
                    payload = {'request': '테스트', 'question': engine.DEFAULT_QUESTION}
                    self.assertEqual(request('/api/runs', payload)[0], 403)
                    self.assertEqual(request('/api/runs', payload, **headers, Origin='https://other.example')[0], 403)
                    code, started = request('/api/runs', payload, **headers)
                    self.assertEqual(code, 202)
                    rid = started['id']
                    manager.worker.join(5)
                    self.assertEqual(request('/api/runs/'+rid)[1]['status'], 'waiting')
                    self.assertEqual(request('/api/runs/'+rid+'/answers', {'answers': {'hasPurpose': ''}}, **headers)[0], 400)
                    code, _ = request('/api/runs/'+rid+'/answers', {'answers': {'hasPurpose': '캠페인'}}, **headers)
                    self.assertEqual(code, 202)
                    manager.worker.join(5)
                    self.assertEqual(request('/api/runs/'+rid)[1]['decision']['approver'], 'DelegatedTeamLead')
                    self.assertEqual(request('/api/status', Host='attacker.example')[0], 403)
                    self.assertEqual(request('/03-askitect/runtime/'+rid+'/result.json')[0], 404)
            finally:
                http.shutdown(); http.server_close(); thread.join()
                if manager.active:
                    manager.cancel(manager.active)


if __name__ == '__main__':
    unittest.main()
