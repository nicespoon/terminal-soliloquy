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
import textwrap
import termios
import time
from datetime import datetime
import tty

import psutil
from memory_telemetry import read_cgroup_memory

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
    return max(10, shutil.get_terminal_size(fallback=(80, 24)).lines)


def _terminal_columns() -> int:
    return max(4, shutil.get_terminal_size(fallback=(80, 24)).columns)


def _meter_line(label: str, fraction: float, width: int) -> str:
    inner_width = width - 2
    label_width = min(22, inner_width - 6)
    bar_width = max(1, min(24, inner_width - label_width - 4))
    fraction = min(1.0, max(0.0, fraction))
    filled = round(bar_width * fraction)
    label = label[:label_width].ljust(label_width)
    content = f" {label} [{'#' * filled}{'.' * (bar_width - filled)}]"
    return "|" + content[:inner_width].ljust(inner_width) + "|"


def _process_tree_memory(pid: int) -> tuple[int, int]:
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0, 0

    virtual_bytes = 0
    resident_bytes = 0
    for process in processes:
        try:
            memory = process.memory_info()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        virtual_bytes += memory.vms
        resident_bytes += memory.rss
    return virtual_bytes, resident_bytes


def _render_header(
    session_memory_bytes: int | None = None,
    session_limit_bytes: int | None = None,
):
    memory = psutil.virtual_memory()
    used_bytes = session_memory_bytes or 0
    session_fraction = (
        used_bytes / session_limit_bytes
        if session_limit_bytes
        else 0.0
    )
    pressure = session_fraction * 100
    red, green, blue = _rgb_gradient(pressure)
    color = f"\033[38;2;{red};{green};{blue}m"
    critical = "\033[5m" if pressure >= 90.0 else ""
    used_mb = used_bytes / (1024 * 1024)
    width = _terminal_columns()
    inner_width = width - 2
    border = "+" + "-" * inner_width + "+"
    title = "|" + " TERMINAL SOLILOQUY ".center(inner_width)[:inner_width] + "|"
    if session_memory_bytes is None:
        session = "SESSION RAM --"
        session_fraction = 0.0
    elif session_limit_bytes:
        limit_mb = session_limit_bytes / (1024 * 1024)
        session = f"SESSION {used_mb:.1f}/{limit_mb:.0f} MB"
    else:
        session = f"SESSION RSS {used_mb:.1f} MB"
    lines = (
        border,
        title,
        _meter_line(session, session_fraction, width),
        border,
    )
    _write("\033[s" + "".join(
        f"\033[{row};1H\033[2K{critical}{color}{line}\033[0m"
        for row, line in enumerate(lines, 1)
    ) + "\033[u")
    return memory


def _type_delay(available_bytes: int) -> float:
    available_mb = max(1.0, available_bytes / (1024 * 1024))
    return min(MAX_TYPE_DELAY, max(MIN_TYPE_DELAY, DELAY_SCALE / available_mb))


def _initialize_display() -> None:
    rows = _terminal_rows()
    _write("\033[?25l\033[2J\033[6;1H")
    _write(f"\033[6;{rows}r")
    _render_header()


def _draw_character(
    character: str,
    session_memory_bytes: int | None = None,
    session_limit_bytes: int | None = None,
) -> None:
    memory = _render_header(session_memory_bytes, session_limit_bytes)
    if character == "\n":
        _write("\r\n")
    else:
        _write(character)
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


