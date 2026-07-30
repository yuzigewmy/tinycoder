from __future__ import annotations

import shutil
from typing import Any

from .chrome import char_display_width, get_work_area_width, string_display_width, wrap_panel_body_line
from .markdown import render_markdownish
from .theme import ACCENT, BOLD, DANGER, DIM, MUTED, PRIMARY, PRIMARY_SOFT, RAIL, RESET, REVERSE, SUCCESS, TOOL, color_enabled, paint, strip_ansi


def slice_by_display_columns(input_text: str, start_col: int, end_col: int | float) -> str:
    if start_col >= end_col:
        return ""
    result = ""; col = 0
    for ch in input_text:
        width = char_display_width(ch)
        next_col = col + width
        if next_col <= start_col:
            col = next_col; continue
        if col >= end_col:
            break
        result += ch
        col = next_col
    return result


def highlight_range(line: str, start_col: int, end_col: int | float) -> str:
    if start_col >= end_col:
        return line
    if not color_enabled():
        return line
    result = ""; visible_col = 0; i = 0; highlighted = False
    while i < len(line):
        if line[i] == "\u001b":
            escape_start = i; i += 1
            if i < len(line) and line[i] == "[":
                i += 1
                while i < len(line) and (line[i] < "@" or line[i] > "~"):
                    i += 1
                i += 1
            seq = line[escape_start:i]
            result += seq
            if seq == RESET and highlighted:
                result += REVERSE
            continue
        ch = line[i]
        width = char_display_width(ch)
        if not highlighted and visible_col >= start_col:
            result += REVERSE; highlighted = True
        if not highlighted and visible_col < start_col and visible_col + width > start_col:
            result += REVERSE; highlighted = True
        if highlighted and visible_col >= end_col:
            result += RESET; highlighted = False
        result += ch
        visible_col += width
        i += 1
        if highlighted and visible_col >= end_col:
            result += RESET; highlighted = False
    if highlighted:
        result += RESET
    return result


def indent_block(input_text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in input_text.split("\n"))


def preview_tool_body(tool_name: str, body: str) -> str:
    max_chars = 1000 if tool_name == "read_file" else 1800
    max_lines = 20 if tool_name == "read_file" else 36
    lines = body.split("\n")
    limited_lines = lines[:max_lines]
    limited = "\n".join(limited_lines)
    if len(limited) > max_chars:
        limited = limited[:max_chars] + "…"
    if limited != body:
        return f"{limited}\n{paint('… output truncated in transcript', MUTED, DIM)}"
    return limited


def _render_track_block(label: str, body: str, color: str, *, meta: str = "") -> str:
    suffix = f"  {paint(meta, MUTED, DIM)}" if meta else ""
    source_lines = render_markdownish(body).split("\n") if body else [""]
    lines: list[str] = []
    for source_line in source_lines:
        # Reserve five columns for the timeline rail and repeat it on every
        # wrapped continuation instead of letting text fall out of the frame.
        lines.extend(wrap_panel_body_line(source_line, max(8, get_work_area_width() - 1)))
    return "\n".join(
        [
            f"{paint('  ╭─', RAIL)} {paint(label, color, BOLD)}{suffix}",
            *[f"{paint('  │', RAIL)}  {line}" if line else paint("  │", RAIL) for line in lines],
            paint("  ╰─", RAIL),
        ]
    )


def render_transcript_entry(entry: dict[str, Any]) -> str:
    kind = entry.get("kind")
    if kind == "user":
        return _render_track_block("you", str(entry.get("body") or ""), PRIMARY_SOFT)
    if kind == "assistant":
        return _render_track_block("tinycoder", str(entry.get("body") or ""), PRIMARY)
    if kind == "progress":
        return _render_track_block("思考", str(entry.get("body") or ""), ACCENT)
    status_name = str(entry.get("status") or "error")
    status = "运行中" if status_name == "running" else "完成" if status_name == "success" else "失败"
    status_color = ACCENT if status_name == "running" else SUCCESS if status_name == "success" else DANGER
    if entry.get("status") == "running":
        body = str(entry.get("body") or "")
    elif entry.get("collapsed"):
        body = str(entry.get("collapsedSummary") or "output collapsed")
    elif entry.get("collapsePhase"):
        body = f"collapsing{'.' * int(entry.get('collapsePhase'))}"
    else:
        body = preview_tool_body(str(entry.get("toolName") or "unknown"), render_markdownish(str(entry.get("body") or "")))
    glyph = "◆" if status_name == "running" else "✓" if status_name == "success" else "×"
    label = f"{glyph} 工具 · {entry.get('toolName') or 'unknown'}"
    return _render_track_block(label, body, TOOL, meta=paint(status, status_color, BOLD))


