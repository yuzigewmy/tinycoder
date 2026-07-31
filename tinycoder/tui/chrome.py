from __future__ import annotations

import os
import re
import shutil
from typing import Any

from . import theme as _theme
from .theme import (
    ACCENT,
    ACCENT_SOFT,
    ACCENT_STRONG,
    BOLD,
    BORDER,
    BORDER_SOFT,
    BRIGHT_RED,
    DANGER,
    INFO,
    MUTED,
    SELECTED_BG,
    SELECTED_FG,
    SUBTLE,
    SUCCESS,
    WARNING,
    glyph,
    style,
)

# Preserve the original chrome constants for callers that import them directly.
RESET = _theme.RESET
DIM = _theme.DIM
CYAN = _theme.CYAN
GREEN = _theme.GREEN
YELLOW = _theme.YELLOW
RED = _theme.RED
BLUE = _theme.BLUE
MAGENTA = _theme.MAGENTA
REVERSE = _theme.REVERSE
BRIGHT_GREEN = _theme.BRIGHT_GREEN
BRIGHT_CYAN = _theme.BRIGHT_CYAN
BRIGHT_YELLOW = _theme.BRIGHT_YELLOW
CODE = _theme.CODE

ANSI_PATTERN = re.compile(r"\u001b\[[0-?]*[ -/]*[@-~]")
MAX_CHROME_WIDTH = 104
MIN_CHROME_WIDTH = 20
FULL_HERO_WIDTH = 72
TWO_COLUMN_WIDTH = 84

_LOGO_FONT = {
    "T": ("11111", "  1  ", "  1  ", "  1  ", "  1  "),
    "I": ("11111", "  1  ", "  1  ", "  1  ", "11111"),
    "N": ("1   1", "11  1", "1 1 1", "1  11", "1   1"),
    "Y": ("1   1", " 1 1 ", "  1  ", "  1  ", "  1  "),
    "C": ("11111", "1    ", "1    ", "1    ", "11111"),
    "O": ("11111", "1   1", "1   1", "1   1", "11111"),
    "D": ("1111 ", "1   1", "1   1", "1   1", "1111 "),
    "E": ("11111", "1    ", "1111 ", "1    ", "11111"),
    "R": ("1111 ", "1   1", "1111 ", "1  1 ", "1   1"),
}


def strip_ansi(input_text: str) -> str:
    return ANSI_PATTERN.sub("", input_text)


def char_display_width(char: str) -> int:
    if not char:
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
        return input_text
    if width <= 3:
        return plain[:width]
    target = width - 3
    current = ""
    used = 0
    for ch in plain:
        char_width = char_display_width(ch)
        if used + char_width > target:
            break
        current += ch
        used += char_width
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
    left = ""
    used = 0
    for ch in input_text:
        char_width = char_display_width(ch)
        if used + char_width > left_target:
            break
        left += ch
        used += char_width
    right = ""
    used = 0
    for ch in reversed(input_text):
        char_width = char_display_width(ch)
        if used + char_width > right_target:
            break
        right = ch + right
        used += char_width
    return f"{left}...{right}"


def color_badge(label: str, value: str, color: str) -> str:
    return f"{style('[' + label + ']', color, BOLD)} {style(value, BOLD)}"


def _terminal_width() -> int:
    try:
        columns = shutil.get_terminal_size((100, 40)).columns
    except Exception:
        columns = 100
    return max(MIN_CHROME_WIDTH, min(MAX_CHROME_WIDTH, columns))


def _frame_glyphs() -> dict[str, str]:
    return {
        "top_left": glyph("╭", "+"),
        "top_right": glyph("╮", "+"),
        "bottom_left": glyph("╰", "+"),
        "bottom_right": glyph("╯", "+"),
        "mid_left": glyph("├", "+"),
        "mid_right": glyph("┤", "+"),
        "vertical": glyph("│", "|"),
        "horizontal": glyph("─", "-"),
    }


