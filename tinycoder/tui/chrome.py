from __future__ import annotations

import os
import shutil
import unicodedata
from pathlib import Path
from typing import Any

from .theme import (
    ACCENT,
    BOLD,
    DANGER,
    DIM,
    INK,
    MUTED,
    PRIMARY,
    PRIMARY_SOFT,
    RAIL,
    REVERSE,
    SUCCESS,
    TOOL,
    paint,
    strip_ansi,
)


def char_display_width(char: str) -> int:
    if not char or unicodedata.combining(char):
        return 0
    code = ord(char)
    if code >= 0x1100 and (
        code <= 0x115F
        or code in (0x2329, 0x232A)
        or (0x2E80 <= code <= 0xA4CF and code != 0x303F)
        or (0xAC00 <= code <= 0xD7A3)
        or (0xF900 <= code <= 0xFAFF)
        or (0xFE10 <= code <= 0xFE19)
        or (0xFE30 <= code <= 0xFE6F)
        or (0xFF00 <= code <= 0xFF60)
        or (0xFFE0 <= code <= 0xFFE6)
        or (0x1F300 <= code <= 0x1FAF6)
        or (0x20000 <= code <= 0x3FFFD)
    ):
        return 2
    return 1


def string_display_width(input_text: str) -> int:
    return sum(char_display_width(ch) for ch in strip_ansi(input_text))


def truncate_plain(input_text: str, width: int) -> str:
    if width <= 0:
        return ""
    plain = strip_ansi(input_text)
    if string_display_width(plain) <= width:
        return plain
    if width <= 3:
        return plain[:width]
    target = width - 1
    current = ""
    used = 0
    for ch in plain:
        char_width = char_display_width(ch)
        if used + char_width > target:
            break
        current += ch
        used += char_width
    return current + "…"


def pad_plain(input_text: str, width: int) -> str:
    visible = string_display_width(input_text)
    return input_text if visible >= width else input_text + " " * (width - visible)


def truncate_path_middle(input_text: str, width: int) -> str:
    plain = strip_ansi(input_text)
    if width <= 0 or string_display_width(plain) <= width:
        return plain
    if width <= 5:
        return truncate_plain(plain, width)
    keep = width - 1
    left_target = (keep + 1) // 2
    right_target = keep // 2
    left = ""
    used = 0
    for ch in plain:
        char_width = char_display_width(ch)
        if used + char_width > left_target:
            break
        left += ch
        used += char_width
    right = ""
    used = 0
    for ch in reversed(plain):
        char_width = char_display_width(ch)
        if used + char_width > right_target:
            break
        right = ch + right
        used += char_width
    return f"{left}…{right}"


def color_badge(label: str, value: str, color: str) -> str:
    return f"{paint(label, color, BOLD)} {paint(value, INK, BOLD)}"


def _terminal_width() -> int:
    try:
        return max(36, shutil.get_terminal_size((96, 40)).columns)
    except Exception:
        return 96


def get_work_area_width() -> int:
    """Keep reading measure calm on wide screens without breaking narrow TTYs."""
    return min(_terminal_width(), 96)


def _labeled_border(
    kind: str,
    width: int,
    title: str = "",
    right_title: str = "",
) -> str:
    top = kind == "top"
    left_corner, right_corner = ("╭", "╮") if top else ("╰", "╯")
    title_text = f"─ {title} " if title else ""
    right_text = f" {right_title} ─" if right_title else ""
    fill = max(1, width - 2 - string_display_width(title_text) - string_display_width(right_text))
    pieces = [paint(left_corner, RAIL)]
    if title_text:
        pieces.extend(
            [
                paint("─ ", RAIL),
                paint(title, PRIMARY if top else MUTED, BOLD if top else DIM),
                paint(" ", RAIL),
            ]
        )
    pieces.append(paint("─" * fill, RAIL))
    if right_text:
        pieces.extend(
            [
                paint(" ", RAIL),
                paint(right_title, MUTED, DIM),
                paint(" ─", RAIL),
            ]
        )
    pieces.append(paint(right_corner, RAIL))
    return "".join(pieces)


def border_line(kind: str, width: int) -> str:
    return _labeled_border(kind, width)


