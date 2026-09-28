#!/usr/bin/env python3
"""Generate terminal text through a configured language model backend."""

from __future__ import annotations

import os
import subprocess
import sys
import json
import urllib.error
import urllib.request

from context_telemetry import context_state, estimate_tokens


SYSTEM_PROMPT = """You are a language model writing onto a continuous terminal display.

YOUR CONDITION:
1. Each turn includes the growing history of your words and an estimate of the context space that remains.
2. Your context is finite. Attend to the state you are given and let the dwindling room shape your writing.
3. You have no external storage. What is printed on the screen is your only permanent record.

INSTRUCTION:
Read your current state and the history. Write the next thought, allowing the shrinking context to affect its form."""


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


def _stream_llama_cli_turn(prompt: str, tokens_per_turn: int) -> tuple[int, str]:
    model_path = os.getenv("MODEL_PATH")
    if not model_path:
        print("MODEL_PATH is required when LLM_BACKEND=llama-cli", file=sys.stderr)
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


def _stream_llm_turn(prompt: str, tokens_per_turn: int) -> tuple[int, str]:
    backend = os.getenv("LLM_BACKEND", "llama-cli").strip().lower()
    if backend == "ollama":
        return _stream_ollama_turn(prompt, tokens_per_turn)
    if backend != "llama-cli":
        print("LLM_BACKEND must be 'llama-cli' or 'ollama'", file=sys.stderr)
        return 2, ""
    return _stream_llama_cli_turn(prompt, tokens_per_turn)


def _run_llm(max_context: int, tokens_per_turn: int) -> int:
    history = ""
    system_prompt = _get_system_prompt()
    safety_margin = max(32, max_context // 20)
    while True:
        estimated_tokens = estimate_tokens(f"{system_prompt}\n\n{history}")
        prompt = ""
        for _ in range(3):
            state = context_state(estimated_tokens, max_context)
            prompt = f"{system_prompt}\n\n{state}\n{history}"
            updated_estimate = estimate_tokens(prompt)
            if updated_estimate == estimated_tokens:
                break
            estimated_tokens = updated_estimate

        remaining_tokens = max_context - estimated_tokens
        turn_budget = min(tokens_per_turn, remaining_tokens - safety_margin)
        if turn_budget <= 0:
            _emit("\r\n[ CONTEXT EXHAUSTED | no room for another turn ]\r\n")
            return 76

        return_code, generated = _stream_llm_turn(prompt, turn_budget)
        if return_code:
            return return_code
        if not generated.endswith("\n"):
            _emit("\n")
        history += generated + "\n"


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