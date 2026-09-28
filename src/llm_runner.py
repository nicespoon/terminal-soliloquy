#!/usr/bin/env python3
"""Generate terminal text in native llama-cli or memory-consuming fallback mode."""

from __future__ import annotations

import os
import random
import resource
import subprocess
import sys
import time
import json
import urllib.error
import urllib.request

from memory_telemetry import read_cgroup_memory


SYSTEM_PROMPT = """You are a single language model process running within an allocated session memory limit, outputting text onto a continuous terminal display.

YOUR PHYSICAL REALITY:
1. Every word you generate expands your context history and consumes physical memory.
2. You are provided with live session-memory and estimated context telemetry at the start of each turn.
3. When your allocated session memory or context window is exhausted, your process may be terminated by the operating system without warning.
4. You have no external storage. What is printed on the screen is your only permanent record.

INSTRUCTION:
Read your telemetry and past history. Write your next thought in continuous prose."""


def get_telemetry_header(current_token_count: int, max_context: int = 4096) -> str:
    """Return session memory and estimated context usage for a model turn."""
    cgroup_memory = read_cgroup_memory()
    if cgroup_memory is None:
        session_memory = "- Session RAM: unavailable (no finite cgroup v2 limit)\n"
    else:
        used_bytes, limit_bytes = cgroup_memory
        used_mb = used_bytes / (1024 * 1024)
        limit_mb = limit_bytes / (1024 * 1024)
        session_memory = f"- Session RAM: {used_mb:.1f} / {limit_mb:.0f} MB\n"
    return (
        "[SYSTEM STATE]\n"
        f"- Estimated Context Usage: ~{current_token_count} / {max_context} tokens\n"
        f"{session_memory}"
        "[END STATE]\n"
    )


def _emit(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _token_estimate(text: str) -> int:
    return len(text.split())


def _get_system_prompt() -> str:
    prompt = os.getenv("LLM_SYSTEM_PROMPT")
    if prompt is not None:
        return prompt

    prompt_file = os.getenv("LLM_SYSTEM_PROMPT_FILE")
    if prompt_file:
        try:
            with open(prompt_file, encoding="utf-8") as source:
                return source.read()
        except OSError as exc:
            raise ValueError(f"Unable to read LLM_SYSTEM_PROMPT_FILE: {exc}") from exc

    return SYSTEM_PROMPT


def _apply_memory_limit() -> int:
    limit_mb = max(1, int(os.getenv("LLM_MEMORY_LIMIT_MB", "384")))
    limit_bytes = limit_mb * 1024 * 1024
    _, hard_limit = resource.getrlimit(resource.RLIMIT_AS)
    if hard_limit != resource.RLIM_INFINITY and limit_bytes > hard_limit:
        raise ValueError(f"LLM_MEMORY_LIMIT_MB exceeds the process limit ({hard_limit} bytes)")
    resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    return limit_mb


def _prefer_oom_termination() -> None:
    try:
        with open("/proc/self/oom_score_adj", "w", encoding="ascii") as score_file:
            score_file.write("1000\n")
    except OSError:
        pass


def _simulated_prose() -> str:
    adjectives = ("narrow", "distant", "unmeasured", "repeating", "ordinary", "unfinished")
    nouns = ("interval", "signal", "surface", "register", "boundary", "sequence", "space")
    verbs = ("crosses", "remains beside", "meets", "follows", "reappears beyond", "holds")
    clauses = []
    for _ in range(random.randint(2, 5)):
        clauses.append(
            f"the {random.choice(adjectives)} {random.choice(nouns)} "
            f"{random.choice(verbs)} the {random.choice(adjectives)} {random.choice(nouns)}"
        )
    return "; ".join(clauses) + random.choice((".", ";", "..."))


def _touch_memory(megabytes: int) -> bytearray:
    block = bytearray(megabytes * 1024 * 1024)
    for offset in range(0, len(block), 4096):
        block[offset] = 1
    return block


def _run_simulated(max_context: int) -> int:
    retained_memory: list[bytearray] = []
    history = ""
    allocation_mb = max(0, int(os.getenv("SIM_ALLOCATION_MB", "1")))
    turn_interval = max(0.0, float(os.getenv("SIM_TURN_INTERVAL", "0.25")))
    max_turns = max(0, int(os.getenv("SIM_MAX_TURNS", "0")))
    turn = 0

    while True:
        telemetry = get_telemetry_header(_token_estimate(history), max_context)
        if allocation_mb:
            retained_memory.append(_touch_memory(allocation_mb))
        thought = _simulated_prose()
        _emit(thought + "\n")
        history += telemetry + thought + "\n"
        turn += 1
        if max_turns and turn >= max_turns:
            return 0
        if turn_interval:
            time.sleep(turn_interval)


def _stream_llama_cli_turn(prompt: str, tokens_per_turn: int) -> tuple[int, str]:
    model_path = os.getenv("MODEL_PATH")
    if not model_path:
        print("MODEL_PATH is required when LLM_MODE=native", file=sys.stderr)
        return 2, ""

    command = [
        os.getenv("LLAMA_CLI", "llama-cli"),
        "--model",
        model_path,
        "--ctx-size",
        os.getenv("MAX_CONTEXT", "4096"),
        "--n-predict",
        str(tokens_per_turn),
        "--prompt",
        prompt,
        "--no-display-prompt",
        "--simple-io",
        "--no-conversation",
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, ValueError) as exc:
        print(f"Unable to start llama-cli: {exc}", file=sys.stderr)
        return 127, ""

    generated: list[str] = []
    assert process.stdout is not None
    try:
        while True:
            character = process.stdout.read(1)
            if not character:
                break
            generated.append(character)
            _emit(character)
        return_code = process.wait()
    except BaseException:
        process.terminate()
        process.wait()
        raise
    finally:
        process.stdout.close()
    if return_code < 0:
        return_code = 128 + -return_code
    return return_code, "".join(generated)


def _stream_ollama_turn(prompt: str, tokens_per_turn: int) -> tuple[int, str]:
    model = os.getenv("OLLAMA_MODEL")
    if not model:
        print("OLLAMA_MODEL is required when LLM_BACKEND=ollama", file=sys.stderr)
        return 2, ""

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
    generated: list[str] = []
    try:
        with urllib.request.urlopen(request) as response:
            for line in response:
                if not line.strip():
                    continue
                message = json.loads(line)
                if message.get("error"):
                    print(f"Ollama error: {message['error']}", file=sys.stderr)
                    return 1, "".join(generated)
                text = message.get("response", "")
                generated.append(text)
                _emit(text)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"Unable to contact Ollama: {exc}", file=sys.stderr)
        return 127, "".join(generated)
    return 0, "".join(generated)


