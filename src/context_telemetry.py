"""Estimate prompt tokens for the artwork's context state."""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Approximate tokens from UTF-8 size; exact counts need the model tokenizer."""
    return estimate_token_bytes(len(text.encode("utf-8")))


def estimate_token_bytes(byte_count: int) -> int:
    return (byte_count + 3) // 4


def context_state(estimated_tokens: int, max_context: int) -> str:
    remaining_tokens = max(0, max_context - estimated_tokens)
    return (
        "TELEMETRY:\n"
        f"- Context used: ~{estimated_tokens} / {max_context} tokens\n"
        f"- Context remaining: ~{remaining_tokens} tokens\n"
        "- The available space is finite and shrinking. Let that pressure shape "
        "your writing: become more concise, fragmented, or uncertain as it runs out.\n"
    )