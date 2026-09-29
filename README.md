# Terminal Soliloquy

Terminal Soliloquy is a fullscreen, phosphor-green terminal artwork for Linux systems using systemd and Wayland. It displays the model's growing conversation history while the model writes into a finite context window.

The runner passes the full generated history and Ollama-reported context usage to the model on every turn. The persistent header meter and teletype pacing use Ollama's exact token counts; when the configured context is full, the display shows an exhaustion block. The supervisor keeps the last frame visible and offers keyboard restart, quit, or a configurable automatic restart.

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

The unit expects the checkout at `~/terminal-soliloquy`. Configure Ollama as described below, then start the service with `systemctl --user enable --now terminal-soliloquy.service`. Check it with `systemctl --user status terminal-soliloquy.service` and `journalctl --user -u terminal-soliloquy.service`.

## Controls

Press `R` to restart the runner immediately or `Q` to quit the display service, either while text is streaming or after a session ends. By default, a finished runner restarts automatically after 30 seconds; set `AUTO_RESTART_DELAY=0` for a manual-only pause. A keyboard must be connected for the manual controls.

## Language Model

The runner uses Ollama. At the start of every turn it passes the model the growing generated history and latest context usage reported by Ollama. The supervisor meter and typing pace use the same exact token count, and generation ends when the measured context reaches `MAX_CONTEXT`. By default the runner uses the built-in system prompt; set `LLM_SYSTEM_PROMPT_FILE` to a UTF-8 text file to replace it, or set `LLM_SYSTEM_PROMPT` directly in the service drop-in. The direct value takes precedence if both are set.

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
Environment=OLLAMA_MODEL=llama3.2:1b
Environment=OLLAMA_HOST=http://127.0.0.1:11434
```

`OLLAMA_HOST` can point to a reachable Ollama server on another machine, for example `http://192.168.1.20:11434`. The remote server must allow connections from this machine; protect its API at the network boundary.

For a first launch, start the configured service:

```sh
systemctl --user daemon-reload
systemctl --user enable --now terminal-soliloquy.service
```

After changing an existing service configuration, apply it with `systemctl --user daemon-reload` and `systemctl --user restart terminal-soliloquy.service`.

Ollama must be running before the display service starts. The runner uses Ollama's streaming `/api/generate` endpoint and sends `MAX_CONTEXT` and a shrinking `TOKENS_PER_TURN` budget as generation options. Choose a model and context size supported by the Ollama server.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `AUTO_RESTART_DELAY` | `30` | Restart seconds; `0` waits for `R` or `Q`. |
| `SHOW_DIAGNOSTICS` | `true` | Show a short session status after a run. |
| `OLLAMA_MODEL` | unset | Model name pulled into Ollama; required. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama HTTP API address. |
| `LLM_SYSTEM_PROMPT_FILE` | unset | UTF-8 file containing the system prompt. |
| `LLM_SYSTEM_PROMPT` | unset | Direct system prompt; takes precedence over the prompt file. |
| `MAX_CONTEXT` | `4096` | Context size passed to Ollama and used as the token horizon. |
| `TOKENS_PER_TURN` | `128` | Upper bound on generated tokens per turn; reduced as context fills. |