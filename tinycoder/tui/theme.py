from __future__ import annotations

import os
import sys
from typing import TextIO

RESET = "\u001b[0m"
BOLD = "\u001b[1m"
DIM = "\u001b[2m"
ITALIC = "\u001b[3m"
REVERSE = "\u001b[7m"

# Semantic tokens keep the interface coherent when individual surfaces evolve.
ACCENT = "\u001b[38;5;45m"
ACCENT_STRONG = "\u001b[38;5;51m"
ACCENT_SOFT = "\u001b[38;5;37m"
BORDER = "\u001b[38;5;31m"
BORDER_SOFT = "\u001b[38;5;24m"
MUTED = "\u001b[38;5;245m"
SUBTLE = "\u001b[38;5;240m"
SUCCESS = "\u001b[38;5;42m"
WARNING = "\u001b[38;5;214m"
DANGER = "\u001b[38;5;203m"
INFO = "\u001b[38;5;75m"
CODE = "\u001b[38;5;141m"
SELECTED_BG = "\u001b[48;5;24m"
SELECTED_FG = "\u001b[38;5;231m"

# Compatibility aliases for existing TUI imports and external callers.
CYAN = ACCENT
GREEN = SUCCESS
YELLOW = WARNING
RED = DANGER
BLUE = INFO
MAGENTA = CODE
BRIGHT_GREEN = SUCCESS
BRIGHT_RED = DANGER
BRIGHT_CYAN = ACCENT_STRONG
BRIGHT_YELLOW = WARNING


def color_enabled(stream: TextIO | None = None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    value = os.environ.get("TINYCODER_COLOR", "auto").strip().lower()
    if value in {"0", "false", "off", "no", "never", "plain"}:
        return False
    if value in {"1", "true", "on", "yes", "always", "ansi"}:
        return True
    target = stream or sys.stdout
    return bool(getattr(target, "isatty", lambda: False)())


def unicode_enabled(encoding: str | None = None) -> bool:
    value = os.environ.get("TINYCODER_UNICODE", "auto").strip().lower()
    if value in {"0", "false", "off", "no", "never", "ascii", "legacy"}:
        return False
    if value in {"1", "true", "on", "yes", "always", "unicode"}:
        return True
    target_encoding = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "╭─◆█●▲■□√×◇".encode(target_encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def glyph(modern: str, legacy: str, *, encoding: str | None = None) -> str:
    value = os.environ.get("TINYCODER_UNICODE", "auto").strip().lower()
    if value in {"1", "true", "on", "yes", "always", "unicode"}:
        return modern
    if not unicode_enabled(encoding):
        return legacy
    target_encoding = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        modern.encode(target_encoding)
        return modern
    except (LookupError, UnicodeEncodeError):
        return legacy


def style(text: str, *codes: str, enabled: bool | None = None) -> str:
    use_color = color_enabled() if enabled is None else enabled
    if not use_color or not text:
        return text
    return "".join(codes) + text + RESET
