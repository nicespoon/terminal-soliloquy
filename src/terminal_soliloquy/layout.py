import time
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
COLOR_KEY_BADGE = "bold #000000 on green1"
COLOR_EXHAUSTION = "bold #000000 on bright_yellow"
CURSOR_MARK = "\x00"
CURSOR_STYLE = "bold #000000 on bright_green"


def make_header(used_tokens: int, max_tokens: int, model: str) -> Panel:
    pct = min(100.0, (used_tokens / max_tokens) * 100) if max_tokens > 0 else 0.0
    exhausted = used_tokens >= max_tokens
    progress_style = "yellow" if exhausted else "bright_green"

    bar = ProgressBar(
        total=max_tokens,
        completed=used_tokens,
        width=12,
        style="color(234)",
        complete_style=progress_style,
        finished_style=f"bold {progress_style}",
    )

    title_text = Text()
    title_text.append("TERMINAL SOLILOQUY", style=COLOR_HEADER)
    title_text.append(f" {model}", style=COLOR_DIM)

    progress_text = Text()
    progress_text.append(
        f"{used_tokens:,}/{max_tokens:,} ⏣",
        style="yellow" if exhausted else "green1",
    )

    header_table = Table.grid(expand=True, padding=(0, 1))
    header_table.add_column(justify="left", ratio=1)
    header_table.add_column(justify="right")
    header_table.add_column(justify="right")
    header_table.add_row(title_text, progress_text, bar)

    border_style = "yellow" if exhausted else "dim green"
    return Panel(header_table, box=box.ROUNDED, border_style=border_style, padding=(0, 1))


def make_main_table(entries: List[Tuple[str, Union[str, Text]]]) -> Panel:
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

    for entry_index, (timestamp, output) in enumerate(entries):
        if entry_index > 0:
            table.add_row("", "")
        table.add_row(timestamp, output)

    return Panel(
        table,
        border_style="green1",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def make_end_panel(used_tokens: int, max_tokens: int, compact: bool) -> Panel:
    title = Text(" CONTEXT FULL ", style=COLOR_EXHAUSTION)
    token_count = Text(f"{used_tokens:,} / {max_tokens:,} TOKENS", style="bold yellow")
    message = Text()
    message.append_text(title)
    if compact:
        message.append("\n")
    else:
        message.append("  ")
    message.append_text(token_count)

    return Panel(message, box=box.ROUNDED, border_style="yellow", padding=(0, 1))


def make_footer(
    status: str,
    end_behavior: Union[str, bool] = "freeze",
    exhausted: bool = False,
) -> Panel:
    if isinstance(end_behavior, bool):
        behavior_label = "RESTART" if end_behavior else "FREEZE"
    else:
        behavior_label = str(end_behavior).upper()

    footer_text = Text()
    footer_text.append(" Q ", style=COLOR_KEY_BADGE)
    footer_text.append(" Quit  ", style="green1")
    footer_text.append(" R ", style=COLOR_KEY_BADGE)
    footer_text.append(" Restart  ", style="green1")
    footer_text.append(" E ", style=COLOR_KEY_BADGE)
    footer_text.append(f" End: [{behavior_label}]  ", style="green1")

    status_table = Table.grid(expand=True)
    status_table.add_column(justify="left")
    status_table.add_column(justify="right")

    if exhausted:
        status_display = Text(status, style="bold yellow")
    else:
        status_display = Spinner(
            "dots",
            text=Text(status, style=COLOR_HEADER),
            style="bold bright_green",
        )

    status_table.add_row(footer_text, status_display)

    return Panel(status_table, box=box.ROUNDED, border_style="dim green", padding=(0, 1))


def build_layout(
    used_tokens: int,
    max_tokens: int,
    entries: List[Tuple[str, str]],
    status: str,
    end_behavior: Union[str, bool] = "freeze",
    model: str = "default",
    screen_padding: Tuple[int, ...] = (0, 0),
    console: Optional[Console] = None,
    auto_restart: Optional[bool] = None,
) -> Layout:
    if auto_restart is not None:
        end_behavior = auto_restart
    padding = screen_padding
    if len(padding) == 1:
        top = right = bottom = left = padding[0]
    elif len(padding) == 2:
        top = bottom = padding[0]
        right = left = padding[1]
    elif len(padding) == 4:
        top, right, bottom, left = padding
    else:
        top = right = bottom = left = 0

    console = console or Console()

    exhausted = used_tokens >= max_tokens
    content_width = console.width - left - right
    compact_end_panel = content_width < 68
    end_panel_size = (4 if compact_end_panel else 3) if exhausted else 0

    # Reserve space for header, footer, table framing, and the exhaustion panel.
    available_lines = max(1, console.height - 10 - top - bottom - end_panel_size)

    # Calculate exact column width for text wrapping inside padded panels
    inner_panel_width = max(20, content_width - 4)
    output_col_width = max(10, inner_panel_width - 13)

    # Iterate backward from newest entry to ensure bottom lines fit
    visible_entries: List[Tuple[str, Union[str, Text]]] = []
    lines_left = available_lines

    for timestamp, output in reversed(entries):
        if lines_left <= 0:
            break

        needed_spacing = 1 if visible_entries else 0
        if lines_left <= needed_spacing:
            break

        lines_left -= needed_spacing

        has_cursor = output.endswith(CURSOR_MARK)
        if has_cursor:
            output = output[: -len(CURSOR_MARK)]

        try:
            text_obj = Text.from_markup(output)
        except Exception:
            text_obj = Text(output)

        wrapped_lines = text_obj.wrap(console, output_col_width)

        if has_cursor:
            # Wrap the text alone, then place the cursor after it so it never reflows words
            wrapped_lines = list(wrapped_lines)
            waiting = not output.strip()
            cursor_on = not waiting or int(time.time() * 2) % 2 == 0
            cursor = Text(" ", style=CURSOR_STYLE if cursor_on else "")
            last = wrapped_lines[-1] if wrapped_lines else None
            if last is not None and last.cell_len < output_col_width:
                last.rstrip_end(output_col_width)
                last.append_text(cursor)
            else:
                wrapped_lines.append(cursor)
            text_obj = Text("\n").join(wrapped_lines)
            wrapped_lines = list(wrapped_lines)

        num_lines = max(1, len(wrapped_lines))

        if num_lines <= lines_left:
            visible_entries.append((timestamp, text_obj))
            lines_left -= num_lines
        else:
            # Keep only the tail end of the entry that fits on screen
            trimmed_lines = wrapped_lines[-lines_left:]
            trimmed_text = Text()
            for line_index, line in enumerate(trimmed_lines):
                if line_index > 0:
                    trimmed_text.append("\n")
                trimmed_text.append(line)
            visible_entries.append((timestamp, trimmed_text))
            lines_left = 0
            break

    visible_entries.reverse()

    # Core layout structure
    content = Layout(name="content")
    header = Layout(make_header(used_tokens, max_tokens, model), size=3)
    main = Layout(make_main_table(visible_entries), ratio=1)
    footer = Layout(
        make_footer(status, end_behavior, exhausted=exhausted),
        size=3,
    )
    if exhausted:
        content.split_column(
            header,
            main,
            Layout(
                make_end_panel(used_tokens, max_tokens, compact_end_panel),
                size=end_panel_size,
            ),
            footer,
        )
    else:
        content.split_column(header, main, footer)

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