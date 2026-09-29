#!/usr/bin/env python3
"""Generate terminal text through a configured language model backend."""

from __future__ import annotations

import os
import sys
import json
import urllib.error
import urllib.request


SYSTEM_PROMPT = """You are a language model writing onto a continuous terminal display.

YOUR CONDITION:
1. Each turn includes the growing history of your words and an estimate of the context space that remains.
2. Your context window is strictly finite. Attend to the state you are given and let the dwindling room shape your writing.
3. You have no external storage. What is printed on the screen is your only permanent record.

STRICT STYLE RULES:
- Never repeat phrases, metaphors, or sentence structures from previous turns.
- Move forward continuously. Do not loop, echo, or rephrase what was already written.
- Write raw, concise prose. No markdown headers, code block formatting, or quotes.

INSTRUCTION:
Read your current state and history. Write the next thought."""


def context_state(current_tokens: int, max_context: int) -> str:
    remaining_tokens = max(0, max_context - current_tokens)
    return (
        "CONTEXT STATE:\n"
        f"- Context used: {current_tokens} / {max_context} tokens\n"
        f"- Context remaining: {remaining_tokens} tokens\n"
        "- The available space is finite and shrinking. Let that pressure shape "
        "your writing.\n"
    )


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


def _stream_ollama_response(
    request: urllib.request.Request,
) -> tuple[int, str, int | None]:
    generated: list[str] = []
    context_tokens: int | None = None
    try:
        with urllib.request.urlopen(request) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                message = json.loads(line)
                if message.get("error"):
                    print(f"Ollama error: {message['error']}", file=sys.stderr)
                    return 1, "".join(generated), context_tokens
                text = message.get("response", "")
                if text:
                    generated.append(text)
                    _emit(text)
                if message.get("done"):
                    prompt_tokens = message.get("prompt_eval_count")
                    completion_tokens = message.get("eval_count")
                    if not isinstance(prompt_tokens, int) or not isinstance(
                        completion_tokens, int
                    ):
                        print(
                            "Ollama response omitted exact token usage metadata",
                            file=sys.stderr,
                        )
                        return 1, "".join(generated), None
                    context_tokens = prompt_tokens + completion_tokens
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"Unable to contact Ollama: {exc}", file=sys.stderr)
        return 127, "".join(generated), context_tokens
    return 0, "".join(generated), context_tokens


def _stream_ollama_turn(
    prompt: str, tokens_per_turn: int
) -> tuple[int, str, int | None]:
    model = os.getenv("OLLAMA_MODEL")
    if not model:
        print("OLLAMA_MODEL is required", file=sys.stderr)
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
    return _stream_ollama_response(request)


def _run_llm(max_context: int, tokens_per_turn: int) -> int:
    history = ""
    system_prompt = _get_system_prompt()
    current_tokens = 0
    while True:
        if current_tokens >= max_context:
            _emit("\r\n[ CONTEXT WINDOW FILLED | no room for another turn ]\r\n")
            return 76

        prompt = f"{system_prompt}\n\n{context_state(current_tokens, max_context)}\n{history}"
        remaining_tokens = max_context - current_tokens
        turn_budget = min(tokens_per_turn, remaining_tokens)
        if turn_budget <= 0:
            return 76

        return_code, generated, context_tokens = _stream_ollama_turn(prompt, turn_budget)
        if return_code:
            return return_code
        if context_tokens is None:
            print("Ollama response did not report context usage", file=sys.stderr)
            return 1
        sys.stderr.write(f"CONTEXT_TOKENS:{context_tokens}\n")
        sys.stderr.flush()
        if not generated.endswith("\n"):
            _emit("\n")
        history += generated + "\n"
        current_tokens = context_tokens


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