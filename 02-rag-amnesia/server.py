#!/usr/bin/env python3
"""Local web UI + actual Claude CLI calls. Python standard library only."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4

from rag_amnesia import (
    CRITERIA, DEFAULT_MODEL, SYSTEM_PROMPT, TURN1_QUERY, TURN2_QUERY,
    TfidfRetriever, build_prompt, load_corpus, score_criteria,
)

HERE = Path(__file__).resolve().parent


class GenerationError(Exception):
    pass


class Cancelled(Exception):
    pass


def terminate(proc):
    if proc.poll() is None:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
    proc.communicate()


def generate(prompt, model, cancel, timeout=180):
    executable = shutil.which("claude")
    if not executable:
        raise GenerationError("claude CLI를 찾을 수 없습니다. README.txt의 설치 및 로그인 안내를 확인하세요.")
    command = [
        executable, "-p", "--model", model, "--system-prompt", SYSTEM_PROMPT,
        "--output-format", "json", "--tools", "", "--safe-mode",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--no-session-persistence",
    ]
    started = time.monotonic()
    # Each call starts fresh: no resume, no project instructions, tools or MCP.
    with subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", cwd=HERE, start_new_session=(os.name == "posix"),
    ) as proc:
        first = True
        while True:
            if cancel.is_set():
                terminate(proc)
                raise Cancelled()
            if time.monotonic() - started > timeout:
                terminate(proc)
                raise GenerationError(f"Claude 응답 시간이 {timeout}초를 초과했습니다. 다시 실행하세요.")
            try:
                stdout, stderr = proc.communicate(input=prompt if first else None, timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                first = False
        if cancel.is_set():
            raise Cancelled()
        if proc.returncode != 0:
            raise GenerationError(f"Claude CLI 실패 (종료 코드 {proc.returncode}): " + (stderr.strip() or stdout.strip())[:2000])
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise GenerationError("Claude CLI가 유효한 JSON 응답을 반환하지 않았습니다. CLI 버전을 확인하세요.") from error
    if not isinstance(result, dict):
        raise GenerationError("Claude CLI 응답 형식이 올바르지 않습니다.")
    if result.get("is_error"):
        detail = result.get("result") or result.get("errors") or result.get("subtype")
        raise GenerationError("Claude 생성 실패: " + str(detail)[:2000])
    answer = result.get("result")
    if not isinstance(answer, str) or not answer.strip():
        raise GenerationError("Claude가 빈 답변을 반환했습니다. 예시 답변으로 대체하지 않습니다.")
    return {
        "answer": answer.strip(), "duration_seconds": round(time.monotonic() - started, 2),
        "session_id": result.get("session_id"), "models": list((result.get("modelUsage") or {}).keys()),
        "usage": result.get("usage"), "cost_usd": result.get("total_cost_usd"),
    }


class RunManager:
    def __init__(self, model=DEFAULT_MODEL, timeout=180, memory_root=None, generator=None):
        self.model, self.timeout = model, timeout
        self.memory_root = Path(memory_root) if memory_root else HERE / "memory" / "runs"
        self.generator = generator or generate
        self.docs = load_corpus()
        self.retriever = TfidfRetriever(self.docs)
        self.lock = threading.RLock()
        self.runs = {}
        self.latest = None
        self.active = None
        self.cancel_event = threading.Event()
        self.worker = None

    def snapshot(self, run_id):
        with self.lock:
            if run_id not in self.runs:
                raise KeyError(run_id)
            return copy.deepcopy(self.runs[run_id])

    def patch(self, run_id, **values):
        with self.lock:
            self.runs[run_id].update(values)

    def start(self, top_k):
        if type(top_k) is not int or top_k not in (1, 2):
            raise ValueError("top_k는 1 또는 2여야 합니다.")
        with self.lock:
            if self.active:
                raise RuntimeError("이미 실행 중입니다. 현재 실행이 끝나거나 취소될 때까지 기다려 주세요.")
            run_id = uuid4().hex
            self.runs[run_id] = {
                "id": run_id, "status": "running", "phase": "turn1", "model": self.model,
                "top_k": top_k, "started_at": time.time(), "finished_at": None,
                "calls": [], "memory": None, "memory_path": None,
                "error": None, "message": "Claude CLI 호출을 준비합니다.",
            }
            self.latest = self.active = run_id
            self.cancel_event = threading.Event()
            self.worker = threading.Thread(target=self.execute, args=(run_id, self.cancel_event), daemon=True)
            self.worker.start()
        return run_id

    def cancel(self, run_id):
        with self.lock:
            if run_id == self.active:
                self.cancel_event.set()

    def execute(self, run_id, cancel):
        directory = self.memory_root / run_id
        try:
            directory.mkdir(parents=True, exist_ok=False)
            for mode, turn in (("naive", 1), ("fixed", 1), ("naive", 2), ("fixed", 2)):
                if cancel.is_set():
                    raise Cancelled()
                query = TURN1_QUERY if turn == 1 else TURN2_QUERY
                retrieved = self.retriever.search(query, self.runs[run_id]["top_k"])
                # Read the actual file, not a constant or the in-memory prior answer.
                memory = (directory / "criteria.md").read_text(encoding="utf-8") if mode == "fixed" and turn == 2 else None
                prompt = build_prompt(query, retrieved, memory)
                call = {
                    "mode": mode, "turn": turn, "status": "running", "query": query,
                    "prompt": prompt, "retrieved": [{"doc": doc, "score": score} for doc, score in retrieved],
                    "memory_injected": memory, "answer": None, "criteria": None, "started_at": time.time(),
                }
                with self.lock:
                    self.runs[run_id]["calls"].append(call)
                self.patch(run_id, phase=f"turn{turn}", message=f"{'일반' if mode == 'naive' else '메모리'} RAG · {turn}턴 Claude 응답 대기 중")
                result = self.generator(prompt, self.model, cancel, self.timeout)
                if cancel.is_set():
                    raise Cancelled()
                with self.lock:
                    call.update(result, status="complete", criteria=score_criteria(result["answer"]))
                if mode == "fixed" and turn == 1:
                    path = directory / "criteria.md"
                    path.write_text(result["answer"], encoding="utf-8")
                    self.patch(run_id, memory=path.read_text(encoding="utf-8"), memory_path=f"memory/runs/{run_id}/criteria.md")
                self.save(run_id, directory)
            self.patch(run_id, status="complete", phase="complete", message="실제 Claude 응답 4개를 모두 받았습니다.")
        except Cancelled:
            self.patch(run_id, status="cancelled", message="실행을 취소했습니다. 완료된 응답은 그대로 표시합니다.")
        except Exception as error:
            self.patch(run_id, status="error", error=str(error), message="실행에 실패했습니다. 예시 답변으로 대체하지 않습니다.")
        finally:
            with self.lock:
                run = self.runs[run_id]
                for call in run["calls"]:
                    if call["status"] == "running":
                        call["status"] = run["status"]
                run["finished_at"] = time.time()
                self.active = None
            try:
                self.save(run_id, directory)
            except OSError as error:
                self.patch(run_id, status="error", error=f"결과 파일 저장 실패: {error}")

    def save(self, run_id, directory):
        temp = directory / "result.tmp"
        temp.write_text(json.dumps(self.snapshot(run_id), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(directory / "result.json")


STATIC_FILES = {
    "/02-rag-amnesia/": (HERE / "index.html", "text/html; charset=utf-8"),
    **{f"/02-rag-amnesia/{name}": (HERE / name, mime) for name, mime in (
        ("index.html", "text/html; charset=utf-8"), ("styles.css", "text/css; charset=utf-8"),
        ("app.js", "text/javascript; charset=utf-8"), ("data.js", "text/javascript; charset=utf-8"),
        ("rag.js", "text/javascript; charset=utf-8"), ("README.txt", "text/plain; charset=utf-8"),
    )},
    **{f"/01-mimic-rag/{name}": (HERE.parent / "01-mimic-rag" / name, mime) for name, mime in (
        ("index.html", "text/html; charset=utf-8"), ("styles.css", "text/css; charset=utf-8"),
        ("app.js", "text/javascript; charset=utf-8"), ("rag.js", "text/javascript; charset=utf-8"),
    )},
}


class Handler(BaseHTTPRequestHandler):
    def send_data(self, status, data, content_type="application/json; charset=utf-8"):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        elif isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def valid_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"localhost:{port}", f"127.0.0.1:{port}"}

    def do_GET(self):
        if not self.valid_host():
            return self.send_data(403, {"error": "로컬 주소로 접속하세요."})
        path = urlsplit(self.path).path
        manager = self.server.manager
        if path in ("/", "/02-rag-amnesia"):
            self.send_response(302)
            self.send_header("Location", "/02-rag-amnesia/")
            self.end_headers()
            return
        if path == "/api/status":
            return self.send_data(200, {
                "service": "rag-amnesia-claude", "claude_available": bool(shutil.which("claude")),
                "model": manager.model, "token": self.server.token, "latest_run": manager.latest,
                "active_run": manager.active, "docs": manager.docs, "criteria": CRITERIA,
                "queries": [TURN1_QUERY, TURN2_QUERY], "system_prompt": SYSTEM_PROMPT,
            })
        if path.startswith("/api/runs/"):
            parts = path.removeprefix("/api/runs/").split("/")
            try:
                run = manager.snapshot(parts[0])
            except KeyError:
                return self.send_data(404, {"error": "이 서버에 해당 실행이 없습니다."})
            if len(parts) == 1:
                return self.send_data(200, run)
            if parts[1:] == ["memory"] and run["memory"] is not None:
                return self.send_data(200, run["memory"], "text/plain; charset=utf-8")
            return self.send_data(404, {"error": "저장된 메모리가 없습니다."})
        if path in STATIC_FILES:
            file, mime = STATIC_FILES[path]
            if file.is_file():
                return self.send_data(200, file.read_bytes(), mime)
        return self.send_data(404, {"error": "찾을 수 없는 경로입니다."})

    def do_POST(self):
        if not self.valid_host() or not secrets.compare_digest(self.headers.get("X-Run-Token", ""), self.server.token):
            return self.send_data(403, {"error": "페이지를 새로고침하고 다시 시도하세요."})
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://localhost:{self.server.server_port}", f"http://127.0.0.1:{self.server.server_port}"}:
            return self.send_data(403, {"error": "다른 사이트에서 실행할 수 없습니다."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError("요청 크기가 올바르지 않습니다.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("JSON 객체가 필요합니다.")
            path = urlsplit(self.path).path
            if path == "/api/runs":
                if not shutil.which("claude"):
                    return self.send_data(503, {"error": "claude CLI가 설치되어 있지 않거나 PATH에 없습니다."})
                run_id = self.server.manager.start(body.get("top_k", 1))
                return self.send_data(202, {"id": run_id})
            if path.startswith("/api/runs/") and path.endswith("/cancel"):
                run_id = path.split("/")[3]
                self.server.manager.snapshot(run_id)
                self.server.manager.cancel(run_id)
                return self.send_data(200, {"cancel_requested": True})
            return self.send_data(404, {"error": "찾을 수 없는 경로입니다."})
        except (ValueError, UnicodeDecodeError) as error:
            return self.send_data(400, {"error": str(error)})
        except KeyError:
            return self.send_data(404, {"error": "실행을 찾을 수 없습니다."})
        except RuntimeError as error:
            return self.send_data(409, {"error": str(error)})


def make_server(port=8002, manager=None):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.manager = manager or RunManager()
    server.token = secrets.token_urlsafe(32)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=180, help="각 Claude 호출의 제한 시간(초)")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout은 양수여야 합니다.")
    manager = RunManager(model=args.model, timeout=args.timeout)
    try:
        server = make_server(args.port, manager)
    except OSError as error:
        parser.exit(1, f"서버 시작 실패: {error}\n기존 서버를 종료하거나 --port 8003을 지정하세요.\n")
    print(f"Local: http://localhost:{server.server_port}/02-rag-amnesia/", flush=True)
    print(f"Model: {args.model} · Claude CLI: {'발견됨' if shutil.which('claude') else '설치 필요'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if manager.active:
            manager.cancel(manager.active)
        if manager.worker:
            manager.worker.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()
