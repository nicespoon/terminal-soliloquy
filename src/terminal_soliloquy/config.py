import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class LlamaCppConfig:
    host: str = "http://localhost:8080"
    model: str = "default"
    timeout: float = 60.0


@dataclass
class SoliloquyConfig:
    max_context_tokens: int = 2048
    end_behavior: str = "freeze"
    restart_duration: float = 30.0
    quit_duration: float = 0.0
    auto_restart: bool = False
    prompt_file: str = "prompt.txt"
    system_prompt: str = ""
    screen_padding: Tuple[int, ...] = (0, 0)

    def __post_init__(self):
        valid_behaviors = ("restart", "freeze", "quit")
        if self.end_behavior not in valid_behaviors:
            self.end_behavior = "restart" if self.auto_restart else "freeze"
        else:
            self.auto_restart = (self.end_behavior == "restart")


@dataclass
class Config:
    llamacpp: LlamaCppConfig
    soliloquy: SoliloquyConfig


def load_config(config_path: str = "config.toml") -> Config:
    path = Path(config_path)
    if not path.exists():
        sol_cfg = SoliloquyConfig()
        prompt_path = path.parent / sol_cfg.prompt_file
        if prompt_path.exists():
            sol_cfg.system_prompt = prompt_path.read_text(encoding="utf-8").strip()
        return Config(llamacpp=LlamaCppConfig(), soliloquy=sol_cfg)

    with open(path, "rb") as f:
        data = tomllib.load(f)

    llama_data = data.get("llamacpp", data.get("ollama", {}))
    sol_data = data.get("soliloquy", {})

    host = llama_data.get("host", "http://localhost:8080")
    model = llama_data.get("model", "default")

    prompt_file = sol_data.get("prompt_file", "prompt.txt")
    prompt_path = path.parent / prompt_file
    system_prompt = (
        prompt_path.read_text(encoding="utf-8").strip()
        if prompt_path.exists()
        else ""
    )

    raw_padding = sol_data.get("screen_padding", (0, 0))
    if isinstance(raw_padding, list):
        screen_padding = tuple(raw_padding)
    elif isinstance(raw_padding, int):
        screen_padding = (raw_padding,)
    elif isinstance(raw_padding, tuple):
        screen_padding = raw_padding
    else:
        screen_padding = (0, 0)

    raw_end_behavior = sol_data.get("end_behavior", sol_data.get("end_behaviour"))
    if raw_end_behavior is not None:
        end_behavior = str(raw_end_behavior).strip().lower()
        if end_behavior not in ("restart", "freeze", "quit"):
            end_behavior = "freeze"
    elif "auto_restart" in sol_data:
        end_behavior = "restart" if sol_data.get("auto_restart") else "freeze"
    else:
        end_behavior = "freeze"

    try:
        restart_duration = max(
            0.0,
            float(
                sol_data.get(
                    "restart_duration",
                    sol_data.get("restart_timer", sol_data.get("restart_delay", 30.0)),
                )
            ),
        )
    except (TypeError, ValueError):
        restart_duration = 30.0

    try:
        quit_duration = max(
            0.0,
            float(
                sol_data.get(
                    "quit_duration",
                    sol_data.get("quit_timer", sol_data.get("quit_delay", 0.0)),
                )
            ),
        )
    except (TypeError, ValueError):
        quit_duration = 0.0

    return Config(
        llamacpp=LlamaCppConfig(
            host=host,
            model=model,
            timeout=float(llama_data.get("timeout", 60.0)),
        ),
        soliloquy=SoliloquyConfig(
            max_context_tokens=int(sol_data.get("max_context_tokens", 2048)),
            end_behavior=end_behavior,
            restart_duration=restart_duration,
            quit_duration=quit_duration,
            auto_restart=(end_behavior == "restart"),
            prompt_file=prompt_file,
            system_prompt=system_prompt,
            screen_padding=screen_padding,
        ),
    )