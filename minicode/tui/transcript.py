from __future__ import annotations

import re
import shutil
from typing import Any

from .chrome import char_display_width, wrap_panel_body_line
from .markdown import render_markdownish

RESET = "\u001b[0m"
DIM = "\u001b[2m"
CYAN = "\u001b[36m"
GREEN = "\u001b[32m"
YELLOW = "\u001b[33m"
RED = "\u001b[31m"
MAGENTA = "\u001b[35m"
BOLD = "\u001b[1m"
BLUE = "\u001b[34m"
REVERSE = "\u001b[7m"


def strip_ansi(value: str) -> str:
    return re.sub(r"\u001b\[[\d;]*[A-Za-z]", "", value)


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
        limited = limited[:max_chars] + "..."
    if limited != body:
        return f"{limited}\n{DIM}... output truncated in transcript{RESET}"
    return limited


def render_transcript_entry(entry: dict[str, Any]) -> str:
    kind = entry.get("kind")
    if kind == "user":
        return f"{CYAN}{BOLD}you{RESET}\n{indent_block(str(entry.get('body') or ''))}"
    if kind == "assistant":
        return f"{GREEN}{BOLD}assistant{RESET}\n{indent_block(render_markdownish(str(entry.get('body') or '')))}"
    if kind == "progress":
        return f"{YELLOW}{BOLD}progress{RESET}\n{indent_block(render_markdownish(str(entry.get('body') or '')))}"
    status = f"{YELLOW}running{RESET}" if entry.get("status") == "running" else f"{GREEN}ok{RESET}" if entry.get("status") == "success" else f"{RED}err{RESET}"
    if entry.get("status") == "running":
        body = str(entry.get("body") or "")
    elif entry.get("collapsed"):
        body = f"{DIM}{entry.get('collapsedSummary') or 'output collapsed'}{RESET}"
    elif entry.get("collapsePhase"):
        body = f"{DIM}collapsing{'.' * int(entry.get('collapsePhase'))}{RESET}"
    else:
        body = preview_tool_body(str(entry.get("toolName") or "unknown"), render_markdownish(str(entry.get("body") or "")))
    return f"{MAGENTA}{BOLD}tool{RESET} {entry.get('toolName') or 'unknown'} {status}\n{indent_block(body)}"


def get_transcript_panel_width() -> int:
    return max(60, shutil.get_terminal_size((100, 40)).columns)


def get_transcript_window_size(window_size: int | None = None) -> int:
    if window_size is not None:
        return max(4, window_size)
    return max(8, shutil.get_terminal_size((100, 40)).lines - 15)


def render_transcript_lines(entries: list[dict[str, Any]]) -> list[str]:
    rendered = [render_transcript_entry(entry) for entry in entries]
    separator = f"{BLUE}{DIM}·{RESET}"
    logical: list[str] = []
    for index, block in enumerate(rendered):
        if index > 0:
            logical.extend(["", separator, ""])
        logical.extend(block.split("\n"))
    width = get_transcript_panel_width()
    lines: list[str] = []
    for line in logical:
        lines.extend(wrap_panel_body_line(line, width))
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