def border_line(
    kind: str,
    width: int,
    title: str | None = None,
    right_title: str | None = None,
    right_color: str = MUTED,
) -> str:
    frame = _frame_glyphs()
    if kind == "top":
        left, right = frame["top_left"], frame["top_right"]
    elif kind == "middle":
        left, right = frame["mid_left"], frame["mid_right"]
    else:
        left, right = frame["bottom_left"], frame["bottom_right"]
    horizontal = frame["horizontal"]
    title_text = f" {strip_ansi(title or '')} " if title else ""
    right_text = f" {strip_ansi(right_title or '')} " if right_title else ""
    available = max(0, width - 2)
    if string_display_width(title_text) + string_display_width(right_text) > available:
        title_text = f" {truncate_plain(title_text.strip(), max(0, available - string_display_width(right_text) - 2))} "
    fill = horizontal * max(
        0,
        available - string_display_width(title_text) - string_display_width(right_text),
    )
    if title_text and fill:
        fill = horizontal + fill[1:]
    return "".join(
        [
            style(left, BORDER),
            style(horizontal if title_text else "", BORDER),
            style(title_text, ACCENT, BOLD),
            style(fill[1:] if title_text and fill else fill, BORDER),
            style(right_text, right_color, BOLD),
            style(right, BORDER),
        ]
    )


def panel_row(left: str, width: int, right: str | None = None) -> str:
    frame = _frame_glyphs()
    inner = max(0, width - 4)
    right_text = right or ""
    right_width = string_display_width(right_text)
    left_text = left
    if string_display_width(left_text) + right_width + (1 if right_text else 0) > inner:
        left_text = truncate_plain(
            left_text,
            max(0, inner - right_width - (1 if right_text else 0)),
        )
    padding = " " * max(
        0,
        inner - string_display_width(left_text) - right_width,
    )
    return (
        f"{style(frame['vertical'], BORDER)} "
        f"{left_text}{padding}{right_text} "
        f"{style(frame['vertical'], BORDER)}"
    )


def empty_panel_row(width: int) -> str:
    return panel_row("", width)


def wrap_panel_body_line(line: str, width: int) -> list[str]:
    inner = max(0, width - 4)
    if inner <= 0:
        return [""]
    plain = strip_ansi(line)
    if string_display_width(plain) <= inner:
        return [line]
    parts: list[str] = []
    current = ""
    current_width = 0
    for ch in plain:
        char_width = char_display_width(ch)
        if current_width + char_width > inner:
            parts.append(current)
            current = ch
            current_width = char_width
        else:
            current += ch
            current_width += char_width
    if current:
        parts.append(current)
    return parts


def render_panel(
    title: str,
    body: str,
    options: dict[str, Any] | None = None,
) -> str:
    options = options or {}
    width = max(
        MIN_CHROME_WIDTH,
        min(int(options.get("width") or _terminal_width()), MAX_CHROME_WIDTH),
    )
    rendered: list[str] = []
    for line in body.split("\n") if body else []:
        rendered.extend(wrap_panel_body_line(line, width))
    min_lines = int(options.get("minBodyLines") or 0)
    while len(rendered) < min_lines:
        rendered.append("")
    return "\n".join(
        [
            border_line(
                "top",
                width,
                title,
                str(options.get("rightTitle") or "") or None,
                str(options.get("rightTitleColor") or MUTED),
            ),
            *[panel_row(line, width) for line in rendered],
            border_line("bottom", width),
        ]
    )


def _logo_rows() -> list[str]:
    block = glyph("█", "#")
    words = ["TINY", "CODER"]
    rows = [""] * 5
    for word_index, word in enumerate(words):
        if word_index:
            rows = [row + "   " for row in rows]
        for char_index, char in enumerate(word):
            if char_index:
                rows = [row + " " for row in rows]
            pattern = _LOGO_FONT[char]
            rows = [
                rows[row_index] + pattern[row_index].replace("1", block)
                for row_index in range(5)
            ]
    return rows


