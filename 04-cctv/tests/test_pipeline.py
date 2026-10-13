import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
from engine import Manager, parse_result, observations, QUERY, DEFAULT_TIME, SOSA
from server import make_server
import requests
from rdflib import Graph, RDF
from ollama_client import generate

DATA = {'weather':'Clear','pedestrian_congestion':'Sparse','traffic_volume':'Heavy'}

class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = Manager()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        original = engine.HERE
        self.here = patch.object(engine, 'HERE', Path(self.tmp.name))
        # The actual snapshots must still be sent through the transport.
        (Path(self.tmp.name) / 'shots').symlink_to(original / 'shots', target_is_directory=True)
        self.here.start()
        self.manager.runs = {}
        self.manager.active = self.manager.latest = None

    def tearDown(self):
        self.here.stop()
        self.tmp.cleanup()

    def wait(self, rid):
        deadline = time.monotonic() + 5
        while self.manager.active and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNone(self.manager.active)
        return self.manager.snapshot(rid)

    def response(self, text):
        return Mock(json=lambda: {'response':text, 'eval_count':10})

    def test_direct_real_rdf_and_sparql(self):
        with patch('engine.generate', return_value=self.response(json.dumps(DATA))) as transport:
            run = self.wait(self.manager.start('direct', DEFAULT_TIME))
        self.assertEqual(run['state'], 'done')
        self.assertEqual(len(run['rows']), 3)
        self.assertEqual({r['label'] for r in run['rows']}, {'강남역'})
        self.assertTrue(all(r['stream'].endswith('playlist.m3u8') for r in run['rows']))
        self.assertEqual(len(transport.call_args.args[1]['images']), 5)
        graph = Graph().parse(data=run['ttl'], format='turtle')
        self.assertEqual(len(graph), 22)
        self.assertEqual(len(list(graph.subjects(RDF.type,SOSA.Observation))),3)
        self.assertEqual(len(self.manager.base),79482)

    def test_describe_requires_resume_and_uses_english(self):
        english = 'A dry street with sparse pedestrians and heavy traffic.'
        with patch('engine.generate', side_effect=[self.response(english),self.response('한국어 번역'),self.response(json.dumps(DATA))]) as transport:
            rid=self.manager.start('describe',DEFAULT_TIME)
            run=self.wait(rid)
            self.assertEqual(run['state'],'waiting')
            self.assertIsNone(run['data'])
            self.assertEqual(transport.call_count,2)
            self.manager.resume(rid)
            run=self.wait(rid)
            self.assertEqual(run['state'],'done')
            payload=transport.call_args.args[1]
            self.assertIn(english,payload['prompt'])
            self.assertNotIn('한국어 번역',payload['prompt'])
            self.assertNotIn('images',payload)

    def test_bad_json_fails_without_fallback(self):
        with patch('engine.generate',return_value=self.response('sunny and heavy traffic')):
            run=self.wait(self.manager.start('direct',DEFAULT_TIME))
        self.assertEqual(run['state'],'error')
        self.assertIsNone(run['data'])
        self.assertFalse(run['ttl'])
        self.assertEqual(run['calls'][0]['response'],'sunny and heavy traffic')

    def test_runs_do_not_mix_observations(self):
        with patch('engine.generate',return_value=self.response(json.dumps(DATA))):
            first=self.wait(self.manager.start('direct',DEFAULT_TIME))
            second=self.wait(self.manager.start('direct',DEFAULT_TIME))
        self.assertEqual(len(second['rows']),3)
        self.assertTrue(set(r['observation'] for r in first['rows']).isdisjoint(r['observation'] for r in second['rows']))

    def test_invalid_values_and_time(self):
        for raw in ['{}','[]',json.dumps(dict(DATA,weather='Sunny'))]:
            with self.assertRaises(ValueError): parse_result(raw)
        with self.assertRaises(ValueError): self.manager.start('direct','2025-01-01T12:00:00')
        with self.assertRaises(ValueError): self.manager.start('other',DEFAULT_TIME)
        self.assertIsNone(self.manager.active)

    def test_translation_failure_keeps_english(self):
        with patch('engine.generate',side_effect=[self.response('Visible traffic.'),requests.ConnectionError('offline')]):
            run=self.wait(self.manager.start('describe',DEFAULT_TIME))
        self.assertEqual(run['state'],'waiting')
        self.assertEqual(run['description'],'Visible traffic.')
        self.assertTrue(run['warnings'])

    def test_http_guards(self):
        server=make_server(0,self.manager)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        root=f'http://127.0.0.1:{server.server_port}'
        try:
            self.assertEqual(requests.post(root+'/api/runs',json={}).status_code,403)
            self.assertEqual(requests.get(root+'/api/status',headers={'Host':'evil.test'}).status_code,403)
            self.assertEqual(requests.post(root+'/api/runs',json={},headers={'X-Run-Token':server.token,'Origin':'https://evil.test'}).status_code,403)
            self.assertEqual(requests.get(root+'/04-cctv/engine.py').status_code,404)
            self.assertEqual(requests.get(root+'/api/runs/missing').status_code,404)
        finally:
            server.shutdown();server.server_close();worker.join()

    def test_mlx_transport_preserves_payload(self):
        payload={'model':'qwen3.8:27b-mlx','prompt':'test'}
        with patch('ollama_client.requests.post',return_value=Mock()) as post:
            generate('http://localhost:11434/api/generate',payload)
        self.assertTrue(post.call_args.kwargs['json']['logprobs'])
        self.assertNotIn('logprobs',payload)

if __name__=='__main__': unittest.main()
