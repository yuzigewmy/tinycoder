from __future__ import annotations

import re

RESET = "\u001b[0m"
DIM = "\u001b[2m"
CYAN = "\u001b[36m"
YELLOW = "\u001b[33m"
MAGENTA = "\u001b[35m"
BOLD = "\u001b[1m"


def render_markdownish(input_text: str) -> str:
    lines = input_text.split("\n")
    in_code = False
    rendered: list[str] = []
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            rendered.append(f"{DIM}{line}{RESET}")
            continue
        if in_code:
            rendered.append(f"{DIM}{line}{RESET}")
            continue
        if re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", line.strip()):
            rendered.append(f"{DIM}{line.replace('|', ' ').strip()}{RESET}")
            continue
        if re.match(r"^\|.*\|$", line.strip()):
            cells = [c.strip() for c in line.split("|") if c.strip()]
            rendered.append(f" {DIM}|{RESET} ".join(cells))
            continue
        if line.startswith("### "):
            rendered.append(f"{CYAN}{BOLD}{line[4:]}{RESET}")
            continue
        if line.startswith("## "):
            rendered.append(f"{CYAN}{BOLD}{line[3:]}{RESET}")
            continue
        if line.startswith("# "):
            rendered.append(f"{CYAN}{BOLD}{line[2:]}{RESET}")
            continue
        if line.startswith("> "):
            rendered.append(f"{DIM}{line}{RESET}")
            continue
        formatted = re.sub(r"^\s*[-*]\s+", f"{YELLOW}•{RESET} ", line)
        formatted = re.sub(r"`([^`]+)`", f"{MAGENTA}\\1{RESET}", formatted)
        formatted = re.sub(r"\*\*([^*]+)\*\*", f"{BOLD}\\1{RESET}", formatted)
        rendered.append(formatted)
    return "\n".join(rendered)


renderMarkdownish = render_markdownish
