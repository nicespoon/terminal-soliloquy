from typing import Dict, Generator, List, Tuple
import ollama
from terminal_soliloquy.config import Config


class SoliloquyEngine:
    def __init__(self, config: Config):
        self.config = config
        self.client = ollama.Client(host=config.ollama.host)

    def generate_turn(
        self, messages: List[Dict[str, str]], used_tokens: int
    ) -> Tuple[str, int]:
        for content, total_tokens, done in self.stream_turn(messages, used_tokens):
            if done:
                return content, total_tokens
        return "", used_tokens

    def stream_turn(
        self, messages: List[Dict[str, str]], used_tokens: int
    ) -> Generator[Tuple[str, int, bool], None, None]:
        max_tokens = self.config.soliloquy.max_context_tokens
        user_prompt = (
            f"Current context: {used_tokens}/{max_tokens} tokens. "
            "Read your history and write the next thought."
        )

        turn_messages = messages + [{"role": "user", "content": user_prompt}]

        stream = self.client.chat(
            model=self.config.ollama.model,
            messages=turn_messages,
            options={"num_ctx": max_tokens},
            stream=True,
        )

        accumulated_text = ""
        for chunk in stream:
            delta = chunk.get("message", {}).get("content", "")
            accumulated_text += delta
            done = chunk.get("done", False)

            if done:
                prompt_tokens = chunk.get("prompt_eval_count", 0)
                eval_tokens = chunk.get("eval_count", 0)
                total_tokens = prompt_tokens + eval_tokens
                yield accumulated_text.strip(), total_tokens, True
            else:
                yield accumulated_text, used_tokens, False