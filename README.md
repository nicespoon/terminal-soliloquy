# Terminal Soliloquy

Terminal Soliloquy is a fullscreen, phosphor-green terminal artwork for Linux systems using systemd and Wayland. It displays live memory telemetry while a local language model or a lightweight text simulator writes at a pace shaped by available RAM.

Sessions have explicit memory boundaries. The runner receives a per-process address-space limit, and systemd places the complete display service in a memory-limited cgroup. If Python reaches its limit, it catches `MemoryError` and pauses normally. The supervisor keeps the last frame visible and offers keyboard restart, quit, or a configurable automatic restart.

## Install
Run these commands from a terminal in the graphical Wayland session so the user service manager receives the display environment.


```sh
cd ~
git clone https://github.com/nicespoon/terminal-soliloquy
cd terminal-soliloquy
sudo apt update
sudo apt install -y foot python3-venv
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p ~/.config/systemd/user
ln -s ~/terminal-soliloquy/systemd/terminal-soliloquy.service \
	~/.config/systemd/user/terminal-soliloquy.service
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR
systemctl --user daemon-reload
systemctl --user enable --now terminal-soliloquy.service
```

The unit expects the checkout at `~/terminal-soliloquy`. Check the service with `systemctl --user status terminal-soliloquy.service` and `journalctl --user -u terminal-soliloquy.service`.

## Controls

When a runner session ends, the last frame remains on screen. Press `R` to restart immediately or `Q` to quit the display service. By default, it restarts automatically after 30 seconds; set `AUTO_RESTART_DELAY=0` for a manual-only pause. A keyboard must be connected for the manual controls.

## Memory Limits

The defaults are deliberately conservative for a system with 1 GB of RAM:

- `LLM_MEMORY_LIMIT_MB=384` limits the runner and its model subprocess address space. The simulator stops cleanly when it reaches this limit.
- `MemoryHigh=448M` and `MemoryMax=512M` bound aggregate memory for the service, including `foot` and the supervisor. This cgroup ceiling protects the rest of the system if a native model does not handle an allocation failure cleanly.
- `OOMPolicy=continue` keeps the supervisor alive when a process in the service cgroup is terminated.

To adjust the budget, create a service drop-in:

```sh
systemctl --user edit terminal-soliloquy.service
```

For example, to allow a larger model while retaining a system memory guard:

```ini
[Service]
Environment=LLM_MEMORY_LIMIT_MB=512
MemoryHigh=576M
MemoryMax=640M
```

`LLM_MEMORY_LIMIT_MB` is a virtual address-space limit, not an exact resident-memory measurement. The systemd limits apply to the whole service, so leave room above the runner limit for `foot` and the supervisor. Choose a ceiling that leaves enough RAM for the operating system. Apply changes with `systemctl --user daemon-reload` and restart the service.

## Language Model

The default `LLM_MODE=simulated` runs without model weights and uses bounded allocations to demonstrate the pause/restart flow. For a local GGUF model, install a compatible `llama-cli` build and configure a model path with `systemctl --user edit terminal-soliloquy.service`.

```ini
[Service]
Environment=LLM_MODE=native
Environment=MODEL_PATH=/path/to/model.gguf
Environment=LLAMA_CLI=/usr/local/bin/llama-cli
Environment=LLM_SYSTEM_PROMPT_FILE=%h/terminal-soliloquy/prompt.txt
```

Native mode injects fresh `psutil` telemetry at the start of every turn along with a system prompt describing the process's physical constraints. By default it uses the built-in prompt; set `LLM_SYSTEM_PROMPT_FILE` to a UTF-8 text file to replace it, or set `LLM_SYSTEM_PROMPT` directly in the service drop-in. The direct value takes precedence if both are set.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `LLM_MEMORY_LIMIT_MB` | `384` | Runner/model virtual address-space ceiling in MB. |
| `MemoryHigh` | `448M` | systemd service memory pressure threshold. |
| `MemoryMax` | `512M` | Hard aggregate memory ceiling for the service cgroup. |
| `AUTO_RESTART_DELAY` | `30` | Restart seconds; `0` waits for `R` or `Q`. |
| `SHOW_DIAGNOSTICS` | `true` | Show a short session status and memory summary after a run. |
| `LLM_MODE` | `simulated` | `simulated` or `native`. |
| `MODEL_PATH` | unset | GGUF file required in native mode. |
| `LLAMA_CLI` | `llama-cli` | Native runner executable. |
| `LLM_SYSTEM_PROMPT_FILE` | unset | UTF-8 file containing the native mode system prompt. |
| `LLM_SYSTEM_PROMPT` | unset | Direct native mode system prompt; takes precedence over the prompt file. |
| `MAX_CONTEXT` | `4096` | Context size passed to llama-cli and reported in telemetry. |
| `TOKENS_PER_TURN` | `128` | Maximum generated tokens per native turn. |

## Safety

The Python simulator catches its configured allocation limit. Native libraries may report allocation failure in different ways; if a process is killed, the supervisor can show diagnostics only if it survives. `SIGKILL` itself cannot be caught. The systemd cgroup limit is the final guard against the artwork consuming memory needed by the rest of the Pi.