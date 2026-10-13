"""Backend contract tests use explicit doubles; the web app has no fake mode."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manager(self, generator):
        return server.RunManager(memory_root=self.root, generator=generator)

    def finish(self, manager, run_id):
        manager.worker.join(timeout=5)
        self.assertFalse(manager.worker.is_alive())
        return manager.snapshot(run_id)

    def test_four_independent_calls_save_real_first_answer_and_read_file(self):
        received = []

        def generator(prompt, model, cancel, timeout):
            received.append(prompt)
            return {"answer": f"테스트 응답 {len(received)}: 30분 걷기 / 커피 / 야식", "duration_seconds": 0.01}

        manager = self.manager(generator)
        run_id = manager.start(1)
        run = self.finish(manager, run_id)
        self.assertEqual(run['status'], 'complete')
        self.assertEqual(len(run['calls']), 4)
        self.assertEqual([(c['mode'], c['turn']) for c in run['calls']], [('naive', 1), ('fixed', 1), ('naive', 2), ('fixed', 2)])
        saved = (self.root / run_id / 'criteria.md').read_text()
        self.assertEqual(saved, run['calls'][1]['answer'])
        self.assertEqual(saved, run['calls'][3]['memory_injected'])
        self.assertNotIn('[이전에 확정한', received[2])
        self.assertIn(saved, received[3])
        self.assertEqual(json.loads((self.root / run_id / 'result.json').read_text())['status'], 'complete')
        next_id = manager.start(2)
        next_run = self.finish(manager, next_id)
        self.assertNotEqual(run_id, next_id)
        self.assertEqual((self.root / run_id / 'criteria.md').read_text(), saved)
        self.assertEqual(len(next_run['calls'][2]['retrieved']), 2)

    def test_fixed_second_turn_reads_disk_not_cached_answer(self):
        count = 0
        def generator(prompt, model, cancel, timeout):
            nonlocal count
            count += 1
            if count == 3:
                next(self.root.glob('*/criteria.md')).write_text('파일에서 읽은 기준 검증')
            return {"answer": '원래 응답', "duration_seconds": 0.01}
        manager = self.manager(generator)
        run = self.finish(manager, manager.start(1))
        self.assertEqual(run['calls'][3]['memory_injected'], '파일에서 읽은 기준 검증')
        self.assertIn('파일에서 읽은 기준 검증', run['calls'][3]['prompt'])

    def test_failure_preserves_partial_answers_and_never_fills_remaining_calls(self):
        count = 0
        def generator(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise server.GenerationError('테스트: 로그인 실패')
            return {"answer": '첫 응답', "duration_seconds": 0.01}
        manager = self.manager(generator)
        run = self.finish(manager, manager.start(1))
        self.assertEqual(run['status'], 'error')
        self.assertIn('로그인 실패', run['error'])
        self.assertEqual(run['calls'][0]['answer'], '첫 응답')
        self.assertIsNone(run['calls'][1]['answer'])
        self.assertIsNone(run['memory'])
        self.assertEqual(len(run['calls']), 2)

    def test_cancel_and_duplicate_run(self):
        def generator(prompt, model, cancel, timeout):
            cancel.wait(3)
            raise server.Cancelled()
        manager = self.manager(generator)
        run_id = manager.start(1)
        with self.assertRaises(RuntimeError):
            manager.start(1)
        manager.cancel(run_id)
        run = self.finish(manager, run_id)
        self.assertEqual(run['status'], 'cancelled')
        self.assertTrue(all(c['answer'] is None for c in run['calls']))

    def test_invalid_top_k(self):
        manager = self.manager(None)
        for value in (0, 3, True, '1', None):
            with self.assertRaises(ValueError):
                manager.start(value)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test-claude'

    def executable(self, body):
        self.path.write_text(f'#!{sys.executable}\n' + body)
        self.path.chmod(0o700)
        return patch('server.shutil.which', return_value=str(self.path))

    def test_cli_receives_stdin_and_isolation_options_and_returns_actual_json(self):
        with self.executable('''import sys,json
args=sys.argv[1:]
assert '--safe-mode' in args and '--no-session-persistence' in args
assert args[args.index('--tools')+1] == ''
assert '--resume' not in args and '--continue' not in args
print(json.dumps({'result':'echo:'+sys.stdin.read(),'session_id':'test-session','modelUsage':{'test-model':{}},'is_error':False}))
'''):
            result = server.generate('요청 본문', 'test', threading.Event(), 5)
        self.assertEqual(result['answer'], 'echo:요청 본문')
        self.assertEqual(result['session_id'], 'test-session')
        self.assertEqual(result['models'], ['test-model'])

    def test_cli_empty_invalid_error_and_nonzero_outputs_fail(self):
        for body in ("print('{}')", "print('invalid')", "print('[]')", "print('{\"is_error\":true,\"result\":\"limit\"}')", "import sys;sys.exit(1)"):
            with self.subTest(body=body), self.executable(body):
                with self.assertRaises(server.GenerationError):
                    server.generate('request', 'test', threading.Event(), 5)

    def test_timeout_kills_cli(self):
        with self.executable('import time;time.sleep(10)'):
            with self.assertRaisesRegex(server.GenerationError, '초과'):
                server.generate('request', 'test', threading.Event(), 0.1)

    def test_cancel_kills_cli(self):
        cancel = threading.Event()
        cancel.set()
        with self.executable('import time;time.sleep(10)'):
            with self.assertRaises(server.Cancelled):
                server.generate('request', 'test', cancel, 5)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.cli_patch = patch('server.shutil.which', return_value='/test/claude')
        self.cli_patch.start()
        self.addCleanup(self.cli_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.manager = server.RunManager(memory_root=self.temp.name, generator=lambda *args: {'answer': '테스트 실제 반환값', 'duration_seconds': 0.01})
        self.http = server.make_server(0, self.manager)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        if self.manager.worker:
            self.manager.worker.join(3)
        self.temp.cleanup()

    def request(self, path, body=None, **headers):
        data = json.dumps(body).encode() if body is not None else None
        req = Request(self.base + path, data=data, headers=headers)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def test_http_run_and_poll_and_origin_protection(self):
        code, info = self.request('/api/status')
        self.assertEqual(code, 200)
        self.assertEqual(info['service'], 'rag-amnesia-claude')
        code, _ = self.request('/api/runs', {'top_k': 1})
        self.assertEqual(code, 403)
        headers = {'X-Run-Token': info['token']}
        code, _ = self.request('/api/runs', {'top_k': 1}, **headers, Origin='https://untrusted.example')
        self.assertEqual(code, 403)
        code, _ = self.request('/api/runs', {'top_k': True}, **headers)
        self.assertEqual(code, 400)
        with patch('server.shutil.which', return_value='/test/claude'):
            code, result = self.request('/api/runs', {'top_k': 1}, **headers)
        self.assertEqual(code, 202)
        self.manager.worker.join(3)
        code, run = self.request('/api/runs/' + result['id'])
        self.assertEqual(code, 200)
        self.assertEqual(run['status'], 'complete')
        self.assertEqual(run['calls'][0]['answer'], '테스트 실제 반환값')
        code, _ = self.request('/api/status', Host='untrusted.example')
        self.assertEqual(code, 403)
        code, _ = self.request('/02-rag-amnesia/memory/runs/' + result['id'] + '/result.json')
        self.assertEqual(code, 404)


if __name__ == '__main__':
    unittest.main()
