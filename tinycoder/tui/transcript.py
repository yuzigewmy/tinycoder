from __future__ import annotations

import shutil
from typing import Any

from .chrome import (
    char_display_width,
    render_activity_line,
    render_assistant_heading,
    strip_ansi,
    wrap_panel_body_line,
)
from .markdown import render_markdownish
from .theme import (
    ACCENT,
    BOLD,
    BORDER_SOFT,
    DIM,
    MUTED,
    RESET,
    REVERSE,
    WARNING,
    glyph,
    style,
)


def slice_by_display_columns(
    input_text: str,
    start_col: int,
    end_col: int | float,
) -> str:
    if start_col >= end_col:
        return ""
    result = ""
    col = 0
    for ch in input_text:
        width = char_display_width(ch)
        next_col = col + width
        if next_col <= start_col:
            col = next_col
            continue
        if col >= end_col:
            break
        result += ch
        col = next_col
    return result


def highlight_range(
    line: str,
    start_col: int,
    end_col: int | float,
) -> str:
    if start_col >= end_col:
        return line
    result = ""
    visible_col = 0
    index = 0
    highlighted = False
    while index < len(line):
        if line[index] == "\u001b":
            escape_start = index
            index += 1
            if index < len(line) and line[index] == "[":
                index += 1
                while index < len(line) and (
                    line[index] < "@" or line[index] > "~"
                ):
                    index += 1
                index += 1
            sequence = line[escape_start:index]
            result += sequence
            if sequence == RESET and highlighted:
                result += REVERSE
            continue
        ch = line[index]
        width = char_display_width(ch)
        if not highlighted and visible_col >= start_col:
            result += REVERSE
            highlighted = True
        if (
            not highlighted
            and visible_col < start_col
            and visible_col + width > start_col
        ):
            result += REVERSE
            highlighted = True
        if highlighted and visible_col >= end_col:
            result += RESET
            highlighted = False
        result += ch
        visible_col += width
        index += 1
        if highlighted and visible_col >= end_col:
            result += RESET
            highlighted = False
    if highlighted:
        result += RESET
    return result


def indent_block(input_text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in input_text.split("\n"))


def preview_tool_body(tool_name: str, body: str) -> str:
    max_chars = 1000 if tool_name == "read_file" else 1800
    max_lines = 20 if tool_name == "read_file" else 36
    lines = body.split("\n")
    limited = "\n".join(lines[:max_lines])
    if len(limited) > max_chars:
        limited = limited[:max_chars] + "..."
    if limited != body:
        return (
            f"{limited}\n"
            f"{style('... 工具输出已在会话视图中截断', MUTED)}"
        )
    return limited


def _render_user_entry(body: str) -> str:
    top = (
        f"{style(glyph('╭─', '+-'), ACCENT)} "
        f"{style('YOU', ACCENT, BOLD)}"
    )
    rail = style(glyph("│", "|"), BORDER_SOFT)
    bottom = style(glyph("╰─", "+-"), BORDER_SOFT)
    content = "\n".join(f"{rail} {line}" for line in body.split("\n"))
    return f"{top}\n{content}\n{bottom}"


def _render_assistant_entry(body: str) -> str:
    rendered = render_markdownish(body)
    return f"{render_assistant_heading()}\n{indent_block(rendered)}"


def _render_progress_entry(body: str) -> str:
    marker = glyph("◇", "*")
    heading = (
        f"{style(marker, WARNING)} "
        f"{style('AGENT ACTIVITY', WARNING, BOLD)}"
    )
    return f"{heading}\n{indent_block(render_markdownish(body))}"


def _render_tool_entry(entry: dict[str, Any]) -> str:
    status = str(entry.get("status") or "error")
    tool_name = str(entry.get("toolName") or "unknown")
    raw_body = str(entry.get("body") or "")
    if status == "running":
        return render_activity_line(tool_name, raw_body, "running")
    if entry.get("collapsed"):
        body = style(
            str(entry.get("collapsedSummary") or "工具输出已折叠"),
            MUTED,
        )
    elif entry.get("collapsePhase"):
        phase = "." * int(entry.get("collapsePhase") or 0)
        body = style(f"正在折叠工具输出{phase}", MUTED)
    else:
        body = preview_tool_body(tool_name, render_markdownish(raw_body))
    heading = render_activity_line(tool_name, "", status)
    return f"{heading}\n{indent_block(body, '     ')}" if body else heading


