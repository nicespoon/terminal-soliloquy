from typing import List, Tuple
from rich import box
from rich.layout import Layout
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

COLOR_HEADER = "bold bright_green"
COLOR_DIM = "dim green"
COLOR_TIMESTAMP = "bold green"
COLOR_TEXT = "spring_green1"
COLOR_KEY_BADGE = "bold black on green1"
COLOR_EXHAUSTION = "bold black on bright_green"


def make_header(used_tokens: int, max_tokens: int, model: str) -> Panel:
    pct = min(100.0, (used_tokens / max_tokens) * 100) if max_tokens > 0 else 0.0

    bar = ProgressBar(
        total=max_tokens,
        completed=used_tokens,
        width=24,
        style="color(234)",
        complete_style="bright_green",
        finished_style="bold bright_green",
    )

    title_text = Text()
    title_text.append("TERMINAL SOLILOQUY", style=COLOR_HEADER)
    title_text.append(f" {model}", style=COLOR_DIM)

    progress_text = Text()
    progress_text.append(f"Context: {used_tokens:,}/{max_tokens:,} tkn ", style="green1")
    progress_text.append(f"({pct:.1f}%) ", style=COLOR_HEADER)

    header_table = Table.grid(expand=True)
    header_table.add_column(justify="left", ratio=1)
    header_table.add_column(justify="right")
    header_table.add_column(justify="right")
    header_table.add_row(title_text, progress_text, bar)

    return Panel(header_table, box=box.ROUNDED, border_style="dim green", padding=(0, 1))


def make_main_table(entries: List[Tuple[str, str]], exhausted: bool) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
        header_style="bold bright_green",
        pad_edge=False,
        padding=(0, 1),
    )

    table.add_column("TIME", style=COLOR_TIMESTAMP, width=10, no_wrap=True)
    table.add_column("MODEL OUTPUT / CONVERSATION HISTORY", style=COLOR_TEXT, ratio=1)

    for ts, output in entries:
        table.add_row(ts, output)

    if exhausted:
        table.add_row(
            "[FULL]",
            Text(
                " ⚡ CONTEXT WINDOW EXHAUSTION BLOCK REACHED — AWAITING SUPERVISOR RESTART ⚡ ",
                style=COLOR_EXHAUSTION,
            ),
        )

    return Panel(
        table,
        title="[ Soliloquy Stream ]",
        title_align="left",
        border_style="green1",
        box=box.ROUNDED,
        padding=(1, 1),
    )


def make_footer(status: str, auto_restart: bool) -> Panel:
    footer_text = Text()
    footer_text.append(" Q ", style=COLOR_KEY_BADGE)
    footer_text.append(" Quit  ", style="green1")
    footer_text.append(" R ", style=COLOR_KEY_BADGE)
    footer_text.append(" Restart  ", style="green1")
    footer_text.append(" A ", style=COLOR_KEY_BADGE)
    footer_text.append(f" Auto-Restart: [{'ENABLED' if auto_restart else 'DISABLED'}]  ", style="green1")

    status_table = Table.grid(expand=True)
    status_table.add_column(justify="left")
    status_table.add_column(justify="right")

    status_text = Text()
    status_text.append("Supervisor Status: ", style=COLOR_DIM)
    status_text.append(status, style=COLOR_HEADER)

    status_table.add_row(footer_text, status_text)

    return Panel(status_table, box=box.ROUNDED, border_style="dim green", padding=(0, 1))


def build_layout(
    used_tokens: int,
    max_tokens: int,
    entries: List[Tuple[str, str]],
    status: str,
    auto_restart: bool,
    model: str,
) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1),
        Layout(name="footer", size=3),
    )

    exhausted = used_tokens >= max_tokens
    layout["header"].update(make_header(used_tokens, max_tokens, model))
    layout["main"].update(make_main_table(entries, exhausted))
    layout["footer"].update(make_footer(status, auto_restart))

    return layout
