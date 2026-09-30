import json
import urllib.request
import urllib.error
from typing import Dict, Generator, List, Tuple
from terminal_soliloquy.config import Config


class SoliloquyEngine:
    def __init__(self, config: Config):
        self.config = config

    def format_prompt(self, messages: List[Dict[str, str]]) -> str:
        """Formats standard message dicts into a ChatML text prompt for native /completion."""
        formatted = [
            f"<|im_start|>{msg.get('role', '')}\n{msg.get('content', '')}<|im_end|>\n"
            for msg in messages
        ]
        formatted.append("<|im_start|>assistant\n")
        return "".join(formatted)

    def stream_turn(
        self, messages: List[Dict[str, str]], used_tokens: int
    ) -> Generator[Tuple[str, int, bool], None, None]:
        max_tokens = self.config.soliloquy.max_context_tokens
        
        # Convert token usage to a percentage so the model feels the limit approaching without seeing exact counts
        percent_used = int((used_tokens / max_tokens) * 100) if max_tokens > 0 else 0
        
        turn_messages = messages + [{
            "role": "user",
            "content": f"System status: Memory at {percent_used}% capacity. Log your next thought directly without using any numbers."
        }]

        prompt_str = self.format_prompt(turn_messages)
        base_url = self.config.llamacpp.host.rstrip("/")
        endpoint = base_url if base_url.endswith("/completion") else f"{base_url}/completion"

        req = urllib.request.Request(
            endpoint,
            data=json.dumps({"prompt": prompt_str, "stream": True, "n_predict": -1}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        accumulated_text = ""
        reported_tokens = None

        try:
            with urllib.request.urlopen(req, timeout=self.config.llamacpp.timeout) as response:
                for line in response:
                    line_str = line.decode("utf-8").strip()
                    if not line_str.startswith("data: "):
                        continue

                    try:
                        chunk = json.loads(line_str[6:].strip())
                    except json.JSONDecodeError:
                        continue

                    accumulated_text += chunk.get("content", "")

                    # Parse native llama-server token counts
                    if "tokens_evaluated" in chunk and "tokens_predicted" in chunk:
                        reported_tokens = chunk["tokens_evaluated"] + chunk["tokens_predicted"]
                    elif "timings" in chunk and isinstance(chunk["timings"], dict):
                        t = chunk["timings"]
                        reported_tokens = t.get("prompt_n", 0) + t.get("predicted_n", 0)

                    current_total = reported_tokens if reported_tokens is not None else used_tokens
                    done = chunk.get("stop", False)

                    yield accumulated_text, current_total, False

                    if done:
                        break

            # Fallback estimation if the server didn't report exact token counts
            final_total = reported_tokens if reported_tokens is not None else max(
                used_tokens + 10, (len(prompt_str) + len(accumulated_text)) // 4
            )
            yield accumulated_text.strip(), final_total, True

        except urllib.error.URLError as e:
            raise RuntimeError(f"Connection failed to llama-cpp host ({self.config.llamacpp.host}): {e.reason}")
        except Exception as e:
            raise RuntimeError(f"llama-cpp error: {e}")