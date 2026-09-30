import contextlib
import os
import queue
import select
import sys
import termios
import threading
import time
import tty
from typing import Dict, List, Tuple

from rich.console import Console
from rich.live import Live

from terminal_soliloquy.config import load_config
from terminal_soliloquy.engine import SoliloquyEngine
from terminal_soliloquy.layout import build_layout


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


def read_key() -> str:
    """Non-blocking key read directly from standard input."""
    if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0]:
        try:
            return os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="ignore").lower()
        except Exception:
            pass
    return ""


def main():
    config = load_config()
    console = Console()
    engine = SoliloquyEngine(config)

    system_prompt = config.soliloquy.system_prompt
    max_tokens = config.soliloquy.max_context_tokens
    auto_restart = config.soliloquy.auto_restart
    padding = config.soliloquy.screen_padding
    CURSOR = "\u200b[bold bright_green]█[/bold bright_green]"

    history: List[Tuple[str, str]] = []
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    used_tokens = 0
    status = "INITIALISING"

    def reset_session():
        nonlocal history, messages, used_tokens, status
        history.clear()
        messages = [{"role": "system", "content": system_prompt}]
        used_tokens = 0
        status = "RESTARTED"

    def get_layout():
        return build_layout(
            used_tokens,
            max_tokens,
            history,
            status,
            auto_restart,
            config.llamacpp.model,
            screen_padding=padding,
            console=console,
        )

    with raw_terminal(), Live(
        get_layout(),
        console=console,
        screen=True,
        refresh_per_second=12,
    ) as live:

        while True:
            # 1. Handle Context Exhaustion
            if used_tokens >= max_tokens:
                status = "EXHAUSTED"
                if auto_restart:
                    status = "AUTO-RESTARTING"
                    live.update(get_layout())
                    time.sleep(30)
                    reset_session()
                else:
                    live.update(get_layout())
                    time.sleep(0.1)
                    key = read_key()
                    if key == "q": break
                    elif key == "r": reset_session()
                    elif key == "a": auto_restart = not auto_restart
                    continue

            # 2. Setup turn & run llama-cpp in background worker thread
            status = "THINKING"
            entry_idx = len(history)
            now = time.strftime("%H:%M:%S")
            history.append((now, CURSOR))

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
                key = read_key()
                if key == "q":
                    return
                elif key == "r":
                    reset_session()
                    interrupted = True
                    break
                elif key == "a":
                    auto_restart = not auto_restart

                while not stream_q.empty():
                    item = stream_q.get_nowait()
                    if isinstance(item, Exception):
                        if len(history) > entry_idx:
                            history.pop()
                        status = f"LLAMA-CPP ERROR: {str(item)[:30]}"
                    else:
                        partial, tokens, done = item
                        status = "STREAMING"
                        if done:
                            used_tokens = tokens
                            final_text = partial
                            history[entry_idx] = (now, final_text)
                        else:
                            history[entry_idx] = (now, f"{partial}{CURSOR}")

                live.update(get_layout())
                time.sleep(0.02)

            if interrupted:
                continue

            if final_text:
                messages.append({"role": "assistant", "content": final_text})
                status = "RUNNING"

            # 3. Inter-turn delay loop
            start_pause = time.time()
            while time.time() - start_pause < config.soliloquy.poll_delay:
                key = read_key()
                if key == "q":
                    return
                elif key == "r":
                    reset_session()
                    break
                elif key == "a":
                    auto_restart = not auto_restart

                live.update(get_layout())
                time.sleep(0.02)


if __name__ == "__main__":
    main()