from __future__ import annotations

import os
import re
import sys
from typing import TextIO

# TinyCoder's "quiet workbench" palette. 256-colour ANSI keeps it attractive
# in modern terminals while remaining portable over SSH and on Windows Terminal.
RESET = "\u001b[0m"
BOLD = "\u001b[1m"
DIM = "\u001b[2m"
ITALIC = "\u001b[3m"
REVERSE = "\u001b[7m"

INK = "\u001b[38;5;253m"
MUTED = "\u001b[38;5;245m"
PRIMARY = "\u001b[38;5;75m"
PRIMARY_SOFT = "\u001b[38;5;110m"
ACCENT = "\u001b[38;5;179m"
SUCCESS = "\u001b[38;5;114m"
DANGER = "\u001b[38;5;203m"
TOOL = "\u001b[38;5;176m"
RAIL = "\u001b[38;5;240m"

ANSI_PATTERN = re.compile(r"\u001b\[[0-?]*[ -/]*[@-~]")


def color_enabled(stream: TextIO | None = None) -> bool:
    """Return whether TinyCoder chrome should emit ANSI colour codes."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    preference = os.environ.get("TINYCODER_COLOR", "auto").strip().lower()
    if preference in {"0", "false", "off", "no", "plain", "never"}:
        return False
    if preference in {"1", "true", "on", "yes", "ansi", "always"}:
        return True
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    target = stream or sys.stdout
    return bool(getattr(target, "isatty", lambda: False)())


def paint(text: object, *codes: str, color: bool | None = None) -> str:
    value = str(text)
    if not value or not (color_enabled() if color is None else color):
        return value
    return "".join(codes) + value + RESET


def strip_ansi(text: object) -> str:
    return ANSI_PATTERN.sub("", str(text))