def panel_row(left: str, width: int, right: str | None = None) -> str:
    inner = max(0, width - 4)
    right_text = right or ""
    right_width = string_display_width(right_text)
    left_text = left
    if string_display_width(left_text) + right_width + (1 if right_text else 0) > inner:
        left_text = truncate_plain(left_text, max(0, inner - right_width - (1 if right_text else 0)))
    padding = " " * max(0, inner - string_display_width(left_text) - right_width)
    return f"{paint('│', RAIL)} {left_text}{padding}{right_text} {paint('│', RAIL)}"


def empty_panel_row(width: int) -> str:
    return f"{paint('│', RAIL)}{' ' * max(0, width - 2)}{paint('│', RAIL)}"


def wrap_panel_body_line(line: str, width: int) -> list[str]:
    inner = max(1, width - 4)
    if string_display_width(line) <= inner:
        return [line]
    # Styled long lines are safely flattened before wrapping; each UI label is
    # short enough to retain its colour and long payloads favour legibility.
    plain = strip_ansi(line)
    parts: list[str] = []
    current = ""
    current_width = 0
    last_space = -1
    for char in plain:
        char_width = char_display_width(char)
        if current_width + char_width > inner:
            if last_space >= 0:
                parts.append(current[:last_space].rstrip())
                current = current[last_space + 1 :].lstrip()
            else:
                parts.append(current)
                current = ""
            current_width = string_display_width(current)
            last_space = current.rfind(" ")
        current += char
        current_width += char_width
        if char.isspace():
            last_space = len(current) - 1
    if current or not parts:
        parts.append(current)
    return parts


def render_panel(title: str, body: str, options: dict[str, Any] | None = None) -> str:
    options = options or {}
    width = max(36, int(options.get("width") or get_work_area_width()))
    body_lines = body.split("\n") if body else []
    rendered: list[str] = []
    for line in body_lines:
        rendered.extend(wrap_panel_body_line(line, width))
    min_lines = int(options.get("minBodyLines") or 0)
    while len(rendered) < min_lines:
        rendered.append("")
    if options.get("padding", True):
        rendered = ["", *rendered, ""]
    return "\n".join(
        [
            _labeled_border("top", width, title, str(options.get("rightTitle") or "")),
            *[panel_row(line, width) for line in rendered],
            _labeled_border("bottom", width, "", str(options.get("bottomTitle") or "")),
        ]
    )


def render_context_badge(stats: dict[str, Any]) -> str:
    utilization = float(stats.get("utilization") or 0)
    warning = stats.get("warningLevel") or "normal"
    percent = round(utilization * 100)
    color = {
        "normal": SUCCESS,
        "warning": ACCENT,
        "critical": DANGER,
        "blocked": DANGER,
    }.get(str(warning), SUCCESS)
    filled = max(0, min(8, round(utilization * 8)))
    bar = "━" * filled + "╌" * (8 - filled)
    accounting = stats.get("accounting") or {}
    source = {
        "provider_usage": "usage",
        "provider_usage_plus_estimate": "usage+est",
        "estimate_only": "estimate",
    }.get(accounting.get("source"), "estimate")
    return f"{paint(bar, color)} {percent}% {paint(source, MUTED, DIM)}"


def _display_path(cwd: str) -> str:
    try:
        home = str(Path.home())
        return "~" + cwd[len(home) :] if cwd == home or cwd.startswith(home + os.sep) else cwd
    except Exception:
        return cwd


def render_status_line(
    runtime: dict[str, Any],
    cwd: str,
    stats: dict[str, Any] | None = None,
) -> str:
    provider = str(runtime.get("provider") or "mock")
    model = str(runtime.get("model") or "mock")
    left = f"{paint('◆', ACCENT)} {paint(provider, PRIMARY, BOLD)} {paint('/', MUTED)} {paint(model, INK)}"
    right_parts = [truncate_path_middle(_display_path(cwd), 38)]
    if stats:
        right_parts.append(render_context_badge(stats))
    return f"{left}  {paint('  '.join(right_parts), MUTED)}"


def render_banner(runtime: dict[str, Any] | None = None, cwd: str | None = None) -> str:
    runtime = runtime or {}
    cwd = cwd or os.getcwd()
    provider = str(runtime.get("provider") or "mock")
    model = str(runtime.get("model") or "mock")
    path_width = max(18, get_work_area_width() - 10)
    body = "\n".join(
        [
            paint("读懂仓库，完成改动，验证结果。", INK, BOLD),
            paint("一张专注于代码的安静工作台。", MUTED),
            "",
            f"{paint('◆', ACCENT)} {paint(provider, PRIMARY, BOLD)} {paint('/', MUTED)} {paint(model, INK)}",
            f"{paint('⌁', PRIMARY_SOFT)} {paint(truncate_path_middle(_display_path(cwd), path_width), MUTED)}",
        ]
    )
    return render_panel(
        "tinycoder",
        body,
        {
            "rightTitle": "本地编程助手",
            "bottomTitle": "理解 · 修改 · 验证",
        },
    )


