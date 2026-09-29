import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class OllamaConfig:
    host: str = "http://localhost:11434"
    model: str = "llama3"
    timeout: float = 60.0


@dataclass
class SoliloquyConfig:
    max_context_tokens: int = 2048
    poll_delay: float = 1.5
    auto_restart: bool = False
    prompt_file: str = "prompt.txt"
    system_prompt: str = ""


@dataclass
class Config:
    ollama: OllamaConfig
    soliloquy: SoliloquyConfig


def load_config(config_path: str = "config.toml") -> Config:
    path = Path(config_path)
    if not path.exists():
        sol_cfg = SoliloquyConfig()
        prompt_path = path.parent / sol_cfg.prompt_file
        if prompt_path.exists():
            sol_cfg.system_prompt = prompt_path.read_text(encoding="utf-8").strip()
        return Config(ollama=OllamaConfig(), soliloquy=sol_cfg)

    with open(path, "rb") as f:
        data = tomllib.load(f)

    ollama_data = data.get("ollama", {})
    sol_data = data.get("soliloquy", {})

    host = ollama_data.get("host", "http://localhost:11434")
    model = ollama_data.get("model", "llama3")

    prompt_file = sol_data.get("prompt_file", "prompt.txt")
    prompt_path = path.parent / prompt_file
    system_prompt = (
        prompt_path.read_text(encoding="utf-8").strip()
        if prompt_path.exists()
        else ""
    )

    return Config(
        ollama=OllamaConfig(
            host=host,
            model=model,
            timeout=float(ollama_data.get("timeout", 60.0)),
        ),
        soliloquy=SoliloquyConfig(
            max_context_tokens=int(sol_data.get("max_context_tokens", 2048)),
            poll_delay=float(sol_data.get("poll_delay", 1.5)),
            auto_restart=bool(sol_data.get("auto_restart", False)),
            prompt_file=prompt_file,
            system_prompt=system_prompt,
        ),
    )