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
    poll_delay: float = 1.5
    auto_restart: bool = False
    prompt_file: str = "prompt.txt"
    system_prompt: str = ""
    screen_padding: Tuple[int, ...] = (0, 0)


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

    return Config(
        llamacpp=LlamaCppConfig(
            host=host,
            model=model,
            timeout=float(llama_data.get("timeout", 60.0)),
        ),
        soliloquy=SoliloquyConfig(
            max_context_tokens=int(sol_data.get("max_context_tokens", 2048)),
            poll_delay=float(sol_data.get("poll_delay", 1.5)),
            auto_restart=bool(sol_data.get("auto_restart", False)),
            prompt_file=prompt_file,
            system_prompt=system_prompt,
            screen_padding=screen_padding,
        ),
    )