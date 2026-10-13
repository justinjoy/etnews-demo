#!/usr/bin/env python3
"""Local CCTV demo. No Java, Jena, Fuseki, or external RDF server."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from engine import Manager

HERE = Path(__file__).resolve().parent
ASSETS = {'index.html': 'text/html', 'styles.css': 'text/css', 'app.js': 'text/javascript', 'README.txt': 'text/plain'}
ASSETS.update({f'shots/shot_0{i}.jpg': 'image/jpeg' for i in range(1, 6)})

class Handler(BaseHTTPRequestHandler):
    def send(self, code, value, mime='application/json'):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            value = value.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', mime + ('; charset=utf-8' if not mime.startswith('image/') else ''))
        self.send_header('Content-Length', str(len(value)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(value)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def valid_host(self):
        return self.headers.get('Host') in {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error': '로컬 주소로 접속하세요.'})
        path = urlsplit(self.path).path
        if path in ('/', '/04-cctv'):
            self.send_response(302)
            self.send_header('Location', '/04-cctv/')
            self.end_headers()
            return
        if path == '/api/status':
            return self.send(200, dict(self.server.manager.status(), token=self.server.token))
        if path.startswith('/api/runs/'):
            parts = path.removeprefix('/api/runs/').split('/')
            try:
                run = self.server.manager.snapshot(parts[0])
            except KeyError:
                return self.send(404, {'error': '실행을 찾을 수 없습니다.'})
            if len(parts) == 1:
                return self.send(200, run)
            if parts[1:] == ['graph']:
                return self.send(200, run['ttl'], 'text/turtle')
        asset = path.removeprefix('/04-cctv/') if path.startswith('/04-cctv/') else None
        if asset == '':
            asset = 'index.html'
        if asset in ASSETS:
            return self.send(200, (HERE / asset).read_bytes(), ASSETS[asset])
        return self.send(404, {'error': '경로를 찾을 수 없습니다.'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        allowed = {f'http://localhost:{self.server.server_port}', f'http://127.0.0.1:{self.server.server_port}'}
        if not self.valid_host() or (origin and origin not in allowed) or not secrets.compare_digest(self.headers.get('X-Run-Token', ''), self.server.token):
            return self.send(403, {'error': '로컬 페이지를 새로고침하고 다시 실행하세요.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError('요청 크기가 올바르지 않습니다.')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('JSON 객체가 필요합니다.')
            path = urlsplit(self.path).path
            if path == '/api/runs':
                rid = self.server.manager.start(body.get('mode'), body.get('time'))
                return self.send(202, {'id': rid})
            parts = path.split('/')
            if len(parts) == 5 and parts[1:3] == ['api', 'runs'] and parts[4] == 'extract':
                self.server.manager.resume(parts[3])
                return self.send(202, {'id': parts[3]})
            return self.send(404, {'error': '경로를 찾을 수 없습니다.'})
        except (ValueError, UnicodeDecodeError) as exc:
            return self.send(400, {'error': str(exc)})
        except RuntimeError as exc:
            return self.send(409, {'error': str(exc)})
        except KeyError:
            return self.send(404, {'error': '실행을 찾을 수 없습니다.'})


def make_server(port=8005, manager=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.manager = manager or Manager()
    server.token = secrets.token_urlsafe(32)
    return server


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', type=int, default=8005)
    ap.add_argument('--model', default='qwen3.8:27b-mlx')
    ap.add_argument('--ollama-host', default='http://localhost:11434')
    ap.add_argument('--timeout', type=int, default=600)
    args = ap.parse_args()
    if args.timeout <= 0:
        ap.error('--timeout은 양수여야 합니다.')
    manager = Manager(args.model, args.ollama_host, args.timeout)
    try:
        server = make_server(args.port, manager)
    except OSError as exc:
        ap.exit(1, f'서버 실행 실패: {exc}\n--port로 다른 포트를 선택하세요.\n')
    print(f'http://localhost:{server.server_port}/04-cctv/ · {args.model}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