def render_transcript_entry(entry: dict[str, Any]) -> str:
    kind = entry.get("kind")
    body = str(entry.get("body") or "")
    if kind == "user":
        return _render_user_entry(body)
    if kind == "assistant":
        return _render_assistant_entry(body)
    if kind == "progress":
        return _render_progress_entry(body)
    return _render_tool_entry(entry)


def get_transcript_panel_width() -> int:
    return max(20, min(104, shutil.get_terminal_size((100, 40)).columns))


def get_transcript_window_size(window_size: int | None = None) -> int:
    if window_size is not None:
        return max(4, window_size)
    return max(8, shutil.get_terminal_size((100, 40)).lines - 15)


def render_transcript_lines(entries: list[dict[str, Any]]) -> list[str]:
    logical: list[str] = []
    for index, entry in enumerate(entries):
        if index:
            logical.append("")
        logical.extend(render_transcript_entry(entry).split("\n"))
    width = get_transcript_panel_width()
    lines: list[str] = []
    for line in logical:
        # wrap_panel_body_line reserves panel gutters; adding four keeps transcript
        # text within the real terminal width without duplicating wrapping logic.
        lines.extend(wrap_panel_body_line(line, width + 4))
    return lines


def get_transcript_max_scroll_offset(
    entries: list[dict[str, Any]],
    window_size: int | None = None,
) -> int:
    if not entries:
        return 0
    return max(
        0,
        len(render_transcript_lines(entries))
        - get_transcript_window_size(window_size),
    )


def render_transcript(
    entries: list[dict[str, Any]],
    scroll_offset: int,
    window_size: int | None = None,
    selection: dict[str, Any] | None = None,
) -> str:
    if not entries:
        return ""
    lines = render_transcript_lines(entries)
    page_size = get_transcript_window_size(window_size)
    max_offset = max(0, len(lines) - page_size)
    offset = max(0, min(scroll_offset, max_offset))
    end = len(lines) - offset
    start = max(0, end - page_size)
    if selection:
        start_line = int(selection.get("startLine") or 0)
        end_line = int(selection.get("endLine") or 0)
        start_col = int(selection.get("startCol") or 0)
        end_col_value = selection.get("endCol")
        end_col = int(end_col_value if end_col_value is not None else 0)
        highlighted_lines = []
        for index, line in enumerate(lines):
            if index < start_line or index > end_line:
                highlighted_lines.append(line)
            elif index == start_line and index == end_line:
                highlighted_lines.append(
                    highlight_range(line, start_col, end_col)
                )
            elif index == start_line:
                highlighted_lines.append(
                    highlight_range(line, start_col, float("inf"))
                )
            elif index == end_line:
                highlighted_lines.append(highlight_range(line, 0, end_col))
            else:
                highlighted_lines.append(
                    highlight_range(line, 0, float("inf"))
                )
        lines = highlighted_lines
    body = "\n".join(lines[start:end])
    if offset == 0:
        return body
    return f"{body}\n\n{style(f'已向上滚动 {offset} 行', DIM)}"


def extract_selected_text(
    entries: list[dict[str, Any]],
    selection: dict[str, Any],
) -> str:
    lines = render_transcript_lines(entries)
    start_line = int(selection.get("startLine") or 0)
    end_line = int(selection.get("endLine") or 0)
    start_col = int(selection.get("startCol") or 0)
    end_col = int(selection.get("endCol") or 0)
    result: list[str] = []
    for index in range(start_line, min(end_line, len(lines) - 1) + 1):
        plain = strip_ansi(lines[index])
        if index == start_line and index == end_line:
            result.append(
                slice_by_display_columns(plain, start_col, end_col)
            )
        elif index == start_line:
            result.append(
                slice_by_display_columns(plain, start_col, float("inf"))
            )
        elif index == end_line:
            result.append(slice_by_display_columns(plain, 0, end_col))
        else:
            result.append(plain)
    return "\n".join(result)


renderTranscript = render_transcript
getTranscriptMaxScrollOffset = get_transcript_max_scroll_offset
getTranscriptWindowSize = get_transcript_window_size
extractSelectedText = extract_selected_text
renderTranscriptLines = render_transcript_lines
