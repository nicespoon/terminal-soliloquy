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

from context_telemetry import estimate_token_bytes

MIN_TYPE_DELAY = 0.002
MAX_TYPE_DELAY = 0.35
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


def _render_header(history_bytes: int = 0, max_context: int = 4096) -> None:
    history_tokens = estimate_token_bytes(history_bytes)
    context_fraction = history_tokens / max_context if max_context else 0.0
    pressure = context_fraction * 100
    red, green, blue = _rgb_gradient(pressure)
    color = f"\033[38;2;{red};{green};{blue}m"
    critical = "\033[5m" if pressure >= 90.0 else ""
    width = _terminal_columns()
    inner_width = width - 2
    border = "+" + "-" * inner_width + "+"
    title = "|" + " TERMINAL SOLILOQUY ".center(inner_width)[:inner_width] + "|"
    history_label = f"HISTORY ~{history_tokens}/{max_context} TOKENS"
    lines = (
        border,
        title,
        _meter_line(history_label, context_fraction, width),
        border,
    )
    _write("\033[s" + "".join(
        f"\033[{row};1H\033[2K{critical}{color}{line}\033[0m"
        for row, line in enumerate(lines, 1)
    ) + "\033[u")


def _type_delay(history_bytes: int, max_context: int) -> float:
    pressure = min(1.0, estimate_token_bytes(history_bytes) / max_context)
    return MIN_TYPE_DELAY + (MAX_TYPE_DELAY - MIN_TYPE_DELAY) * pressure


def _initialize_display() -> None:
    rows = _terminal_rows()
    _write("\033[?25l\033[2J\033[6;1H")
    _write(f"\033[6;{rows}r")
    _render_header()


def _draw_character(
    character: str,
    history_bytes: int = 0,
    max_context: int = 4096,
) -> None:
    _render_header(history_bytes, max_context)
    if character == "\n":
        _write("\r\n")
    else:
        _write(character)
    time.sleep(_type_delay(history_bytes, max_context))


def _start_runner() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "llm_runner.py")],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def _run_child(
    keyboard_ready: bool,
    max_context: int,
) -> tuple[int, int, str | None, int, str]:
    child = _start_runner()
    assert child.stdout is not None
    assert child.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    selector.register(child.stderr, selectors.EVENT_READ)
    if keyboard_ready:
        selector.register(sys.stdin, selectors.EVENT_READ)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    history_bytes = 0
    keyboard_action = None
    newline_run = 0
    output_count = 0
    runner_stderr = bytearray()
    try:
        while not STOP_REQUESTED:
            events = selector.select(timeout=0.1)
            if not events:
                if child.poll() is not None:
                    _terminate_process_group(child.pid)
                    break
                _render_header(history_bytes, max_context)
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
                elif key.fileobj is child.stderr:
                    error_data = os.read(child.stderr.fileno(), 4096)
                    if error_data:
                        runner_stderr.extend(error_data)
                        if len(runner_stderr) > 4096:
                            del runner_stderr[:-4096]
                    else:
                        selector.unregister(child.stderr)
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
                history_bytes += len(character.encode("utf-8"))
                if character == "\n":
                    _draw_character(character, history_bytes, max_context)
                    newline_run += 1
                    continue
                if newline_run == 1:
                    _write("\r\n")
                newline_run = 0
                _draw_character(character, history_bytes, max_context)
            if keyboard_action is not None:
                break
            if child.poll() is not None:
                _terminate_process_group(child.pid)
                break

        if STOP_REQUESTED and child.poll() is None:
            _terminate_process_group(child.pid)
        return_code = child.wait()
        runner_stderr.extend(child.stderr.read() or b"")
        for character in decoder.decode(b"", final=True):
            output_count += 1
            history_bytes += len(character.encode("utf-8"))
            if character == "\n":
                _draw_character(character, history_bytes, max_context)
                newline_run += 1
                continue
            if newline_run == 1:
                _write("\r\n")
            newline_run = 0
            _draw_character(character, history_bytes, max_context)
        return (
            return_code,
            child.pid,
            keyboard_action,
            output_count,
            runner_stderr[-4096:].decode("utf-8", errors="replace").strip(),
        )
    finally:
        selector.close()
        child.stdout.close()
        child.stderr.close()


def _render_diagnostics(
    return_code: int,
    child_pid: int,
    output_count: int,
    runner_error: str,
) -> None:
    if return_code == 76:
        title = "CONTEXT WINDOW FILLED"
        result = "The runner stopped before the next turn exceeded its context budget."
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
    details = [
        f"Runner PID {child_pid}  |  {timestamp}",
        f"Output {output_count} characters",
        f"Status: {result}",
    ]
    if runner_error:
        details.append(f"Runner stderr: {' '.join(runner_error.split())}")
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
        max_context = max(1, int(os.getenv("MAX_CONTEXT", "4096")))
        show_diagnostics = os.getenv("SHOW_DIAGNOSTICS", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }
        while not STOP_REQUESTED:
            (
                return_code,
                child_pid,
                keyboard_action,
                output_count,
                runner_error,
            ) = _run_child(keyboard_settings is not None, max_context)
            if STOP_REQUESTED:
                break
            if keyboard_action == "quit":
                break
            if keyboard_action == "restart":
                continue
            if show_diagnostics:
                _render_diagnostics(
                    return_code,
                    child_pid,
                    output_count,
                    runner_error,
                )
            if not _wait_before_restart(
                delay_seconds,
                keyboard_settings is not None,
            ):
                break
        return 0
    except (OSError, ValueError) as exc:
        _write(f"\r\n[ SUPERVISOR ERROR: {exc} ]\r\n")
        return 1
    finally:
        _restore_keyboard(keyboard_settings)
        _write("\033[0m\033[r\033[?25h")


if __name__ == "__main__":
    raise SystemExit(main())