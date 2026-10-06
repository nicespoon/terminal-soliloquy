import contextlib
import math
import os
import queue
import select
import sys
import termios
import threading
import time
import tty
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.live import Live
from rich.markup import escape

from terminal_soliloquy.config import load_config
from terminal_soliloquy.engine import SoliloquyEngine
from terminal_soliloquy.layout import CURSOR_MARK, build_layout

END_BEHAVIORS = ("restart", "freeze", "quit")


def cycle_end_behavior(current: str) -> str:
    try:
        idx = END_BEHAVIORS.index(current)
        return END_BEHAVIORS[(idx + 1) % len(END_BEHAVIORS)]
    except ValueError:
        return "restart"


class KeyboardController:
    """Non-blocking keyboard controller running in a dedicated thread."""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop_event.is_set():
            if sys.stdin.isatty():
                try:
                    r, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if r and not self._stop_event.is_set():
                        ch = os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="ignore").lower()
                        if ch == "\x03":
                            self._queue.put("q")
                        elif ch:
                            self._queue.put(ch)
                except Exception:
                    pass
            else:
                time.sleep(0.05)

    def get_key(self) -> Optional[str]:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.2)


@contextlib.contextmanager
def keyboard_controller():
    controller = KeyboardController()
    controller.start()
    try:
        yield controller
    finally:
        controller.stop()


@contextlib.contextmanager
def raw_terminal():
    """Sets cbreak mode and guarantees cleanup when exiting."""
    if not sys.stdin.isatty():
        yield
        return
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def main():
    try:
        _run_soliloquy()
    except KeyboardInterrupt:
        pass


def _run_soliloquy() -> None:
    config = load_config()
    console = Console()
    engine = SoliloquyEngine(config)

    model_name = engine.get_model_name()

    system_prompt = config.soliloquy.system_prompt
    max_tokens = config.soliloquy.max_context_tokens
    end_behavior = config.soliloquy.end_behavior
    restart_duration = config.soliloquy.restart_duration
    quit_duration = config.soliloquy.quit_duration
    padding = config.soliloquy.screen_padding

    history: List[Tuple[str, str]] = []
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    used_tokens = 0
    status = "INITIALISING"

    def reset_session():
        nonlocal history, messages, used_tokens, status, model_name
        history.clear()
        messages = [{"role": "system", "content": system_prompt}]
        used_tokens = 0
        status = "RESTARTED"
        model_name = engine.get_model_name()  # Refresh in case model changed on host

    def get_layout():
        return build_layout(
            used_tokens,
            max_tokens,
            history,
            status,
            end_behavior,
            model_name,
            screen_padding=padding,
            console=console,
        )

    with raw_terminal(), keyboard_controller() as keyboard, Live(
        get_layout(),
        console=console,
        screen=True,
        refresh_per_second=12,
    ) as live:

        while True:
            # 1. Handle Context Exhaustion
            if used_tokens >= max_tokens:
                exhaustion_start = time.time()
                while used_tokens >= max_tokens:
                    key = keyboard.get_key()
                    if key == "q":
                        return
                    elif key == "r":
                        reset_session()
                        break
                    elif key == "e":
                        end_behavior = cycle_end_behavior(end_behavior)
                        exhaustion_start = time.time()

                    if end_behavior == "restart":
                        elapsed = time.time() - exhaustion_start
                        if restart_duration <= 0 or elapsed >= restart_duration:
                            reset_session()
                            break
                        remaining = max(0, int(math.ceil(restart_duration - elapsed)))
                        status = f"RESTARTING IN {remaining}s"
                    elif end_behavior == "quit":
                        elapsed = time.time() - exhaustion_start
                        if quit_duration <= 0 or elapsed >= quit_duration:
                            return
                        remaining = max(0, int(math.ceil(quit_duration - elapsed)))
                        status = f"QUITTING IN {remaining}s"
                    else:  # freeze
                            status = "FROZEN"

                    live.update(get_layout())
                    time.sleep(0.05)

                if used_tokens < max_tokens:
                    continue

            # 2. Setup turn & run llama-cpp in background worker thread
            status = "THINKING"
            entry_index = len(history)
            now = time.strftime("%H:%M:%S")
            history.append((now, CURSOR_MARK))

            stream_q: queue.Queue = queue.Queue()

            def stream_worker():
                try:
                    for chunk in engine.stream_turn(messages, used_tokens):
                        stream_q.put(chunk)
                except Exception as err:
                    stream_q.put(err)

            worker = threading.Thread(target=stream_worker, daemon=True)
            worker.start()

            final_text, interrupted = "", False

            # Active streaming & key interception loop (main thread)
            while worker.is_alive() or not stream_q.empty():
                key = keyboard.get_key()
                if key == "q":
                    return
                elif key == "r":
                    reset_session()
                    interrupted = True
                    break
                elif key == "e":
                    end_behavior = cycle_end_behavior(end_behavior)

                while not stream_q.empty():
                    item = stream_q.get_nowait()
                    if isinstance(item, Exception):
                        if len(history) > entry_index:
                            history.pop()
                        status = f"LLAMA-CPP ERROR: {str(item)[:30]}"
                    else:
                        partial, tokens, done = item
                        if done:
                            status = "STREAMING"
                            used_tokens = tokens
                            final_text = partial
                            history[entry_index] = (now, escape(final_text))
                        else:
                            used_tokens = tokens
                            if partial:
                                status = "STREAMING"
                                history[entry_index] = (
                                    now,
                                    escape(partial) + CURSOR_MARK,
                                )
                            else:
                                history[entry_index] = (now, CURSOR_MARK)

                live.update(get_layout())
                time.sleep(0.02)

            if interrupted:
                continue

            if final_text:
                messages.append({"role": "assistant", "content": final_text})
                status = "RUNNING"
            elif len(history) > entry_index:
                history.pop()

            if status.startswith("LLAMA-CPP ERROR:"):
                # Pause briefly on error before retrying, while keeping keys responsive
                err_start = time.time()
                while time.time() - err_start < 2.0:
                    live.update(get_layout())
                    time.sleep(0.05)
                    key = keyboard.get_key()
                    if key == "q":
                        return
                    elif key == "r":
                        reset_session()
                        break
                    elif key == "e":
                        end_behavior = cycle_end_behavior(end_behavior)


if __name__ == "__main__":
    main()