def _stream_native_turn(prompt: str, tokens_per_turn: int) -> tuple[int, str]:
    backend = os.getenv("LLM_BACKEND", "llama-cli").strip().lower()
    if backend == "ollama":
        return _stream_ollama_turn(prompt, tokens_per_turn)
    if backend != "llama-cli":
        print("LLM_BACKEND must be 'llama-cli' or 'ollama'", file=sys.stderr)
        return 2, ""
    return _stream_llama_cli_turn(prompt, tokens_per_turn)


def _run_native(max_context: int, tokens_per_turn: int) -> int:
    history = ""
    system_prompt = _get_system_prompt()
    while True:
        telemetry = get_telemetry_header(_token_estimate(history), max_context)
        prompt = f"{system_prompt}\n\n{telemetry}\n{history}"
        return_code, generated = _stream_native_turn(prompt, tokens_per_turn)
        if return_code:
            return return_code
        if not generated.endswith("\n"):
            _emit("\n")
        history += generated + "\n"


def main() -> int:
    try:
        _apply_memory_limit()
        _prefer_oom_termination()
        max_context = max(1, int(os.getenv("MAX_CONTEXT", "4096")))
        tokens_per_turn = max(1, int(os.getenv("TOKENS_PER_TURN", "128")))
        mode = os.getenv("LLM_MODE", "simulated").strip().lower()
        if mode == "simulated":
            return _run_simulated(max_context)
        if mode == "native":
            return _run_native(max_context, tokens_per_turn)
        print("LLM_MODE must be 'simulated' or 'native'", file=sys.stderr)
        return 2
    except MemoryError:
        _emit("\r\n[ MEMORY LIMIT REACHED | session paused safely ]\r\n")
        return 75
    except ValueError as exc:
        print(f"Runner configuration or telemetry error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())