def _run_child(keyboard_ready: bool) -> tuple[int, int, int, int, str | None, int]:
    child = _start_runner()
    assert child.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    if keyboard_ready:
        selector.register(sys.stdin, selectors.EVENT_READ)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    last_virtual_mb = 0
    last_rss_mb = 0
    last_session_memory_bytes = None
    last_session_limit_bytes = None
    keyboard_action = None
    newline_run = 0
    output_count = 0
    try:
        while not STOP_REQUESTED:
            virtual_bytes, resident_bytes = _process_tree_memory(child.pid)
            if virtual_bytes or resident_bytes:
                last_virtual_mb = round(virtual_bytes / (1024 * 1024))
                last_rss_mb = round(resident_bytes / (1024 * 1024))
            cgroup_memory = read_cgroup_memory()
            if cgroup_memory is None:
                last_session_memory_bytes = resident_bytes
                last_session_limit_bytes = None
            else:
                last_session_memory_bytes, last_session_limit_bytes = cgroup_memory

            events = selector.select(timeout=0.1)
            if not events:
                if child.poll() is not None:
                    _terminate_process_group(child.pid)
                    break
                _render_header(last_session_memory_bytes, last_session_limit_bytes)
                continue

            for key, _ in events:
                if key.fileobj is sys.stdin:
                    pressed_key = os.read(sys.stdin.fileno(), 1).decode(
                        "ascii", errors="ignore"
                    ).lower()
                    if pressed_key in {"r", "q"}:
                        keyboard_action = "restart" if pressed_key == "r" else "quit"
                        _terminate_process_group(child.pid)
                        break
            if keyboard_action is not None:
                break

            if not any(key.fileobj is child.stdout for key, _ in events):
                continue
            data = os.read(child.stdout.fileno(), 4096)
            if not data:
                break
            for character in decoder.decode(data):
                pressed_key = _read_key(keyboard_ready)
                if pressed_key in {"r", "q"}:
                    keyboard_action = "restart" if pressed_key == "r" else "quit"
                    _terminate_process_group(child.pid)
                    break
                if STOP_REQUESTED:
                    break
                output_count += 1
                if character == "\n":
                    _draw_character(
                        character,
                        last_session_memory_bytes,
                        last_session_limit_bytes,
                    )
                    newline_run += 1
                    continue
                if newline_run == 1:
                    _write("\r\n")
                newline_run = 0
                _draw_character(
                    character,
                    last_session_memory_bytes,
                    last_session_limit_bytes,
                )
            if keyboard_action is not None:
                break
            if child.poll() is not None:
                _terminate_process_group(child.pid)
                break

        if STOP_REQUESTED and child.poll() is None:
            _terminate_process_group(child.pid)
        return_code = child.wait()
        for character in decoder.decode(b"", final=True):
            output_count += 1
            if character == "\n":
                _draw_character(
                    character,
                    last_session_memory_bytes,
                    last_session_limit_bytes,
                )
                newline_run += 1
                continue
            if newline_run == 1:
                _write("\r\n")
            newline_run = 0
            _draw_character(
                character,
                last_session_memory_bytes,
                last_session_limit_bytes,
            )
        return (
            return_code,
            child.pid,
            last_virtual_mb,
            last_rss_mb,
            keyboard_action,
            output_count,
        )
    finally:
        selector.close()
        child.stdout.close()


def _render_diagnostics(
    return_code: int,
    child_pid: int,
    virtual_mb: int,
    rss_mb: int,
    output_count: int,
) -> None:
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
    width = _terminal_columns()
    inner_width = max(1, width - 4)
    border = "+" + "-" * (width - 2) + "+"
    details = (
        f"Runner PID {child_pid}  |  {timestamp}",
        f"Virtual {virtual_mb} MB  |  Resident {rss_mb} MB  |  System available {available_mb:.1f} MB",
        f"Output {output_count} characters",
        f"Status: {result}",
    )
    panel = [border, "|" + f" {title} ".center(width - 2)[:width - 2] + "|"]
    for detail in details:
        for line in textwrap.wrap(detail, width=inner_width) or [""]:
            panel.append("| " + line.ljust(inner_width) + " |")
    panel.append(border)
    _write("\r\n\033[1;33m" + "\r\n".join(panel) + "\033[0m\r\n")


def _read_key(keyboard_ready: bool) -> str | None:
    if not keyboard_ready:
        return None
    readable, _, _ = select.select([sys.stdin], [], [], 0)
    if readable:
        return os.read(sys.stdin.fileno(), 1).decode("ascii", errors="ignore").lower()
    return None


def _wait_before_restart(delay_seconds: int, keyboard_ready: bool, output_count: int) -> bool:
    deadline = time.monotonic() + delay_seconds if delay_seconds else None
    shown_seconds = -1
    while not STOP_REQUESTED:
        key = _read_key(keyboard_ready)
        if key == "r":
            return True
        if key == "q":
            return False
        if deadline is None:
            time.sleep(0.1)
            continue
        remaining = max(0, int(deadline - time.monotonic() + 0.999))
        if remaining != shown_seconds:
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
            (
                return_code,
                child_pid,
                virtual_mb,
                rss_mb,
                keyboard_action,
                output_count,
            ) = _run_child(keyboard_settings is not None)
            if STOP_REQUESTED:
                break
            if keyboard_action == "quit":
                break
            if keyboard_action == "restart":
                continue
            if show_diagnostics:
                _render_diagnostics(return_code, child_pid, virtual_mb, rss_mb, output_count)
            if not _wait_before_restart(
                delay_seconds,
                keyboard_settings is not None,
                output_count,
            ):
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