def get_transcript_panel_width() -> int:
    return get_work_area_width()


def get_transcript_window_size(window_size: int | None = None) -> int:
    if window_size is not None:
        return max(4, window_size)
    return max(8, shutil.get_terminal_size((100, 40)).lines - 15)


def render_transcript_lines(entries: list[dict[str, Any]]) -> list[str]:
    rendered = [render_transcript_entry(entry) for entry in entries]
    logical: list[str] = []
    for index, block in enumerate(rendered):
        if index > 0:
            logical.append("")
        logical.extend(block.split("\n"))
    width = get_transcript_panel_width()
    lines: list[str] = []
    for line in logical:
        lines.extend(wrap_panel_body_line(line, width) if string_display_width(line) > width else [line])
    return lines


def get_transcript_max_scroll_offset(entries: list[dict[str, Any]], window_size: int | None = None) -> int:
    if not entries:
        return 0
    return max(0, len(render_transcript_lines(entries)) - get_transcript_window_size(window_size))


def render_transcript(entries: list[dict[str, Any]], scroll_offset: int, window_size: int | None = None, selection: dict[str, Any] | None = None) -> str:
    if not entries:
        return ""
    lines = render_transcript_lines(entries)
    page_size = get_transcript_window_size(window_size)
    max_offset = max(0, len(lines) - page_size)
    offset = max(0, min(scroll_offset, max_offset))
    end = len(lines) - offset
    start = max(0, end - page_size)
    if selection:
        start_line = int(selection.get("startLine") or 0); end_line = int(selection.get("endLine") or 0)
        start_col = int(selection.get("startCol") or 0); end_col = selection.get("endCol") if selection.get("endCol") is not None else 0
        end_col = int(end_col)
        new_lines = []
        for index, line in enumerate(lines):
            if index < start_line or index > end_line:
                new_lines.append(line)
            elif index == start_line and index == end_line:
                new_lines.append(highlight_range(line, start_col, end_col))
            elif index == start_line:
                new_lines.append(highlight_range(line, start_col, float("inf")))
            elif index == end_line:
                new_lines.append(highlight_range(line, 0, end_col))
            else:
                new_lines.append(highlight_range(line, 0, float("inf")))
        lines = new_lines
    body = "\n".join(lines[start:end])
    return body if offset == 0 else f"{body}\n\n{DIM}scroll offset: {offset}{RESET}"


def extract_selected_text(entries: list[dict[str, Any]], selection: dict[str, Any]) -> str:
    lines = render_transcript_lines(entries)
    start_line = int(selection.get("startLine") or 0); end_line = int(selection.get("endLine") or 0)
    start_col = int(selection.get("startCol") or 0); end_col = int(selection.get("endCol") or 0)
    result: list[str] = []
    for i in range(start_line, min(end_line, len(lines) - 1) + 1):
        plain = strip_ansi(lines[i])
        if i == start_line and i == end_line:
            result.append(slice_by_display_columns(plain, start_col, end_col))
        elif i == start_line:
            result.append(slice_by_display_columns(plain, start_col, float("inf")))
        elif i == end_line:
            result.append(slice_by_display_columns(plain, 0, end_col))
        else:
            result.append(plain)
    return "\n".join(result)


renderTranscript = render_transcript
getTranscriptMaxScrollOffset = get_transcript_max_scroll_offset
getTranscriptWindowSize = get_transcript_window_size
extractSelectedText = extract_selected_text
renderTranscriptLines = render_transcript_lines
