#!/usr/bin/env python3
"""Fullscreen display supervisor for the Terminal Soliloquy child process."""

from __future__ import annotations

import codecs
import os
import select
import selectors
import shutil
import signal
import subprocess
import sys
import termios
import time
from datetime import datetime
import tty

import psutil

MIN_TYPE_DELAY = 0.002
MAX_TYPE_DELAY = 0.35
DELAY_SCALE = 1.536
STOP_REQUESTED = False


def _handle_stop_signal(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def _setup_keyboard() -> list | None:
    if not sys.stdin.isatty():
        return None
    try:
        previous_settings = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
        return previous_settings
    except (OSError, termios.error):
        return None


def _restore_keyboard(previous_settings: list | None) -> None:
    if previous_settings is not None:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, previous_settings)


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _rgb_gradient(pressure: float) -> tuple[int, int, int]:
    pressure = min(100.0, max(0.0, pressure))
    if pressure <= 70.0:
        amount = pressure / 70.0
        return (round(255 * amount), 255, round(64 * (1.0 - amount)))
    amount = (pressure - 70.0) / 30.0
    return (255, round(191 * (1.0 - amount)), 0)


def _terminal_rows() -> int:
    return max(8, shutil.get_terminal_size(fallback=(80, 24)).lines)


def _render_header():
    memory = psutil.virtual_memory()
    red, green, blue = _rgb_gradient(memory.percent)
    color = f"\033[38;2;{red};{green};{blue}m"
    critical = "\033[5m" if memory.percent >= 90.0 else ""
    reset_style = "\033[0m"
    available_mb = memory.available / (1024 * 1024)
    _write(
        f"\033[s\033[1;1H\033[2K{critical}{color}"
        f"TERMINAL SOLILOQUY  |  AVAILABLE RAM: {available_mb:7.1f} MB"
        f"{reset_style}\033[u"
    )
    _write(
        f"\033[s\033[2;1H\033[2K{critical}{color}"
        f"MEMORY PRESSURE: {memory.percent:5.1f}%"
        f"{reset_style}\033[u"
    )
    return memory


def _type_delay(available_bytes: int) -> float:
    available_mb = max(1.0, available_bytes / (1024 * 1024))
    return min(MAX_TYPE_DELAY, max(MIN_TYPE_DELAY, DELAY_SCALE / available_mb))


def _initialize_display() -> None:
    rows = _terminal_rows()
    _write("\033[?25l\033[2J\033[3;1H")
    _write(f"\033[3;{rows - 1}r")
    _render_header()


def _draw_character(character: str) -> None:
    memory = _render_header()
    _write("\r\n" if character == "\n" else character)
    time.sleep(_type_delay(memory.available))


def _start_runner() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "llm_runner.py")],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
        close_fds=True,
        start_new_session=True,
    )


def _terminate_process_group(process_group: int) -> None:
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(0.1)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run_child() -> tuple[int, int, int, int]:
    child = _start_runner()
    assert child.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    last_virtual_mb = 0
    last_rss_mb = 0
    try:
        while not STOP_REQUESTED:
            try:
                child_memory = psutil.Process(child.pid).memory_info()
                last_virtual_mb = round(child_memory.vms / (1024 * 1024))
                last_rss_mb = round(child_memory.rss / (1024 * 1024))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            events = selector.select(timeout=0.1)
            if not events:
                if child.poll() is not None:
                    _terminate_process_group(child.pid)
                    break
                _render_header()
                continue

            data = os.read(child.stdout.fileno(), 4096)
            if not data:
                break
            for character in decoder.decode(data):
                if STOP_REQUESTED:
                    break
                _draw_character(character)
            if child.poll() is not None:
                _terminate_process_group(child.pid)
                break

        if STOP_REQUESTED and child.poll() is None:
            _terminate_process_group(child.pid)
        return_code = child.wait()
        for character in decoder.decode(b"", final=True):
            _draw_character(character)
        return return_code, child.pid, last_virtual_mb, last_rss_mb
    finally:
        selector.close()
        child.stdout.close()


def _render_diagnostics(return_code: int, child_pid: int, virtual_mb: int, rss_mb: int) -> None:
    try:
        available_mb = psutil.virtual_memory().available / (1024 * 1024)
    except psutil.Error:
        available_mb = 0.0
    if return_code == 75:
        title = "MEMORY LIMIT REACHED"
        result = "The configured session limit was reached; the runner stopped cleanly."
    elif return_code < 0:
        signal_number = -return_code
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = f"SIGNAL_{signal_number}"
        title = "SESSION PAUSED"
        result = f"Runner ended with {signal_name}; supervisor remains available."
    elif return_code == 137:
        title = "SESSION PAUSED"
        result = "Runner ended with status 137; resource limit or external stop is possible."
    else:
        title = "SESSION PAUSED"
        result = f"Runner finished with status {return_code}."
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    _write("\r\n\033[1;33m")
    _write(f"[ {title} ] Runner (PID {child_pid}) at {timestamp}.\r\n")
    _write(
        f"[ MEMORY ] Total Virtual: {virtual_mb} MB | Resident: {rss_mb} MB | "
        f"Available physical: {available_mb:.1f} MB.\r\n"
    )
    _write(f"[ STATUS ] {result}\r\n")
    _write("[ INPUT ] Press R to restart or Q to quit.\033[0m\r\n")


def _render_status(text: str) -> None:
    _write(f"\033[s\033[{_terminal_rows()};1H\033[2K{text[:120]}\033[u")


def _read_key(keyboard_ready: bool) -> str | None:
    if not keyboard_ready:
        return None
    readable, _, _ = select.select([sys.stdin], [], [], 0)
    if readable:
        return os.read(sys.stdin.fileno(), 1).decode("ascii", errors="ignore").lower()
    return None


def _wait_before_restart(delay_seconds: int, keyboard_ready: bool) -> bool:
    deadline = time.monotonic() + delay_seconds if delay_seconds else None
    shown_seconds = -1
    while not STOP_REQUESTED:
        key = _read_key(keyboard_ready)
        if key == "r":
            return True
        if key == "q":
            return False
        if deadline is None:
            _render_status("[ R: restart now | Q: quit | automatic restart disabled ]")
            time.sleep(0.1)
            continue
        remaining = max(0, int(deadline - time.monotonic() + 0.999))
        if remaining != shown_seconds:
            _render_status(f"[ R: restart now | Q: quit | automatic restart in {remaining:02d}s ]")
            shown_seconds = remaining
        if remaining == 0:
            return True
        time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))
    return False


def main() -> int:
    global STOP_REQUESTED
    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)
    keyboard_settings = _setup_keyboard()
    try:
        _initialize_display()
        delay_seconds = max(0, int(os.getenv("AUTO_RESTART_DELAY", "30")))
        show_diagnostics = os.getenv("SHOW_DIAGNOSTICS", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }
        while not STOP_REQUESTED:
            return_code, child_pid, virtual_mb, rss_mb = _run_child()
            if STOP_REQUESTED:
                break
            if show_diagnostics:
                _render_diagnostics(return_code, child_pid, virtual_mb, rss_mb)
            if not _wait_before_restart(delay_seconds, keyboard_settings is not None):
                break
        return 0
    except (OSError, psutil.Error, ValueError) as exc:
        _write(f"\r\n[ SUPERVISOR ERROR: {exc} ]\r\n")
        return 1
    finally:
        _restore_keyboard(keyboard_settings)
        _write("\033[0m\033[r\033[?25h")


if __name__ == "__main__":
    raise SystemExit(main())