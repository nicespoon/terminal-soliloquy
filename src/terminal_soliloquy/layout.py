from typing import List, Optional, Tuple, Union
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
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
    progress_text.append(f"{used_tokens:,}/{max_tokens:,} ⏣", style="green1")

    # padding=(0, 1) adds horizontal space between grid columns
    header_table = Table.grid(expand=True, padding=(0, 1))
    header_table.add_column(justify="left", ratio=1)
    header_table.add_column(justify="right")
    header_table.add_column(justify="right")
    header_table.add_row(title_text, progress_text, bar)

    return Panel(header_table, box=box.ROUNDED, border_style="dim green", padding=(0, 1))


def make_main_table(entries: List[Tuple[str, Union[str, Text]]], exhausted: bool) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        border_style=COLOR_DIM,
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
        border_style="green1",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def make_footer(status: str, auto_restart: bool) -> Panel:
    footer_text = Text()
    footer_text.append(" Q ", style=COLOR_KEY_BADGE)
    footer_text.append(" Quit  ", style="green1")
    footer_text.append(" R ", style=COLOR_KEY_BADGE)
    footer_text.append(" Restart  ", style="green1")
    footer_text.append(" A ", style=COLOR_KEY_BADGE)
    footer_text.append(f" Auto-Restart: [{'ON' if auto_restart else 'OFF'}]  ", style="green1")

    status_table = Table.grid(expand=True)
    status_table.add_column(justify="left")
    status_table.add_column(justify="right")

    # Render default Rich 'dots' spinner for active/in-progress statuses
    if status.endswith("...") or any(kw in status for kw in ("THINKING", "STREAMING", "INITIALISING", "RESTARTING")):
        status_display = Spinner("dots", text=Text(status, style=COLOR_HEADER), style="bold bright_green")
    else:
        status_display = Text(status, style=COLOR_HEADER)

    status_table.add_row(footer_text, status_display)

    return Panel(status_table, box=box.ROUNDED, border_style="dim green", padding=(0, 1))


def build_layout(
    used_tokens: int,
    max_tokens: int,
    entries: List[Tuple[str, str]],
    status: str,
    auto_restart: bool,
    model: str,
    screen_padding: Tuple[int, ...] = (0, 0),
    console: Optional[Console] = None,
) -> Layout:
    p = screen_padding
    if len(p) == 1:
        top = right = bottom = left = p[0]
    elif len(p) == 2:
        top = bottom = p[0]; right = left = p[1]
    elif len(p) == 4:
        top, right, bottom, left = p
    else:
        top = right = bottom = left = 0

    console = console or Console()

    # Available row height accounting for header (3), footer (3), main borders (2), and table header (2)
    available_lines = max(1, console.height - 10 - top - bottom)
    if used_tokens >= max_tokens:
        available_lines = max(1, available_lines - 1)

    # Calculate exact column width for text wrapping inside padded panels
    content_width = console.width - left - right
    inner_panel_width = max(20, content_width - 4)
    output_col_width = max(10, inner_panel_width - 12)

    # Iterate backward from newest entry to ensure bottom lines fit
    visible_entries: List[Tuple[str, Union[str, Text]]] = []
    lines_left = available_lines

    for ts, output in reversed(entries):
        if lines_left <= 0:
            break

        try:
            text_obj = Text.from_markup(output)
        except Exception:
            text_obj = Text(output)

        wrapped_lines = text_obj.wrap(console, output_col_width)
        num_lines = max(1, len(wrapped_lines))

        if num_lines <= lines_left:
            visible_entries.append((ts, text_obj))
            lines_left -= num_lines
        else:
            # Keep only the tail end of the entry that fits on screen
            trimmed_lines = wrapped_lines[-lines_left:]
            trimmed_text = Text()
            for i, line in enumerate(trimmed_lines):
                if i > 0:
                    trimmed_text.append("\n")
                trimmed_text.append(line)
            visible_entries.append((ts, trimmed_text))
            lines_left = 0
            break

    visible_entries.reverse()

    # Core layout structure
    content = Layout(name="content")
    content.split_column(
        Layout(make_header(used_tokens, max_tokens, model), size=3),
        Layout(make_main_table(visible_entries, used_tokens >= max_tokens), ratio=1),
        Layout(make_footer(status, auto_restart), size=3),
    )

    # Horizontal padding split
    if left or right:
        h_layout = Layout(name="h_pad")
        cols = []
        if left:
            cols.append(Layout(Text(" "), size=left))
        cols.append(content)
        if right:
            cols.append(Layout(Text(" "), size=right))
        h_layout.split_row(*cols)
        content = h_layout

    # Vertical padding split
    if top or bottom:
        v_layout = Layout(name="v_pad")
        rows = []
        if top:
            rows.append(Layout(Text(" "), size=top))
        rows.append(content)
        if bottom:
            rows.append(Layout(Text(" "), size=bottom))
        v_layout.split_column(*rows)
        content = v_layout

    return content