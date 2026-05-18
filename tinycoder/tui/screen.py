from __future__ import annotations

import sys

ENTER_ALT_SCREEN = "\u001b[?1049h"
EXIT_ALT_SCREEN = "\u001b[?1049l"
ERASE_SCREEN_AND_HOME = "\u001b[2J\u001b[H"
ENABLE_MOUSE_TRACKING = "\u001b[?1000h\u001b[?1002h\u001b[?1006h"
DISABLE_MOUSE_TRACKING = "\u001b[?1006l\u001b[?1002l\u001b[?1000l"


def _write(value: str) -> None:
    sys.stdout.write(value)
    sys.stdout.flush()


def hide_cursor() -> None:
    _write("\u001b[?25l")


def show_cursor() -> None:
    _write("\u001b[?25h")


def enter_alternate_screen() -> None:
    _write(DISABLE_MOUSE_TRACKING + ENTER_ALT_SCREEN + ERASE_SCREEN_AND_HOME + ENABLE_MOUSE_TRACKING)


def exit_alternate_screen() -> None:
    _write(DISABLE_MOUSE_TRACKING + EXIT_ALT_SCREEN)


def clear_screen() -> None:
    _write("\u001b[H\u001b[J")


hideCursor = hide_cursor
showCursor = show_cursor
enterAlternateScreen = enter_alternate_screen
exitAlternateScreen = exit_alternate_screen
clearScreen = clear_screen
