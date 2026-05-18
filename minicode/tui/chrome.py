from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

RESET = "\u001b[0m"
DIM = "\u001b[2m"
CYAN = "\u001b[36m"
GREEN = "\u001b[32m"
YELLOW = "\u001b[33m"
RED = "\u001b[31m"
BLUE = "\u001b[34m"
MAGENTA = "\u001b[35m"
BOLD = "\u001b[1m"
REVERSE = "\u001b[7m"
BRIGHT_GREEN = "\u001b[92m"
BRIGHT_RED = "\u001b[91m"
BRIGHT_CYAN = "\u001b[96m"
BRIGHT_YELLOW = "\u001b[93m"
BORDER = "\u001b[38;5;31m"


def strip_ansi(input_text: str) -> str:
    return re.sub(r"\u001b\[[0-9;]*[A-Za-z]", "", input_text)


def char_display_width(char: str) -> int:
    if not char:
        return 0
    code = ord(char)
    if code >= 0x1100 and (code <= 0x115F or code in (0x2329, 0x232A) or (0x2E80 <= code <= 0xA4CF and code != 0x303F) or (0xAC00 <= code <= 0xD7A3) or (0xF900 <= code <= 0xFAFF) or (0xFE10 <= code <= 0xFE19) or (0xFE30 <= code <= 0xFE6F) or (0xFF00 <= code <= 0xFF60) or (0xFFE0 <= code <= 0xFFE6) or (0x1F300 <= code <= 0x1FAF6) or (0x20000 <= code <= 0x3FFFD)):
        return 2
    return 1


def string_display_width(input_text: str) -> int:
    return sum(char_display_width(ch) for ch in strip_ansi(input_text))


def truncate_plain(input_text: str, width: int) -> str:
    if width <= 0:
        return ""
    if string_display_width(input_text) <= width:
        return input_text
    if width <= 3:
        return input_text[:width]
    target = width - 3
    current = ""
    used = 0
    for ch in input_text:
        w = char_display_width(ch)
        if used + w > target:
            break
        current += ch
        used += w
    return current + "..."


def pad_plain(input_text: str, width: int) -> str:
    visible = string_display_width(input_text)
    return input_text if visible >= width else input_text + " " * (width - visible)


def truncate_path_middle(input_text: str, width: int) -> str:
    if width <= 0 or string_display_width(input_text) <= width:
        return input_text
    if width <= 5:
        return truncate_plain(input_text, width)
    keep = width - 3
    left_target = (keep + 1) // 2
    right_target = keep // 2
    left = ""; used = 0
    for ch in input_text:
        w = char_display_width(ch)
        if used + w > left_target: break
        left += ch; used += w
    right = ""; used = 0
    for ch in reversed(input_text):
        w = char_display_width(ch)
        if used + w > right_target: break
        right = ch + right; used += w
    return f"{left}...{right}"


def color_badge(label: str, value: str, color: str) -> str:
    return f"{color}[{label}]{RESET} {BOLD}{value}{RESET}"


def _terminal_width() -> int:
    try:
        return max(60, shutil.get_terminal_size((100, 40)).columns)
    except Exception:
        return 100


def border_line(kind: str, width: int) -> str:
    inner = max(0, width - 2)
    return f"{BORDER}{'╭' if kind == 'top' else '╰'}{'─' * inner}{'╮' if kind == 'top' else '╯'}{RESET}"


def panel_row(left: str, width: int, right: str | None = None) -> str:
    inner = max(0, width - 4)
    right_text = right or ""
    right_width = string_display_width(right_text)
    left_text = left
    if string_display_width(left_text) + right_width + 1 > inner:
        left_text = truncate_plain(strip_ansi(left_text), max(0, inner - right_width - 1))
    padding = " " * max(0, inner - string_display_width(left_text) - right_width)
    return f"{BORDER}│{RESET} {left_text}{padding}{right_text} {BORDER}│{RESET}"


def empty_panel_row(width: int) -> str:
    return f"{BORDER}│{RESET}{' ' * max(0, width - 2)}{BORDER}│{RESET}"


def wrap_panel_body_line(line: str, width: int) -> list[str]:
    inner = max(0, width - 4)
    if inner <= 0:
        return [""]
    plain = strip_ansi(line)
    if string_display_width(plain) <= inner:
        return [line]
    parts: list[str] = []
    current = ""; current_width = 0
    for ch in plain:
        w = char_display_width(ch)
        if current_width + w > inner:
            parts.append(current)
            current = ch; current_width = w
        else:
            current += ch; current_width += w
    if current:
        parts.append(current)
    return parts


