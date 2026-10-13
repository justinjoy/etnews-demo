#!/usr/bin/env python3
"""Local Askitect web server: real Claude CLI, RDFLib and SPARQL."""
import argparse
import json
from pathlib import Path
import secrets
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from engine import Manager, DEFAULT_REQUEST, DEFAULT_QUESTION, Q_MISSING, Q_APPROVER, TTL

HERE = Path(__file__).resolve().parent
ASSETS = {'index.html': 'text/html', 'styles.css': 'text/css', 'app.js': 'text/javascript', 'README.txt': 'text/plain'}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, data, mime='application/json'):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False)
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def valid_host(self):
        port = self.server.server_port
        return self.headers.get('Host') in {f'localhost:{port}', f'127.0.0.1:{port}'}

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error': '로컬 주소로 접속하세요.'})
        path = urlsplit(self.path).path
        manager = self.server.manager
        if path in ('/', '/03-askitect'):
            self.send_response(302)
            self.send_header('Location', '/03-askitect/')
            self.end_headers()
            return
        if path == '/api/status':
            return self.send(200, {'service': 'askitect-claude', 'claude_available': bool(shutil.which('claude')), 'model': manager.model, 'token': self.server.token, 'latest_run': manager.latest, 'active_run': manager.active, 'request': DEFAULT_REQUEST, 'question': DEFAULT_QUESTION, 'queries': {'missing': Q_MISSING, 'approver': Q_APPROVER}, 'ontology': Path(TTL).read_text(encoding='utf-8'), 'policy': manager.policy})
        if path.startswith('/api/runs/'):
            parts = path.removeprefix('/api/runs/').split('/')
            try:
                run = manager.snapshot(parts[0])
            except KeyError:
                return self.send(404, {'error': '실행을 찾을 수 없습니다.'})
            if len(parts) == 1:
                return self.send(200, run)
            if parts[1:] == ['graph']:
                with manager.lock:
                    return self.send(200, manager.graphs[parts[0]].serialize(format='turtle'), 'text/turtle')
            return self.send(404, {'error': '경로를 찾을 수 없습니다.'})
        asset = path.removeprefix('/03-askitect/') if path.startswith('/03-askitect/') else ''
        if path == '/03-askitect/':
            asset = 'index.html'
        if asset in ASSETS:
            return self.send(200, (HERE / asset).read_bytes(), ASSETS[asset])
        return self.send(404, {'error': '경로를 찾을 수 없습니다.'})

    def do_POST(self):
        if not self.valid_host() or not secrets.compare_digest(self.headers.get('X-Run-Token', ''), self.server.token):
            return self.send(403, {'error': '페이지를 새로고침한 뒤 다시 시도하세요.'})
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://localhost:{self.server.server_port}', f'http://127.0.0.1:{self.server.server_port}'}:
            return self.send(403, {'error': '다른 사이트에서 요청할 수 없습니다.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32768:
                raise ValueError('요청 크기가 올바르지 않습니다.')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('JSON 객체가 필요합니다.')
            path = urlsplit(self.path).path
            manager = self.server.manager
            if path == '/api/runs':
                if not shutil.which('claude'):
                    return self.send(503, {'error': 'claude CLI를 찾지 못했습니다. README.txt를 확인하세요.'})
                rid = manager.start(body.get('request'), body.get('question', DEFAULT_QUESTION), body.get('delegation', True))
                return self.send(202, {'id': rid})
            parts = path.split('/')
            if len(parts) == 5 and parts[1:3] == ['api', 'runs']:
                rid, action = parts[3:]
                manager.snapshot(rid)
                if action == 'answers':
                    manager.submit(rid, body.get('answers'))
                    return self.send(202, {'id': rid})
                if action == 'cancel':
                    manager.cancel(rid)
                    return self.send(200, {'cancel_requested': True})
            return self.send(404, {'error': '경로를 찾을 수 없습니다.'})
        except (ValueError, UnicodeDecodeError) as error:
            return self.send(400, {'error': str(error)})
        except KeyError:
            return self.send(404, {'error': '실행을 찾을 수 없습니다.'})
        except RuntimeError as error:
            return self.send(409, {'error': str(error)})


def make_server(port=8004, manager=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.manager = manager or Manager()
    server.token = secrets.token_urlsafe(32)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8004)
    parser.add_argument('--model', default='claude-sonnet-4-6')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout은 양수여야 합니다.')
    manager = Manager(model=args.model, timeout=args.timeout)
    try:
        server = make_server(args.port, manager)
    except OSError as error:
        parser.exit(1, f'서버 실행 실패: {error}\n다른 --port 값을 사용하세요.\n')
    print(f'Local: http://localhost:{server.server_port}/03-askitect/', flush=True)
    print(f'Model: {args.model} · Claude CLI: {"발견됨" if shutil.which("claude") else "설치 필요"}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if manager.active:
            manager.cancel(manager.active)
        if manager.worker:
            manager.worker.join(5)
        server.server_close()


if __name__ == '__main__':
    main()