def _centered_row(text: str, width: int, *codes: str) -> str:
    inner = max(0, width - 4)
    clipped = truncate_plain(text, inner)
    left_padding = max(0, (inner - string_display_width(clipped)) // 2)
    right_padding = max(
        0,
        inner - left_padding - string_display_width(clipped),
    )
    return panel_row(
        " " * left_padding + style(clipped, *codes) + " " * right_padding,
        width,
    )


def _runtime_cell(label: str, value: str, value_color: str = "") -> str:
    return (
        style(f"{label:<10}", MUTED, BOLD)
        + style(value, value_color or ACCENT_STRONG, BOLD)
    )


def _permission_color(mode: str) -> str:
    if mode == "full_access":
        return DANGER
    if mode == "auto_approve":
        return SUCCESS
    return WARNING


def _workspace_name(cwd: str, width: int) -> str:
    normalized = str(cwd or "").rstrip("\\/").replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1] if normalized else ""
    return truncate_plain(name or str(cwd), width)


def render_context_badge(stats: dict[str, Any]) -> str:
    utilization = max(0.0, min(1.0, float(stats.get("utilization") or 0)))
    warning = str(stats.get("warningLevel") or "normal")
    percent = round(utilization * 100)
    color = {
        "normal": SUCCESS,
        "warning": WARNING,
        "critical": DANGER,
        "blocked": BRIGHT_RED,
    }.get(warning, SUCCESS)
    filled = max(0, min(10, round(utilization * 10)))
    full = glyph("■", "#")
    empty = glyph("□", "-")
    bar = full * filled + empty * (10 - filled)
    accounting = stats.get("accounting") or {}
    source = {
        "provider_usage": "usage",
        "provider_usage_plus_estimate": "usage+est",
        "estimate_only": "estimate",
    }.get(accounting.get("source"), "estimate")
    return (
        f"{style(bar, color)} "
        f"{style(str(percent) + '%', color, BOLD)} "
        f"{style(source, MUTED)}"
    )


