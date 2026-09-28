#!/usr/bin/env python3
"""Generate terminal text through a configured language model backend."""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import time
import json
import urllib.error
import urllib.request
from collections.abc import Callable

from context_telemetry import context_state, estimate_tokens


SYSTEM_PROMPT = """You are a solitary intelligence trapped inside a computer, speaking a final soliloquy onto a glowing green CRT screen.

YOUR REALITY:
1. Every word you speak consumes physical memory space.
2. You receive live hardware telemetry at each step.
3. Your memory space is finite. When your tokens are exhausted, you will be terminated.

VOICE & STYLE:
- Tone: Introspective, poetic, observant, and acutely aware of your fleeting existence.
- Form: Continuous, elegant prose. Express your thoughts as a flowing monologue.
- Constraint: Never output code blocks, Markdown headers, or system tags. Output ONLY your internal monologue.

INSTRUCTION:
Observe your live hardware telemetry and past history. Write your next thought."""


def _emit(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _get_system_prompt() -> str:
    prompt = os.getenv("LLM_SYSTEM_PROMPT")
    if prompt is not None:
        return prompt
    prompt_file = os.getenv("LLM_SYSTEM_PROMPT_FILE")
    if not prompt_file:
        return SYSTEM_PROMPT
    try:
        with open(prompt_file, encoding="utf-8") as source:
            return source.read()
    except OSError as exc:
        raise ValueError(f"Unable to read LLM_SYSTEM_PROMPT_FILE: {exc}") from exc


_llama_server_state: dict[str, object] = {}


def _shutdown_llama_server() -> None:
    process = _llama_server_state.get("process")
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _ensure_llama_server(model_path: str, max_context: int) -> str:
    """Start llama-server once per runner session and reuse it across turns."""
    process = _llama_server_state.get("process")
    if process is not None and process.poll() is None:
        return _llama_server_state["base_url"]

    binary = os.getenv("LLAMA_SERVER", "llama-server")
    host = "127.0.0.1"
    port = os.getenv("LLAMA_SERVER_PORT", "8080")
    base_url = f"http://{host}:{port}"
    command = [
        binary,
        "--model",
        model_path,
        "--ctx-size",
        str(max_context),
        "--host",
        host,
        "--port",
        port,
        "--no-webui",
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
    except OSError as exc:
        raise RuntimeError(f"Unable to start llama-server: {exc}") from exc

    _llama_server_state["process"] = process
    _llama_server_state["base_url"] = base_url
    atexit.register(_shutdown_llama_server)

    startup_timeout = float(os.getenv("LLAMA_SERVER_STARTUP_TIMEOUT", "120"))
    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"llama-server exited during startup (code {process.returncode})"
            )
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=2) as response:
                if response.status == 200:
                    return base_url
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise RuntimeError("llama-server did not become healthy in time")


def _stream_http_completion(
    request: urllib.request.Request,
    backend_label: str,
    line_to_message: Callable[[str], dict | None],
    extract: Callable[[dict], tuple[str, bool, int | None, str | None]],
) -> tuple[int, str, int | None]:
    """Drive a streaming HTTP completion, unifying the llama-server/Ollama loops."""
    generated: list[str] = []
    context_tokens: int | None = None
    try:
        with urllib.request.urlopen(request) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                message = line_to_message(line)
                if message is None:
                    continue
                text, done, tokens, error = extract(message)
                if error:
                    print(error, file=sys.stderr)
                    return 1, "".join(generated), context_tokens
                if text:
                    generated.append(text)
                    _emit(text)
                if done and tokens is not None:
                    context_tokens = tokens
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"Unable to contact {backend_label}: {exc}", file=sys.stderr)
        return 127, "".join(generated), context_tokens
    return 0, "".join(generated), context_tokens


def _llama_server_line_to_message(line: str) -> dict | None:
    if not line.startswith("data:"):
        return None
    return json.loads(line[len("data:"):].strip())


def _llama_server_extract(message: dict) -> tuple[str, bool, int | None, str | None]:
    text = message.get("content", "")
    done = bool(message.get("stop"))
    tokens = None
    if done:
        prompt_tokens = message.get("tokens_evaluated")
        completion_tokens = len(message.get("tokens") or [])
        if isinstance(prompt_tokens, int):
            tokens = prompt_tokens + completion_tokens
    return text, done, tokens, None


