from __future__ import annotations

from .theme import ACCENT, BOLD, DIM, INK, MUTED, PRIMARY, RAIL, REVERSE, paint


def render_inline_prompt() -> str:
    return f"{paint('tinycoder', PRIMARY, BOLD)} {paint('›', ACCENT, BOLD)} "


def render_input_prompt(input_text: str, cursor_offset: int) -> str:
    offset = max(0, min(cursor_offset, len(input_text)))
    before = input_text[:offset]
    current = input_text[offset] if offset < len(input_text) else " "
    after = input_text[offset + 1 :] if offset < len(input_text) else ""
    placeholder = "" if input_text else paint("描述改动、提出问题，或输入 /help", MUTED, DIM)
    cursor = paint(current, INK, REVERSE)
    return "\n".join(
        [
            f"{paint('  ╭─', RAIL)} {paint('输入', PRIMARY, BOLD)}  "
            f"{paint('Enter 发送 · Esc 清空 · Ctrl+C 退出', MUTED, DIM)}",
            f"{paint('  │', RAIL)}  {before}{cursor}{after}{placeholder}",
            paint("  ╰─", RAIL),
        ]
    )


renderInputPrompt = render_input_prompt
renderInlinePrompt = render_inline_prompt