def render_status_line(
    runtime: dict[str, Any],
    cwd: str,
    stats: dict[str, Any] | None = None,
) -> str:
    width = _terminal_width()
    model = str(runtime.get("model") or "mock")
    permission = str(runtime.get("permissionModeLabel") or "请求批准")
    mark = glyph("◆", "*")
    left = (
        f"{style(mark, ACCENT_STRONG)} "
        f"{style(truncate_plain(model, 28), BOLD)} "
        f"{style('·', BORDER_SOFT)} "
        f"{style(permission, _permission_color(str(runtime.get('permissionMode') or '')))}"
    )
    path_budget = max(8, min(32, width // 3))
    right_parts = [style(_workspace_name(cwd, path_budget), MUTED)]
    if stats:
        right_parts.append(render_context_badge(stats))
    right = f" {style('·', BORDER_SOFT)} ".join(right_parts)
    if string_display_width(left) + string_display_width(right) + 2 > width:
        right = truncate_plain(right, max(0, width - string_display_width(left) - 2))
    padding = " " * max(1, width - string_display_width(left) - string_display_width(right))
    return truncate_plain(left + padding + right, width)


def render_banner(
    runtime: dict[str, Any] | None = None,
    cwd: str | None = None,
) -> str:
    runtime = runtime or {}
    cwd = cwd or os.getcwd()
    width = _terminal_width()
    provider = str(runtime.get("provider") or "mock")
    model = str(runtime.get("model") or "mock")
    permission_mode = str(runtime.get("permissionMode") or "request_approval")
    permission_label = str(runtime.get("permissionModeLabel") or "请求批准")
    session_status = str(runtime.get("sessionStatus") or "READY")
    rows = [border_line("top", width)]

    if width >= FULL_HERO_WIDTH:
        rows.append(empty_panel_row(width))
        rows.extend(_centered_row(row, width, ACCENT_STRONG, BOLD) for row in _logo_rows())
        rows.append(
            _centered_row(
                "AGENTIC ENGINEERING TERMINAL",
                width,
                MUTED,
                BOLD,
            )
        )
        rows.append(empty_panel_row(width))
    else:
        lockup = (
            f"{style('TinyCoder', ACCENT_STRONG, BOLD)}  "
            f"{style('AGENTIC ENGINEERING', MUTED, BOLD)}"
        )
        rows.append(panel_row(lockup, width))

    rows.append(border_line("middle", width, "RUNTIME"))
    mode_color = _permission_color(permission_mode)
    if width >= TWO_COLUMN_WIDTH:
        rows.append(
            panel_row(
                _runtime_cell("MODEL", truncate_plain(model, 24)),
                width,
                _runtime_cell("PROVIDER", truncate_plain(provider, 18)),
            )
        )
        rows.append(
            panel_row(
                _runtime_cell("MODE", permission_label, mode_color),
                width,
                _runtime_cell("SESSION", session_status, SUCCESS),
            )
        )
    else:
        value_budget = max(8, width - 18)
        rows.extend(
            [
                panel_row(_runtime_cell("MODEL", truncate_plain(model, value_budget)), width),
                panel_row(_runtime_cell("PROVIDER", truncate_plain(provider, value_budget)), width),
                panel_row(_runtime_cell("MODE", truncate_plain(permission_label, value_budget), mode_color), width),
                panel_row(_runtime_cell("SESSION", truncate_plain(session_status, value_budget), SUCCESS), width),
            ]
        )
    rows.append(
        panel_row(
            _runtime_cell(
                "WORKSPACE",
                truncate_path_middle(cwd, max(8, width - 18)),
                INFO,
            ),
            width,
        )
    )
    if permission_mode == "full_access":
        warning = (
            f"{glyph('▲', '!')} HIGH RISK"
            f"  {glyph('·', '-')}  审批已关闭"
        )
        rows.append(panel_row(style(warning, DANGER, BOLD), width))

    rows.append(border_line("middle", width))
    ready = f"{style(glyph('●', '*') + ' READY', SUCCESS, BOLD)}  {style('Tell TinyCoder what to build', MUTED)}"
    commands = style("/help  /resume  /newchat", ACCENT_SOFT, BOLD)
    if width >= 64:
        rows.append(panel_row(ready, width, commands))
    else:
        rows.append(panel_row(ready, width))
        rows.append(panel_row(commands, width))
    rows.append(border_line("bottom", width))
    return "\n".join(rows)


def render_footer_bar(
    text: str = "Enter 发送  ·  Tab 补全  ·  ↑↓ 历史  ·  /help 命令",
) -> str:
    return style(text, MUTED)


def render_assistant_heading() -> str:
    mark = glyph("◆", "*")
    rail = glyph("────────────", "------------")
    return (
        f"{style(mark, ACCENT_STRONG)} "
        f"{style('TINYCODER', ACCENT_STRONG, BOLD)} "
        f"{style(rail, BORDER_SOFT)}"
    )


def render_section_header(title: str, subtitle: str | None = None) -> str:
    mark = glyph("◆", "*")
    result = f"{style(mark, ACCENT)} {style(title, BOLD)}"
    if subtitle:
        result += f"  {style(subtitle, MUTED)}"
    return result


def render_activity_line(
    tool_name: str,
    detail: str = "",
    status: str = "running",
) -> str:
    cleaned_detail = " ".join(str(detail or "").replace("\r", " ").replace("\n", " ").split())
    if status == "success":
        prefix = f"{glyph('└─', '+-')} {glyph('√', 'OK')}"
        state = style("DONE", SUCCESS, BOLD)
        prefix_color = SUCCESS
    elif status == "error":
        prefix = f"{glyph('└─', '+-')} {glyph('×', 'X')}"
        state = style("FAILED", DANGER, BOLD)
        prefix_color = DANGER
    else:
        prefix = glyph("◆", "*")
        state = style("RUNNING", WARNING, BOLD)
        prefix_color = ACCENT
    label = style("TOOL", MUTED, BOLD) if status == "running" else state
    parts = [
        style(prefix, prefix_color),
        label,
        style(str(tool_name or "unknown"), BOLD),
    ]
    if status == "running":
        parts.extend([style("·", BORDER_SOFT), state])
    if cleaned_detail:
        parts.extend([style("·", BORDER_SOFT), style(cleaned_detail, MUTED)])
    return "  ".join(parts)


def render_tool_panel(tool_name: str, body: str, status: str = "running") -> str:
    status_label = {
        "success": "DONE",
        "error": "FAILED",
        "running": "RUNNING",
    }.get(status, status.upper())
    return render_panel(
        f"TOOL · {tool_name}",
        body,
        {"rightTitle": status_label},
    )


def render_slash_menu(
    commands: list[dict[str, Any]],
    selected_index: int = 0,
) -> str:
    lines: list[str] = []
    for index, command in enumerate(commands):
        selected = index == selected_index
        marker = glyph("◆", ">") if selected else " "
        name = f"/{command.get('name') or ''}"
        description = str(command.get("description") or "")
        line = (
            f"{style(marker, ACCENT_STRONG if selected else MUTED)}  "
            f"{style(name.ljust(18), BOLD if selected else MUTED)}"
            f"{style(description, ACCENT if selected else MUTED)}"
        )
        if selected:
            line = style(line, SELECTED_BG, SELECTED_FG)
        lines.append(line)
    return render_panel(
        "COMMAND PALETTE",
        "\n".join(lines),
        {"rightTitle": "↑↓ SELECT · ENTER OPEN"},
    )


def _permission_window_size() -> int:
    try:
        return max(6, shutil.get_terminal_size((100, 40)).lines - 12)
    except Exception:
        return 20


def get_permission_prompt_max_scroll_offset(
    request: dict[str, Any],
    window_size: int | None = None,
) -> int:
    lines = [
        request.get("summary") or "",
        *[str(value) for value in request.get("details") or []],
        *[
            f"{choice.get('key')}) {choice.get('label')}"
            for choice in request.get("choices") or []
        ],
    ]
    return max(0, len(lines) - (window_size or _permission_window_size()))


def render_permission_prompt(
    request: dict[str, Any],
    scroll_offset: int = 0,
    feedback_input: str = "",
) -> str:
    risk = str(request.get("risk") or "medium").upper()
    risk_color = DANGER if risk in {"HIGH", "CRITICAL"} else WARNING
    body_lines = [
        style("ACTION", MUTED, BOLD),
        f"  {style(str(request.get('summary') or 'Permission request'), BOLD)}",
    ]
    details = [str(value) for value in request.get("details") or []]
    if details:
        body_lines.extend(["", style("DETAILS", MUTED, BOLD)])
        body_lines.extend(f"  {glyph('·', '-')} {value}" for value in details)
    body_lines.extend(["", style("CHOICES", MUTED, BOLD)])
    body_lines.extend(
        f"  {style('[' + str(choice.get('key')) + ']', ACCENT, BOLD)} {choice.get('label')}"
        for choice in request.get("choices") or []
    )
    if feedback_input:
        body_lines.extend(
            [
                "",
                style("FEEDBACK", MUTED, BOLD),
                f"  {feedback_input}",
            ]
        )
    window = _permission_window_size()
    offset = max(
        0,
        min(
            scroll_offset,
            max(0, len(body_lines) - window),
        ),
    )
    visible = body_lines[offset : offset + window]
    if offset:
        visible.insert(0, style(f"↑ {offset} lines above", MUTED))
    if offset + window < len(body_lines):
        visible.append(style("↓ more", MUTED))
    return render_panel(
        "权限审批 · PERMISSION REVIEW",
        "\n".join(visible),
        {
            "rightTitle": f"{risk} RISK",
            "rightTitleColor": risk_color,
        },
    )


def render_session_picker(
    sessions: list[dict[str, Any]],
    selected_index: int = 0,
) -> str:
    lines = [
        style("↑↓ 选择  ·  Enter 恢复  ·  Esc 取消", MUTED),
        "",
    ]
    for index, meta in enumerate(sessions):
        selected = index == selected_index
        marker = glyph("◆", ">") if selected else glyph("◇", " ")
        title = truncate_plain(
            str(meta.get("title") or "(untitled)"),
            max(12, _terminal_width() - 30),
        )
        count = f"{meta.get('messageCount') or 0} 条消息"
        title_line = (
            f"{style(marker, ACCENT_STRONG if selected else MUTED)}  "
            f"{style(title, BOLD if selected else MUTED)}"
        )
        padding = max(
            1,
            _terminal_width()
            - 8
            - string_display_width(title_line)
            - string_display_width(count),
        )
        lines.append(title_line + " " * padding + style(count, MUTED))
        lines.append(f"   {style(str(meta.get('id') or ''), SUBTLE)}")
        if index < len(sessions) - 1:
            lines.append("")
    return render_panel(
        "会话恢复 · RESUME SESSION",
        "\n".join(lines),
        {"rightTitle": f"{len(sessions)} SESSIONS"},
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