def render_panel(title: str, body: str, options: dict[str, Any] | None = None) -> str:
    options = options or {}
    width = _terminal_width()
    body_lines = body.split("\n") if body else []
    rendered: list[str] = []
    for line in body_lines:
        rendered.extend(wrap_panel_body_line(line, width))
    min_lines = int(options.get("minBodyLines") or 0)
    while len(rendered) < min_lines:
        rendered.append("")
    right = options.get("rightTitle")
    return "\n".join([border_line("top", width), panel_row(f"{BRIGHT_CYAN}{BOLD}{title}{RESET}", width, f"{DIM}{truncate_plain(str(right), max(10, width // 3))}{RESET}" if right else None), empty_panel_row(width), *[panel_row(line, width) for line in rendered], border_line("bottom", width)])


def render_context_badge(stats: dict[str, Any]) -> str:
    utilization = float(stats.get("utilization") or 0)
    warning = stats.get("warningLevel") or "normal"
    percent = round(utilization * 100)
    color = {"normal": GREEN, "warning": YELLOW, "critical": RED, "blocked": BRIGHT_RED}.get(warning, GREEN)
    filled = max(0, min(10, round(utilization * 10)))
    bar = "▓" * filled + "░" * (10 - filled)
    accounting = stats.get("accounting") or {}
    source = {"provider_usage": "usage", "provider_usage_plus_estimate": "usage+est", "estimate_only": "est"}.get(accounting.get("source"), "est")
    return f"{color}{bar}{RESET} {percent}% {DIM}{source}{RESET}"


def render_status_line(runtime: dict[str, Any], cwd: str, stats: dict[str, Any] | None = None) -> str:
    model = str(runtime.get("model") or "mock")
    left = color_badge("model", model, CYAN)
    right_parts = [truncate_path_middle(cwd, 40)]
    if stats:
        right_parts.append(render_context_badge(stats))
    return f"{left}  {DIM}{'  '.join(right_parts)}{RESET}"


def render_banner(runtime: dict[str, Any] | None = None, cwd: str | None = None) -> str:
    runtime = runtime or {}
    cwd = cwd or os.getcwd()
    return render_panel("mini-code", f"model: {runtime.get('model') or 'mock'}\ncwd: {cwd}")


def render_footer_bar(text: str = "Enter send | /help commands | Ctrl+C exit") -> str:
    return f"{DIM}{text}{RESET}"


def render_tool_panel(tool_name: str, body: str, status: str = "running") -> str:
    color = GREEN if status == "success" else RED if status == "error" else YELLOW
    return render_panel(f"tool {tool_name}", body, {"rightTitle": f"{color}{status}{RESET}"})


def render_slash_menu(commands: list[dict[str, Any]], selected_index: int = 0) -> str:
    lines = []
    for i, command in enumerate(commands):
        prefix = f"{REVERSE}>" if i == selected_index else " "
        suffix = RESET if i == selected_index else ""
        lines.append(f"{prefix} /{command.get('name')} {DIM}{command.get('description', '')}{RESET}{suffix}")
    return render_panel("commands", "\n".join(lines))


def _permission_window_size() -> int:
    try:
        return max(6, shutil.get_terminal_size((100, 40)).lines - 10)
    except Exception:
        return 20


def get_permission_prompt_max_scroll_offset(request: dict[str, Any], window_size: int | None = None) -> int:
    lines = [request.get("summary") or "", *[str(x) for x in request.get("details") or []], *[f"{c.get('key')}) {c.get('label')}" for c in request.get("choices") or []]]
    return max(0, len(lines) - (window_size or _permission_window_size()))


def render_permission_prompt(request: dict[str, Any], scroll_offset: int = 0, feedback_input: str = "") -> str:
    lines = [f"{BOLD}{request.get('summary') or 'Permission request'}{RESET}", "", *[str(x) for x in request.get("details") or []], "", "Choices:"]
    lines.extend(f"  {BOLD}{c.get('key')}{RESET}) {c.get('label')}" for c in request.get("choices") or [])
    if feedback_input:
        lines.extend(["", f"feedback: {feedback_input}"])
    return render_panel("permission", "\n".join(lines), {"minBodyLines": 4})


charDisplayWidth = char_display_width
stringDisplayWidth = string_display_width
wrapPanelBodyLine = wrap_panel_body_line
renderPanel = render_panel
renderContextBadge = render_context_badge
renderStatusLine = render_status_line
renderBanner = render_banner
renderFooterBar = render_footer_bar
renderToolPanel = render_tool_panel
renderSlashMenu = render_slash_menu
renderPermissionPrompt = render_permission_prompt
getPermissionPromptMaxScrollOffset = get_permission_prompt_max_scroll_offset