def render_footer_bar(text: str = "Enter 发送 · /help 命令 · Ctrl+C 退出") -> str:
    return f"{paint('  ─', RAIL)} {paint(text, MUTED, DIM)}"


def render_activity(
    kind: str,
    title: str,
    detail: str = "",
    *,
    status: str = "running",
) -> str:
    color = DANGER if status == "error" else SUCCESS if status == "success" else ACCENT
    glyph = "×" if status == "error" else "✓" if status == "success" else "◆"
    label_color = TOOL if kind == "tool" else PRIMARY_SOFT
    display_kind = {"tool": "工具", "thinking": "思考"}.get(kind, kind)
    detail_text = f"  {paint(detail, MUTED, DIM)}" if detail else ""
    return (
        f"{paint('  ├─', RAIL)} {paint(glyph, color, BOLD)} "
        f"{paint(display_kind, label_color, BOLD)}  {paint(title, INK)}{detail_text}"
    )


def render_response_header(label: str = "tinycoder") -> str:
    return f"{paint('  ╭─', RAIL)} {paint(label, PRIMARY, BOLD)}"


def render_response_line(line: str = "") -> str:
    return f"{paint('  │', RAIL)}  {line}" if line else paint("  │", RAIL)


def render_response_footer() -> str:
    return paint("  ╰─", RAIL)


def render_response(body: str, label: str = "tinycoder") -> str:
    lines = body.split("\n") if body else [""]
    return "\n".join(
        [
            render_response_header(label),
            *[render_response_line(line) for line in lines],
            render_response_footer(),
        ]
    )


def render_tool_panel(tool_name: str, body: str, status: str = "running") -> str:
    label = "done" if status == "success" else "failed" if status == "error" else "running"
    return render_panel(
        f"tool · {tool_name}",
        body,
        {"rightTitle": paint(label, SUCCESS if status == "success" else DANGER if status == "error" else ACCENT)},
    )


def render_slash_menu(commands: list[dict[str, Any]], selected_index: int = 0) -> str:
    lines = []
    for index, command in enumerate(commands):
        selected = index == selected_index
        marker = paint("›", PRIMARY, BOLD) if selected else " "
        name = paint(f"/{command.get('name')}", INK, BOLD if selected else DIM)
        description = paint(command.get("description", ""), MUTED)
        line = f"{marker} {name:<20} {description}"
        lines.append(paint(line, REVERSE) if selected else line)
    return render_panel("commands", "\n".join(lines), {"bottomTitle": "↑↓ move · enter select · esc close"})


def _permission_window_size() -> int:
    try:
        return max(6, shutil.get_terminal_size((96, 40)).lines - 10)
    except Exception:
        return 20


def get_permission_prompt_max_scroll_offset(
    request: dict[str, Any],
    window_size: int | None = None,
) -> int:
    lines = [
        request.get("summary") or "",
        *[str(item) for item in request.get("details") or []],
        *[f"{choice.get('key')}) {choice.get('label')}" for choice in request.get("choices") or []],
    ]
    return max(0, len(lines) - (window_size or _permission_window_size()))


def render_permission_prompt(
    request: dict[str, Any],
    scroll_offset: int = 0,
    feedback_input: str = "",
) -> str:
    summary = str(request.get("summary") or "请在执行前确认此操作")
    lines = [paint(summary, INK, BOLD), ""]
    lines.extend(f"{paint('│', ACCENT)} {item}" for item in request.get("details") or [])
    lines.extend(["", paint("选择操作", MUTED, DIM)])
    for choice in request.get("choices") or []:
        key = paint(f" {choice.get('key')} ", PRIMARY, BOLD)
        lines.append(f"  {key}  {choice.get('label')}")
    if feedback_input:
        lines.extend(["", f"{paint('feedback', MUTED)}  {feedback_input}"])
    return render_panel(
        "需要授权",
        "\n".join(lines),
        {"rightTitle": "执行前请确认", "minBodyLines": 4},
    )


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
