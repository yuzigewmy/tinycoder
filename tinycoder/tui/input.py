from __future__ import annotations

RESET = "\u001b[0m"
DIM = "\u001b[2m"
GREEN = "\u001b[32m"
YELLOW = "\u001b[33m"
BOLD = "\u001b[1m"
REVERSE = "\u001b[7m"


def render_input_prompt(input_text: str, cursor_offset: int) -> str:
    offset = max(0, min(cursor_offset, len(input_text)))
    before = input_text[:offset]
    current = input_text[offset] if offset < len(input_text) else " "
    after = input_text[offset + 1:] if offset < len(input_text) else ""
    placeholder = "" if input_text else " Ask for code, files, tasks, or MCP tools"
    return "\n".join([
        f"{YELLOW}{BOLD}prompt{RESET} {DIM}Enter send | /help commands | Esc clear | Ctrl+C exit{RESET}",
        "",
        f"{GREEN}{BOLD}tinycoder>{RESET} {before}{REVERSE}{current}{RESET}{after}{DIM}{placeholder}{RESET}",
    ])


renderInputPrompt = render_input_prompt