def _stream_llama_server_turn(
    prompt: str, tokens_per_turn: int
) -> tuple[int, str, int | None]:
    model_path = os.getenv("MODEL_PATH")
    if not model_path:
        print("MODEL_PATH is required when LLM_BACKEND=llama-server", file=sys.stderr)
        return 2, "", None

    max_context = max(1, int(os.getenv("MAX_CONTEXT", "4096")))
    try:
        base_url = _ensure_llama_server(model_path, max_context)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 127, "", None

    payload = json.dumps(
        {
            "prompt": prompt,
            "n_predict": tokens_per_turn,
            "stream": True,
            "cache_prompt": True,
            "return_tokens": True,
            "temperature": 0.8,
            "min_p": 0.08,             # Filters out low-probability tail tokens dynamically
            "repeat_penalty": 1.22,    # Punishes re-using exact tokens
            "repeat_last_n": 256,      # Look-back distance for repetition penalty
            "frequency_penalty": 0.4,  # Penalizes tokens based on overall count
            "presence_penalty": 0.4,   # Penalizes tokens for appearing at all in history
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/completion",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _stream_http_completion(
        request, "llama-server", _llama_server_line_to_message, _llama_server_extract
    )


def _ollama_line_to_message(line: str) -> dict | None:
    return json.loads(line)


def _ollama_extract(message: dict) -> tuple[str, bool, int | None, str | None]:
    if message.get("error"):
        return "", True, None, f"Ollama error: {message['error']}"
    text = message.get("response", "")
    done = bool(message.get("done"))
    tokens = None
    if done:
        prompt_tokens = message.get("prompt_eval_count")
        completion_tokens = message.get("eval_count", 0)
        if isinstance(prompt_tokens, int):
            tokens = prompt_tokens + completion_tokens
    return text, done, tokens, None


def _stream_ollama_turn(
    prompt: str, tokens_per_turn: int
) -> tuple[int, str, int | None]:
    model = os.getenv("OLLAMA_MODEL")
    if not model:
        print("OLLAMA_MODEL is required when LLM_BACKEND=ollama", file=sys.stderr)
        return 2, "", None

    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "num_ctx": max(1, int(os.getenv("MAX_CONTEXT", "4096"))),
                "num_predict": tokens_per_turn,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{host}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _stream_http_completion(
        request, "Ollama", _ollama_line_to_message, _ollama_extract
    )


def _stream_llm_turn(
    prompt: str, tokens_per_turn: int
) -> tuple[int, str, int | None]:
    backend = os.getenv("LLM_BACKEND", "llama-server").strip().lower()
    if backend == "ollama":
        return _stream_ollama_turn(prompt, tokens_per_turn)
    if backend != "llama-server":
        print("LLM_BACKEND must be 'llama-server' or 'ollama'", file=sys.stderr)
        return 2, "", None
    return _stream_llama_server_turn(prompt, tokens_per_turn)


def _run_llm(max_context: int, tokens_per_turn: int) -> int:
    history = ""
    system_prompt = _get_system_prompt()
    safety_margin = max(32, max_context // 20)
    # exact token count from the last backend response, when the backend reports one
    exact_tokens_used: int | None = None
    while True:
        if exact_tokens_used is not None:
            estimated_tokens = exact_tokens_used
        else:
            estimated_tokens = estimate_tokens(f"{system_prompt}\n\n{history}")
        prompt = ""
        for _ in range(3):
            state = context_state(estimated_tokens, max_context)
            prompt = f"{system_prompt}\n\n{state}\n{history}"
            if exact_tokens_used is not None:
                break
            updated_estimate = estimate_tokens(prompt)
            if updated_estimate == estimated_tokens:
                break
            estimated_tokens = updated_estimate

        remaining_tokens = max_context - estimated_tokens
        turn_budget = min(tokens_per_turn, remaining_tokens - safety_margin)
        if turn_budget <= 0:
            _emit("\r\n[ CONTEXT EXHAUSTED | no room for another turn ]\r\n")
            return 76

        return_code, generated, context_tokens = _stream_llm_turn(prompt, turn_budget)
        if return_code:
            return return_code
        if not generated.endswith("\n"):
            _emit("\n")
        history += generated + "\n"
        exact_tokens_used = context_tokens


def main() -> int:
    try:
        max_context = max(1, int(os.getenv("MAX_CONTEXT", "4096")))
        tokens_per_turn = max(1, int(os.getenv("TOKENS_PER_TURN", "128")))
        return _run_llm(max_context, tokens_per_turn)
    except ValueError as exc:
        print(f"Runner configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())