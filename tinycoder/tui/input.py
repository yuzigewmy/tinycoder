from __future__ import annotations

from .chrome import render_panel
from .theme import (
    ACCENT_STRONG,
    BOLD,
    MUTED,
    RESET,
    REVERSE,
    glyph,
    style,
)


def render_input_prompt(input_text: str, cursor_offset: int) -> str:
    offset = max(0, min(cursor_offset, len(input_text)))
    before = input_text[:offset]
    current = input_text[offset] if offset < len(input_text) else " "
    after = input_text[offset + 1 :] if offset < len(input_text) else ""
    placeholder = (
        ""
        if input_text
        else style("描述目标、粘贴错误，或输入 / 调用命令", MUTED)
    )
    cursor = f"{REVERSE}{current}{RESET}"
    prompt_line = (
        f"{style(glyph('›', '>'), ACCENT_STRONG, BOLD)} "
        f"{before}{cursor}{after}{placeholder}"
    )
    footer = style(
        "Enter 发送  ·  Tab 补全  ·  Esc 清空  ·  Ctrl+C 退出",
        MUTED,
    )
    return render_panel(
        "ASK TINYCODER",
        f"{prompt_line}\n{footer}",
    )


renderInputPrompt = render_input_prompt
