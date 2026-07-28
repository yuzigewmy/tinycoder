from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def char_display_width(char: str) -> int:
    if not char or char in {"\r", "\n"}:
        return 0
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def text_display_width(text: str) -> int:
    plain_text = _ANSI_ESCAPE_RE.sub("", text)
    return sum(char_display_width(char) for char in plain_text)


def _index_for_display_offset(text: str, offset: int) -> int:
    target = max(0, offset)
    used = 0
    for index, char in enumerate(text):
        width = char_display_width(char)
        next_used = used + width
        if target < next_used:
            return index if (target - used) * 2 < width else index + 1
        if target == next_used:
            return index + 1
        used = next_used
    return len(text)


@dataclass
class LineEditor:
    text: str = ""
    cursor: int | None = None
    _preferred_column: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.cursor is None:
            self.cursor = len(self.text)
        self.cursor = max(0, min(int(self.cursor), len(self.text)))

    def _reset_vertical_column(self) -> None:
        self._preferred_column = None

    def replace(self, text: str) -> None:
        self.text = text
        self.cursor = len(text)
        self._reset_vertical_column()

    def insert(self, value: str) -> None:
        if not value:
            return
        cursor = int(self.cursor)
        self.text = self.text[:cursor] + value + self.text[cursor:]
        self.cursor = cursor + len(value)
        self._reset_vertical_column()

    def backspace(self) -> bool:
        cursor = int(self.cursor)
        if cursor <= 0:
            return False
        self.text = self.text[: cursor - 1] + self.text[cursor:]
        self.cursor = cursor - 1
        self._reset_vertical_column()
        return True

    def delete(self) -> bool:
        cursor = int(self.cursor)
        if cursor >= len(self.text):
            return False
        self.text = self.text[:cursor] + self.text[cursor + 1 :]
        self._reset_vertical_column()
        return True

    def move_left(self) -> bool:
        cursor = int(self.cursor)
        if cursor <= 0:
            return False
        self.cursor = cursor - 1
        self._reset_vertical_column()
        return True

    def move_right(self) -> bool:
        cursor = int(self.cursor)
        if cursor >= len(self.text):
            return False
        self.cursor = cursor + 1
        self._reset_vertical_column()
        return True

    def move_home(self) -> bool:
        if self.cursor == 0:
            return False
        self.cursor = 0
        self._reset_vertical_column()
        return True

    def move_end(self) -> bool:
        if self.cursor == len(self.text):
            return False
        self.cursor = len(self.text)
        self._reset_vertical_column()
        return True

    def cursor_display_offset(self) -> int:
        return text_display_width(self.text[: int(self.cursor)])

    def move_vertical(
        self,
        direction: int,
        *,
        terminal_columns: int,
        prompt_width: int,
        origin_x: int,
    ) -> bool:
        if direction not in {-1, 1}:
            raise ValueError("direction must be -1 or 1")
        columns = max(1, terminal_columns)
        text_start = max(0, origin_x) + max(0, prompt_width)
        current_cell = text_start + self.cursor_display_offset()
        current_row, current_column = divmod(current_cell, columns)
        first_row = text_start // columns
        last_row = (text_start + text_display_width(self.text)) // columns
        target_row = current_row + direction
        if target_row < first_row or target_row > last_row:
            return False

        if self._preferred_column is None:
            self._preferred_column = current_column
        target_cell = target_row * columns + self._preferred_column
        row_start = target_row * columns
        row_end = row_start + columns - 1
        text_end = text_start + text_display_width(self.text)
        target_cell = max(row_start, min(target_cell, row_end, text_end))
        target_cell = max(text_start, target_cell)
        next_cursor = _index_for_display_offset(self.text, target_cell - text_start)
        if next_cursor == self.cursor:
            return False
        self.cursor = next_cursor
        return True

    def move_to_screen_position(
        self,
        *,
        x: int,
        y: int,
        origin_x: int,
        origin_y: int,
        terminal_columns: int,
        prompt_width: int,
    ) -> bool:
        columns = max(1, terminal_columns)
        text_start = origin_y * columns + origin_x + max(0, prompt_width)
        text_end = text_start + text_display_width(self.text)
        click_cell = y * columns + x
        if click_cell < text_start:
            return False
        if click_cell > text_end and click_cell // columns != text_end // columns:
            return False

        next_cursor = _index_for_display_offset(
            self.text,
            min(click_cell, text_end) - text_start,
        )
        changed = next_cursor != self.cursor
        self.cursor = next_cursor
        self._reset_vertical_column()
        return changed
