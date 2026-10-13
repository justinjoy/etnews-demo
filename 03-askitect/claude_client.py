"""Actual Claude CLI adapter. No rule-based or prerecorded fallback."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

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


def generate(prompt, model, cancel, timeout=180, system_prompt="주어진 자료만 사용하여 한국어로 답하세요."):
    executable = shutil.which("claude")
    if not executable:
        raise GenerationError("claude CLI를 찾을 수 없습니다. README.txt의 설치 및 로그인 안내를 확인하세요.")
    command = [
        executable, "-p", "--model", model, "--system-prompt", system_prompt,
        "--output-format", "json", "--tools", "", "--safe-mode",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--no-session-persistence",
    ]
    started = time.monotonic()
    # Each call starts fresh: no resume, no project instructions, tools or MCP.
    with subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", cwd=Path(__file__).resolve().parent, start_new_session=(os.name == "posix"),
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

