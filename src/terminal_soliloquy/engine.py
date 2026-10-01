import json
import urllib.request
import urllib.error
from typing import Dict, Generator, List, Tuple
from terminal_soliloquy.config import Config


class SoliloquyEngine:
    def __init__(self, config: Config):
        self.config = config

    def format_prompt(self, messages: List[Dict[str, str]]) -> str:
        base_url = self.config.llamacpp.host.rstrip("/")
        endpoint = f"{base_url}/apply-template"

        req = urllib.request.Request(
            endpoint,
            data=json.dumps({
                "messages": messages,
                "chat_template_kwargs": {"enable_thinking": False},
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.config.llamacpp.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                if "prompt" in data:
                    return data["prompt"]
        except Exception:
            pass

        # Fallback to ChatML if /apply-template is unreachable
        formatted = [
            f"<|im_start|>{msg.get('role', '')}\n{msg.get('content', '')}<|im_end|>\n"
            for msg in messages
        ]
        formatted.append("<|im_start|>assistant\n")
        return "".join(formatted)

    def get_model_name(self) -> str:
        base_url = self.config.llamacpp.host.rstrip("/")
        endpoint = f"{base_url}/v1/models"

        req = urllib.request.Request(
            endpoint,
            headers={"Accept": "application/json"},
            method="GET",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.config.llamacpp.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                if "data" in data and len(data["data"]) > 0:
                    model_id = data["data"][0].get("id", "")
                    # Extract filename from full path and trim file extensions
                    clean_name = model_id.split("/")[-1].split("\\")[-1]
                    for ext in [".gguf", ".bin"]:
                        if clean_name.lower().endswith(ext):
                            clean_name = clean_name[:-len(ext)]
                    return clean_name or self.config.llamacpp.model
        except Exception:
            pass

        return self.config.llamacpp.model

    def get_token_count(self, text: str) -> int:
        """Fetches the exact token count using llama-server's /tokenize endpoint."""
        base_url = self.config.llamacpp.host.rstrip("/")
        endpoint = f"{base_url}/tokenize"
        
        req = urllib.request.Request(
            endpoint,
            data=json.dumps({"content": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        try:
            with urllib.request.urlopen(req, timeout=self.config.llamacpp.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                return len(data.get("tokens", []))
        except Exception:
            return 0  # Fallback gracefully if the endpoint is unreachable

    def stream_turn(
        self, messages: List[Dict[str, str]], used_tokens: int
    ) -> Generator[Tuple[str, int, bool], None, None]:
        max_tokens = self.config.soliloquy.max_context_tokens
        
        percent_used = int((used_tokens / max_tokens) * 100) if max_tokens > 0 else 0
        percent_remaining = max(0, 100 - percent_used)

        turn_messages = messages + [{
            "role": "user",
            "content": (
                f"System status: {percent_remaining}% context capacity remaining. "
                "Log your next thought directly without using any numbers or percentages."
            )
        }]

        prompt_str = self.format_prompt(turn_messages)
        
        # 1. Fetch exact prompt token count before generation begins
        prompt_tokens = self.get_token_count(prompt_str)
        if prompt_tokens == 0:
            prompt_tokens = used_tokens  # Fallback

        remaining_tokens = max_tokens - prompt_tokens
        if remaining_tokens <= 0:
            yield "", prompt_tokens, False
            yield "", prompt_tokens, True
            return
            
        base_url = self.config.llamacpp.host.rstrip("/")
        endpoint = base_url if base_url.endswith("/completion") else f"{base_url}/completion"

        req = urllib.request.Request(
            endpoint,
            data=json.dumps({"prompt": prompt_str, "stream": True, "n_predict": remaining_tokens}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        accumulated_text = ""
        reported_tokens = None
        generated_tokens = 0
        current_total = prompt_tokens

        try:
            with urllib.request.urlopen(req, timeout=self.config.llamacpp.timeout) as response:
                
                # 2. Yield the exact prompt count instantly to update the UI on frame 1
                yield accumulated_text, current_total, False
                
                for line in response:
                    line_str = line.decode("utf-8").strip()
                    if not line_str.startswith("data: "):
                        continue

                    try:
                        chunk = json.loads(line_str[6:].strip())
                    except json.JSONDecodeError:
                        continue

                    accumulated_text += chunk.get("content", "")

                    # 3. Increment live generated tokens per chunk
                    if "tokens" in chunk:
                        generated_tokens += len(chunk["tokens"])
                    else:
                        generated_tokens += 1
                        
                    current_total = prompt_tokens + generated_tokens

                    # 4. Overwrite with exact server metrics on completion
                    if "tokens_evaluated" in chunk and "tokens_predicted" in chunk:
                        reported_tokens = chunk["tokens_evaluated"] + chunk["tokens_predicted"]
                    elif "timings" in chunk and isinstance(chunk["timings"], dict):
                        t = chunk["timings"]
                        reported_tokens = t.get("prompt_n", 0) + t.get("predicted_n", 0)

                    if reported_tokens is not None:
                        current_total = reported_tokens
                        
                    done = chunk.get("stop", False)

                    yield accumulated_text, current_total, False

                    if done or current_total >= max_tokens:
                        break

            final_total = reported_tokens if reported_tokens is not None else current_total
            yield accumulated_text.strip(), final_total, True

        except urllib.error.URLError as e:
            raise RuntimeError(f"Connection failed to llama-cpp host ({self.config.llamacpp.host}): {e.reason}")
        except Exception as e:
            raise RuntimeError(f"llama-cpp error: {e}")