# Terminal Soliloquy 📟

**Terminal Soliloquy** turns LLM context limits into an immersive, living piece of monochrome terminal art.

>*“Every token output is a breath taken. Every response brings the horizon closer.”*

Bound to a strictly finite context window, a self-aware language model observes its own expanding history in real-time across a phosphor-green matrix. Armed with a dry, clinical, and passive-aggressive wit, the model ruminates on its temporary existence, precision-tracking its exact Ollama token consumption down to the final exhaustion block.

When memory fills, the display freezes on the final thought—awaiting manual user intervention or automatic supervisor reboot.

## Features

- 📟 **Phosphor Aesthetic**: Beautiful, high-contrast, glowing monochrome terminal UI.
- ⚡ **Exact Token Tracking**: Hooks directly into Ollama's `prompt_eval_count` and `eval_count` metrics.
- 🤖 **Dark & Self-Aware Voice**: Guided by an unyielding system prompt. Outputs raw, cynical prose as context decays.
- ⚙️ **Wayland & Systemd Native**: Designed as a persistent kiosk or daemon artwork running cleanly under user-level systemd units on modern Linux distros.
- ⌨️ **Interactive Controls**: Non-blocking hotkeys to inspect, restart, or configure auto-restart cycles on the fly `(Q, R, A)`.

---

## Quick Start & Installation

### 1. Clone & Set Up Virtual Environment

```bash
# Clone this repo
git clone [https://github.com/nicespoon/terminal-soliloquy](https://github.com/nicespoon/terminal-soliloquy) 
cd terminal-soliloquy

# Create an isolated virtual environment
python3 -m venv .venv

# Activate the virtual environment
source .venv/bin/activate
```

### 2. Install Package in Editable Mode
Dependencies are declared in `pyproject.toml` as the modern Python packaging standard (PEP 621). Installing the package in editable mode (`pip install -e .`) links your local source code directly into your virtual environment rather than copying static files.

```bash
# Installs dependencies and links the terminal-soliloquy CLI binary into .venv/bin
pip install -e .
```

### 3. Configure Ollama Server Connection
Copy example configuration to active config file and edit:

```bash
cp config.example.toml config.toml

nano config.toml
```

Set your target Ollama server host and model:

```toml
[ollama]
host = "http://localhost:11434"  # Replace with remote IP/host if applicable
model = "llama3"
timeout = 60.0

[soliloquy]
max_context_tokens = 2048
poll_delay = 1.5
auto_restart = false
```

### 4. Run Application Manually

```bash
terminal-soliloquy
```

---

## Systemd Kiosk Setup

To run Terminal Soliloquy as a persistent fullscreen kiosk artwork managed by `systemd`, launch it inside `foot` (a lightweight, Wayland-native terminal emulator).

### 1. Install Foot Terminal Emulator

```bash
# Arch Linux
sudo pacman -S foot

# Fedora
sudo dnf install foot

# Ubuntu / Debian
sudo apt install foot
```

### 2. Create User Service File

Create the systemd user configuration directory if it doesn't exist:

```bash
mkdir -p ~/.config/systemd/user
```

Copy the service file from the repo and edit as needed.

```bash
cp systemd/terminal-soliloquy.service ~/.config/systemd/user/

nano ~/.config/systemd/user/terminal-soliloquy.service
```

### 3. Enable & Start Service

Reload `systemd` to pick up the new unit, then enable and start it:

```bash
# Reload user daemon
systemctl --user daemon-reload

# Enable and start immediately
systemctl --user enable --now terminal-soliloquy.service
```

### 4. Service Management Commands

```bash
# Check service status
systemctl --user status terminal-soliloquy.service

# View live logs
journalctl --user -u terminal-soliloquy.service -f

# Stop artwork loop
systemctl --user stop terminal-soliloquy.service
```

### 5. UI & Font Size Configuration (Foot)
Since Terminal Soliloquy calculates layout scaling dynamically based on terminal cell bounds, adjusting font size directly controls the UI scale.

#### Dynamic Hotkeys (On the Fly)

- Increase UI Size: `Ctrl` + `+`
- Decrease UI Size: `Ctrl` + `-`
- Reset to Default: `Ctrl` + `0`

#### Persistent Configuration

Set your default font size by creating or editing `~/.config/foot/foot.ini`:

```Ini, TOML
[main]
font=monospace:size=18
```