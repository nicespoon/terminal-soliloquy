#!/usr/bin/env python3
"""Glaydos-style Fullscreen Display Supervisor (Ultra-Compact Edition)."""

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

    def start_new_output_turn(self) -> None:
        now_str = datetime.now().strftime("%H:%M:%S")
        if len(self.output_text) > 0 and not self.output_text.plain.endswith("\n"):
            self.output_text.append("\n")

        target_width = max(10, console.width - 2)
        prefix = f"─ [{now_str}] "
        fill_count = max(0, target_width - len(prefix))
        divider = f"{prefix}{'─' * fill_count}\n"

        self.output_text.append(divider, style="bold dim cyan")

    def append_stream_text(self, text: str) -> None:
        self.output_text.append(text, style="bright_white")

    def render_ultra_compact() -> Group:
        """Render borderless UI designed specifically for 4.5" screens."""
        w = console.width
        h = console.height
        pressure = min(100.0, (self.current_tokens / self.max_context) * 100)
        p_style = get_pressure_style(pressure)

        # 1. Top status line (1 row)
        status_line = Text("SOLILOQUY ", style="bold gold1")
        status_line.append(f"[{self.status_msg[:3]}] ", style=p_style)
        status_line.append(
            f"{self.current_tokens}/{self.max_context} ({pressure:.0f}%)",
            style="bold white",
        )

        # 2. Top divider line (1 row)
        top_divider = Text("─" * w, style="bold dim blue")

        # 3. Main Stream Area (Height - 4 rows)
        visible_rows = max(2, h - 4)
        text_lines = self.output_text.split("\n")
        visible_text = Text("\n").join(text_lines[-visible_rows:])

        # 4. Bottom divider line (1 row)
        bot_divider = Text("─" * w, style="bold dim grey35")

        # 5. Footer line (1 row)
        footer_line = Text.from_markup(
            "[bold white_on_blue] R [/] REBOOT   [bold white_on_red] Q [/] QUIT"
        )

        return Group(
            status_line,
            top_divider,
            visible_text,
            bot_divider,
            footer_line,
        )

    def render_header(self) -> Panel:
        pressure = min(100.0, (self.current_tokens / self.max_context) * 100)
        style = get_pressure_style(pressure)

        title = Text("TERMINAL SOLILOQUY ", style="bold bright_white")
        title.append(":: SYSTEM MONITOR", style="bold gold1")
        title.append(f"  [{self.status_msg}]", style=style)

        bar = Progress(
            TextColumn("[bold grey70]CONTEXT:"),
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
        height = console.height
        visible_rows = max(3, height - 9)
        text_lines = self.output_text.split("\n")
        visible_text = Text("\n").join(text_lines[-visible_rows:])

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
        table.add_row("PID", str(self.runner_pid))
        table.add_row("MODEL", os.getenv("OLLAMA_MODEL", "llama3"))
        table.add_row("TOKENS", f"{self.current_tokens}")
        table.add_row("CAPACITY", f"{self.max_context}")
        table.add_row("PRESSURE", f"{pressure:.1f}%")
        table.add_row("TIME", datetime.now().strftime("%H:%M:%S"))

        return Panel(
            table,
            title="[bold gold1] DIAG [/bold gold1]",
            box=box.ROUNDED,
            border_style="gold1",
        )

    def render_footer(self) -> Panel:
        controls = Text.from_markup(
            "[bold white_on_blue] R [/bold white_on_blue] [bold bright_white]REBOOT SESSION[/bold bright_white]    "
            "[bold white_on_red] Q [/bold white_on_red] [bold bright_white]QUIT PROGRAM[/bold bright_white]"
        )
        return Panel(Align.center(controls), box=box.SQUARE, border_style="grey35")

    def update(self) -> Layout | Group:
        width = console.width
        height = console.height

        # Ultra-Compact mode for small displays (4.5" screens / CRT monitors)
        if width < 60 or height < 18:
            return self.render_ultra_compact()

        layout = Layout()
        layout.split(
            Layout(name="header", size=4),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3),
        )
        layout["body"].split_row(
            Layout(name="main", ratio=3),
            Layout(name="sidebar", ratio=1),
        )
        layout["header"].update(self.render_header())
        layout["main"].update(self.render_main())
        layout["sidebar"].update(self.render_sidebar())
        layout["footer"].update(self.render_footer())

        return layout


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
        with Live(ui.update(), console=console, refresh_per_second=10, screen=True) as live:
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

                ui.status_msg = "REBOOTING"
                live.update(ui.update())
                time.sleep(1.0)

        return 0
    finally:
        _restore_keyboard(keyboard_settings)


if __name__ == "__main__":
    raise SystemExit(run_supervisor())