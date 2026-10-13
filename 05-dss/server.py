#!/usr/bin/env python3
"""Local web API for the SPARQL and rule-based DSS demonstration."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from engine import Engine

HERE = Path(__file__).resolve().parent
ASSETS = {'index.html':'text/html', 'styles.css':'text/css', 'app.js':'text/javascript', 'README.txt':'text/plain'}

class Handler(BaseHTTPRequestHandler):
    def send(self, status, value, mime='application/json'):
        if isinstance(value,(dict,list)): value=json.dumps(value,ensure_ascii=False)
        if isinstance(value,str): value=value.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',mime+'; charset=utf-8')
        self.send_header('Content-Length',str(len(value)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try: self.wfile.write(value)
        except (BrokenPipeError,ConnectionResetError): pass

    def valid_host(self):
        return self.headers.get('Host') in {f'localhost:{self.server.server_port}',f'127.0.0.1:{self.server.server_port}'}

    def do_GET(self):
        if not self.valid_host(): return self.send(403,{'error':'로컬 주소로 접속하세요.'})
        path=urlsplit(self.path).path
        if path in ('/','/05-dss'):
            self.send_response(302);self.send_header('Location','/05-dss/');self.end_headers();return
        if path=='/api/status':
            with self.server.engine.lock:
                return self.send(200,dict(self.server.engine.status(),token=self.server.token))
        asset=path.removeprefix('/05-dss/') if path.startswith('/05-dss/') else None
        if asset=='': asset='index.html'
        if asset in ASSETS: return self.send(200,(HERE/asset).read_bytes(),ASSETS[asset])
        return self.send(404,{'error':'경로를 찾을 수 없습니다.'})

    def do_POST(self):
        origin=self.headers.get('Origin')
        allowed={f'http://localhost:{self.server.server_port}',f'http://127.0.0.1:{self.server.server_port}'}
        if not self.valid_host() or (origin and origin not in allowed) or not secrets.compare_digest(self.headers.get('X-Run-Token',''),self.server.token):
            return self.send(403,{'error':'로컬 페이지를 새로고침하고 다시 실행하세요.'})
        if urlsplit(self.path).path!='/api/evaluate': return self.send(404,{'error':'경로를 찾을 수 없습니다.'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=8192: raise ValueError('요청 크기가 올바르지 않습니다.')
            data=json.loads(self.rfile.read(length))
            return self.send(200,self.server.engine.evaluate(data))
        except (ValueError,UnicodeDecodeError) as exc:
            return self.send(400,{'error':str(exc)})
        except Exception:
            import traceback
            traceback.print_exc()
            return self.send(500,{'error':'질의 실행에 실패했습니다. 서버 로그와 질의 파일을 확인하세요.'})


def make_server(port=8006,engine=None):
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.engine=engine or Engine()
    server.token=secrets.token_urlsafe(32)
    return server

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8006)
    args=parser.parse_args()
    try: server=make_server(args.port)
    except OSError as exc: parser.exit(1,f'서버 실행 실패: {exc}\n--port로 다른 포트를 지정하세요.\n')
    print(f'http://localhost:{server.server_port}/05-dss/',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
