# Terminal Soliloquy

Terminal Soliloquy is a fullscreen, phosphor-green terminal artwork for Linux systems using systemd and Wayland. It displays the model's growing conversation history while the model writes into a finite context window.

The runner passes estimated context state and the full generated history to the model on every turn. As the context fills, the model receives less room to answer, the display slows its typing, and the runner stops cleanly before the next turn is estimated to exceed the configured context. systemd limits remain the system-level guard for memory use. The supervisor keeps the last frame visible and offers keyboard restart, quit, or a configurable automatic restart.

## Install
Run these commands from a terminal in the graphical Wayland session so the user service manager receives the display environment.


```sh
cd ~
git clone https://github.com/nicespoon/terminal-soliloquy
cd terminal-soliloquy
sudo apt update
sudo apt install -y foot python3
mkdir -p ~/.config/systemd/user
ln -s ~/terminal-soliloquy/systemd/terminal-soliloquy.service \
	~/.config/systemd/user/terminal-soliloquy.service
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR
systemctl --user daemon-reload
```

The unit expects the checkout at `~/terminal-soliloquy`. Configure an LLM backend as described below, then start the service with `systemctl --user enable --now terminal-soliloquy.service`. Check it with `systemctl --user status terminal-soliloquy.service` and `journalctl --user -u terminal-soliloquy.service`.

## Controls

The footer shows the controls and current session state. Press `R` to restart the runner immediately or `Q` to quit the display service, either while text is streaming or after a session ends. By default, a finished runner restarts automatically after 30 seconds; set `AUTO_RESTART_DELAY=0` for a manual-only pause. A keyboard must be connected for the manual controls.

## System Resource Guard

The service unit applies systemd memory limits to the complete display service. These limits protect the rest of the system; the application does not collect or display RAM telemetry.

- `MemoryHigh=448M` and `MemoryMax=512M` bound aggregate memory for the service, including `foot` and the supervisor. This cgroup ceiling protects the rest of the system if a model backend does not handle an allocation failure cleanly.
- `OOMPolicy=continue` keeps the supervisor alive when a process in the service cgroup is terminated.

To adjust the budget, create a service drop-in:

```sh
systemctl --user edit terminal-soliloquy.service
```

For example, to allow a larger model while retaining a system memory guard:

```ini
[Service]
MemoryHigh=576M
MemoryMax=640M
```

The systemd limits apply to the whole service, so leave room for `foot` and the supervisor and choose a ceiling that leaves enough RAM for the operating system. Apply changes with `systemctl --user daemon-reload` and restart the service. Ollama runs outside this service's cgroup, so its model memory is not covered by this guard.

To optionally cap CPU usage, add a quota to the same service drop-in:

```ini
[Service]
CPUQuota=200%
```

`CPUQuota=200%` allows the service to use up to the equivalent of two CPU cores. The quota applies to the service cgroup, including `foot`, the supervisor, and `llama-server`; it does not limit Ollama when Ollama runs as a separate system service. A lower quota can reduce the impact on other applications, but may also slow model generation.

## Language Model

The runner requires a configured LLM backend. At the start of every turn it passes the model the growing generated history and the context used/remaining. When the backend reports exact token counts (both `llama-server` and Ollama do), that exact figure is used instead of the runner's own UTF-8-based estimate; a small margin is reserved before stopping cleanly either way. By default the runner uses the built-in system prompt; set `LLM_SYSTEM_PROMPT_FILE` to a UTF-8 text file to replace it, or set `LLM_SYSTEM_PROMPT` directly in the service drop-in. The direct value takes precedence if both are set.

### llama-server (recommended)

`llama-server` is recommended because the runner spawns and owns it directly: it starts once per runner session, stays resident inside the service cgroup (covered by `MemoryMax`) for the whole session, and is torn down when the runner restarts. Keeping the model resident and reusing its prompt cache across turns avoids the reload cost of relaunching a model process every turn. Install a compatible `llama-server` build and obtain a local GGUF model, then configure the user-service drop-in:

```ini
[Service]
Environment=LLM_BACKEND=llama-server
Environment=MODEL_PATH=/path/to/model.gguf
Environment=LLAMA_SERVER=/usr/local/bin/llama-server
Environment=LLM_SYSTEM_PROMPT_FILE=%h/terminal-soliloquy/prompt.txt
```

For a first launch, start the configured service:

```sh
systemctl --user daemon-reload
systemctl --user enable --now terminal-soliloquy.service
```

After changing an existing service configuration, apply it with `systemctl --user daemon-reload` and `systemctl --user restart terminal-soliloquy.service`.

If `LLAMA_SERVER_PORT` (default `8080`) is already in use on the machine, set it to a free port in the same drop-in.

### Ollama

Ollama is an alternative if you already use its model service. Its model memory is outside this user service's `MemoryMax`, so account for it separately when choosing a model, especially on memory-constrained systems.

Install Ollama using the instructions for your distribution at [ollama.com](https://ollama.com/download/linux), then start its service and download a model. For example:

```sh
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama pull llama3.2:1b
ollama run llama3.2:1b "Reply with one short sentence."
```

Configure Terminal Soliloquy to use Ollama through the user-service drop-in:

```ini
[Service]
Environment=LLM_BACKEND=ollama
Environment=OLLAMA_MODEL=llama3.2:1b
Environment=OLLAMA_HOST=http://127.0.0.1:11434
```

For a first launch, start the configured service:

```sh
systemctl --user daemon-reload
systemctl --user enable --now terminal-soliloquy.service
```

After changing an existing service configuration, apply it with `systemctl --user daemon-reload` and `systemctl --user restart terminal-soliloquy.service`.

Ollama must be running before the display service starts. The runner uses Ollama's streaming `/api/generate` endpoint and sends `MAX_CONTEXT` and a shrinking `TOKENS_PER_TURN` budget as generation options. Choose a model that fits the machine's available RAM; systemd's service limits do not cover an Ollama server running separately.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `MemoryHigh` | `448M` | systemd service memory pressure threshold. |
| `MemoryMax` | `512M` | Hard aggregate memory ceiling for the service cgroup. |
| `AUTO_RESTART_DELAY` | `30` | Restart seconds; `0` waits for `R` or `Q`. |
| `SHOW_DIAGNOSTICS` | `true` | Show a short session status after a run. |
| `LLM_BACKEND` | `llama-server` | LLM backend: `llama-server` or `ollama`. |
| `MODEL_PATH` | unset | GGUF file required by the `llama-server` backend. |
| `LLAMA_SERVER` | `llama-server` | llama-server executable. |
| `LLAMA_SERVER_PORT` | `8080` | Port the runner starts llama-server on (127.0.0.1 only). |
| `LLAMA_SERVER_STARTUP_TIMEOUT` | `120` | Seconds to wait for llama-server's `/health` to report ready. |
| `OLLAMA_MODEL` | unset | Model name pulled into Ollama, required by the `ollama` backend. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama HTTP API address. |
| `LLM_SYSTEM_PROMPT_FILE` | unset | UTF-8 file containing the system prompt. |
| `LLM_SYSTEM_PROMPT` | unset | Direct system prompt; takes precedence over the prompt file. |
| `MAX_CONTEXT` | `4096` | Context size passed to the backend and used for the estimated history budget. |
| `TOKENS_PER_TURN` | `128` | Upper bound on generated tokens per turn; reduced as context fills. |