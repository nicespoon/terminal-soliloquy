#!/usr/bin/env python3
"""Glaydos-style Fullscreen Display Supervisor using Rich."""

from __future__ import annotations

import codecs
import os
import select
import selectors
import signal
import subprocess
import sys
import termios
import time
import tty
from datetime import datetime

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.style import Style
from rich.table import Table
from rich.text import Text

# Environment and limits
MAX_CONTEXT = int(os.getenv("MAX_CONTEXT", "4096"))
STOP_REQUESTED = False

console = Console()


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


def _read_key(keyboard_ready: bool) -> str | None:
    if not keyboard_ready:
        return None
    readable, _, _ = select.select([sys.stdin], [], [], 0)
    if readable:
        return os.read(sys.stdin.fileno(), 1).decode("ascii", errors="ignore").lower()
    return None


def get_pressure_style(pressure: float) -> Style:
    """Return memory pressure warning styles."""
    if pressure < 50.0:
        return Style(color="bright_cyan", bold=True)
    elif pressure < 80.0:
        return Style(color="bright_yellow", bold=True)
    else:
        return Style(color="bright_red", bold=True, blink=True)


class SoliloquyUI:
    def __init__(self, max_context: int = 4096) -> None:
        self.max_context = max_context
        self.current_tokens = 0
        self.runner_pid = "-"
        self.status_msg = "ACTIVE"
        self.output_text = Text()
        self.layout = Layout()
        self._setup_layout()

    def _setup_layout(self) -> None:
        self.layout.split(
            Layout(name="header", size=4),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3),
        )
        self.layout["body"].split_row(
            Layout(name="main", ratio=3),
            Layout(name="sidebar", ratio=1),
        )

    def start_new_output_turn(self) -> None:
        """Inject a timestamp delimiter before a new streaming turn begins."""
        now_str = datetime.now().strftime("%H:%M:%S")
        if len(self.output_text) > 0 and not self.output_text.plain.endswith("\n"):
            self.output_text.append("\n")
        self.output_text.append(
            f"─── [{now_str}] ────────────────────────────────────────\n",
            style="bold dim cyan",
        )

    def append_stream_text(self, text: str) -> None:
        """Append raw character output from the LLM stream."""
        self.output_text.append(text, style="bright_white")

    def render_header(self) -> Panel:
        pressure = min(100.0, (self.current_tokens / self.max_context) * 100)
        style = get_pressure_style(pressure)

        title = Text("TERMINAL SOLILOQUY ", style="bold bright_white")
        title.append(":: SYSTEM MONITOR", style="bold gold1")
        title.append(f"  [{self.status_msg}]", style=style)

        bar = Progress(
            TextColumn("[bold grey70]CONTEXT MEMORY:"),
            BarColumn(bar_width=None, complete_style=style, finished_style="bright_red"),
            TextColumn(f"[bold white]{self.current_tokens}/{self.max_context}"),
            TextColumn(f"({pressure:.1f}%)"),
            expand=True,
        )
        bar.add_task("context", total=self.max_context, completed=self.current_tokens)

        return Panel(
            Group(Align.center(title), bar),
            box=box.ROUNDED,
            border_style="gold1",
        )

    def render_main(self) -> Panel:
        # Render the last 25 lines of formatted text to maintain viewport frame
        text_lines = self.output_text.split("\n")
        visible_text = Text("\n").join(text_lines[-25:])
        return Panel(
            visible_text,
            title="[bold gold1] OUTPUT STREAM [/bold gold1]",
            title_align="left",
            box=box.HEAVY,
            border_style="bright_blue",
        )

    def render_sidebar(self) -> Panel:
        table = Table(show_header=False, expand=True, box=None)
        table.add_column("Key", style="bold dim white")
        table.add_column("Value", style="bright_yellow")

        pressure = (self.current_tokens / self.max_context) * 100
        table.add_row("RUNNER PID", str(self.runner_pid))
        table.add_row("MODEL", os.getenv("OLLAMA_MODEL", "llama3"))
        table.add_row("TOKENS", f"{self.current_tokens}")
        table.add_row("CAPACITY", f"{self.max_context}")
        table.add_row("PRESSURE", f"{pressure:.1f}%")
        table.add_row("TIME", datetime.now().strftime("%H:%M:%S"))

        return Panel(
            table,
            title="[bold gold1] DIAGNOSTICS [/bold gold1]",
            box=box.ROUNDED,
            border_style="gold1",
        )

    def render_footer(self) -> Panel:
        controls = Text.from_markup(
            "[bold white_on_blue] R [/bold white_on_blue] [bold bright_white]REBOOT SESSION[/bold bright_white]    "
            "[bold white_on_red] Q [/bold white_on_red] [bold bright_white]QUIT PROGRAM[/bold bright_white]"
        )
        return Panel(Align.center(controls), box=box.SQUARE, border_style="grey35")

    def update(self) -> Layout:
        self.layout["header"].update(self.render_header())
        self.layout["main"].update(self.render_main())
        self.layout["sidebar"].update(self.render_sidebar())
        self.layout["footer"].update(self.render_footer())
        return self.layout


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


def _terminate_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_supervisor() -> int:
    global STOP_REQUESTED
    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)

    keyboard_settings = _setup_keyboard()
    ui = SoliloquyUI(max_context=MAX_CONTEXT)

    try:
        with Live(ui.update(), console=console, refresh_per_second=20, screen=True) as live:
            while not STOP_REQUESTED:
                runner = _start_runner()
                ui.runner_pid = runner.pid
                ui.status_msg = "RUNNING"
                turn_header_added = False
                live.update(ui.update())

                assert runner.stdout is not None
                assert runner.stderr is not None

                selector = selectors.DefaultSelector()
                selector.register(runner.stdout, selectors.EVENT_READ)
                selector.register(runner.stderr, selectors.EVENT_READ)
                if keyboard_settings:
                    selector.register(sys.stdin, selectors.EVENT_READ)

                decoder = codecs.getincrementaldecoder("utf-8")("replace")
                action = None

                while not STOP_REQUESTED:
                    events = selector.select(timeout=0.05)
                    if not events:
                        if runner.poll() is not None:
                            break
                        live.update(ui.update())
                        continue

                    for key, _ in events:
                        if key.fileobj is sys.stdin:
                            k = _read_key(True)
                            if k in {"r", "q"}:
                                action = "restart" if k == "r" else "quit"
                                _terminate_process_group(runner.pid)
                                break
                        elif key.fileobj is runner.stderr:
                            err_data = os.read(runner.stderr.fileno(), 1024)
                            for line in err_data.split(b"\n"):
                                if line.startswith(b"CONTEXT_TOKENS:"):
                                    try:
                                        ui.current_tokens = int(line.partition(b":")[2])
                                    except ValueError:
                                        pass
                        elif key.fileobj is runner.stdout:
                            out_data = os.read(runner.stdout.fileno(), 1024)
                            if out_data:
                                if not turn_header_added:
                                    ui.start_new_output_turn()
                                    turn_header_added = True
                                text = decoder.decode(out_data)
                                ui.append_stream_text(text)
                                live.update(ui.update())

                    if action or runner.poll() is not None:
                        break

                _terminate_process_group(runner.pid)
                if action == "quit" or STOP_REQUESTED:
                    break

                ui.status_msg = "PAUSED - REBOOTING"
                live.update(ui.update())
                time.sleep(1.0)

        return 0
    finally:
        _restore_keyboard(keyboard_settings)


if __name__ == "__main__":
    raise SystemExit(run_supervisor())