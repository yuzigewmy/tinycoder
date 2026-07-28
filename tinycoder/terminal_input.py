from __future__ import annotations

import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Any, TextIO

from .line_editor import LineEditor, text_display_width

ENABLE_MOUSE_TRACKING = "\x1b[?1000h\x1b[?1006h"
DISABLE_MOUSE_TRACKING = "\x1b[?1006l\x1b[?1000l"
_CURSOR_POSITION_RE = re.compile(r"\x1b\[(\d+);(\d+)R")


@dataclass(frozen=True)
class TerminalGeometry:
    origin_x: int
    origin_y: int | None
    columns: int
    rows: int


def terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size((80, 24))
    return max(1, size.columns), max(1, size.lines)


def query_posix_cursor_position(
    stdin: TextIO,
    stdout: TextIO,
    *,
    timeout: float = 0.12,
) -> tuple[int, int] | None:
    import select

    stdout.write("\x1b[6n")
    stdout.flush()
    response = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([stdin], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            break
        response += stdin.read(1)
        match = _CURSOR_POSITION_RE.search(response)
        if match:
            return int(match.group(2)) - 1, int(match.group(1)) - 1
        if len(response) >= 32:
            break
    return None


class PromptRenderer:
    def __init__(
        self,
        prompt: str,
        geometry: TerminalGeometry,
        *,
        output: TextIO | None = None,
    ) -> None:
        self.prompt = prompt
        self.prompt_width = text_display_width(prompt)
        self.origin_x = max(0, geometry.origin_x)
        self.origin_y = geometry.origin_y
        self.columns = max(1, geometry.columns)
        self.rows = max(1, geometry.rows)
        self.output = output or sys.stdout
        self._rendered_cells = 0

    def update_size(self, columns: int, rows: int) -> None:
        self.columns = max(1, columns)
        self.rows = max(1, rows)

    def _write_cursor_position(self, x: int, y: int) -> None:
        self.output.write(f"\x1b[{y + 1};{x + 1}H")

    def _ensure_capacity(self, cells: int) -> None:
        if self.origin_y is None:
            return
        last_relative_row = (self.origin_x + max(0, cells - 1)) // self.columns
        overflow = self.origin_y + last_relative_row - (self.rows - 1)
        if overflow <= 0:
            return
        self._write_cursor_position(0, self.rows - 1)
        self.output.write("\n" * overflow)
        self.origin_y = max(0, self.origin_y - overflow)

    def _write_absolute_offset(self, offset: int) -> None:
        if self.origin_y is None:
            return
        row_offset, column = divmod(self.origin_x + max(0, offset), self.columns)
        self._write_cursor_position(column, self.origin_y + row_offset)

    def redraw(self, editor: LineEditor) -> None:
        text_width = text_display_width(editor.text)
        visible_cells = self.prompt_width + text_width
        cells_to_touch = max(self._rendered_cells, visible_cells)
        self._ensure_capacity(cells_to_touch)

        if self.origin_y is None:
            self.output.write(f"\r{self.prompt}{editor.text}\x1b[K")
            suffix_width = text_display_width(editor.text[int(editor.cursor) :])
            if suffix_width:
                self.output.write(f"\x1b[{suffix_width}D")
        else:
            self._write_absolute_offset(0)
            self.output.write(self.prompt + editor.text)
            if cells_to_touch > visible_cells:
                self.output.write(" " * (cells_to_touch - visible_cells))
            self._write_absolute_offset(self.prompt_width + editor.cursor_display_offset())

        self._rendered_cells = visible_cells
        self.output.flush()

    def finish(self, editor: LineEditor) -> None:
        if self.origin_y is None:
            self.output.write(f"\r{self.prompt}{editor.text}\x1b[K\n")
        else:
            self._write_absolute_offset(self.prompt_width + text_display_width(editor.text))
            self.output.write("\x1b[K\n")
        self.output.flush()


class WindowsConsoleInput:
    KEY_EVENT = 0x0001
    MOUSE_EVENT = 0x0002
    WINDOW_BUFFER_SIZE_EVENT = 0x0004

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows console input is only available on Windows")
        self._kernel32: Any = None
        self._input_handle: Any = None
        self._output_handle: Any = None
        self._old_input_mode: int | None = None
        self._old_output_mode: int | None = None
        self._types: dict[str, Any] = {}

    def __enter__(self) -> WindowsConsoleInput:
        import ctypes
        from ctypes import wintypes

        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left", wintypes.SHORT),
                ("Top", wintypes.SHORT),
                ("Right", wintypes.SHORT),
                ("Bottom", wintypes.SHORT),
            ]

        class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
            _fields_ = [
                ("dwSize", COORD),
                ("dwCursorPosition", COORD),
                ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD),
            ]

        class CHAR_UNION(ctypes.Union):
            _fields_ = [("UnicodeChar", wintypes.WCHAR), ("AsciiChar", wintypes.CHAR)]

        class KEY_EVENT_RECORD(ctypes.Structure):
            _anonymous_ = ("uChar",)
            _fields_ = [
                ("bKeyDown", wintypes.BOOL),
                ("wRepeatCount", wintypes.WORD),
                ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD),
                ("uChar", CHAR_UNION),
                ("dwControlKeyState", wintypes.DWORD),
            ]

        class MOUSE_EVENT_RECORD(ctypes.Structure):
            _fields_ = [
                ("dwMousePosition", COORD),
                ("dwButtonState", wintypes.DWORD),
                ("dwControlKeyState", wintypes.DWORD),
                ("dwEventFlags", wintypes.DWORD),
            ]

        class EVENT_UNION(ctypes.Union):
            _fields_ = [
                ("KeyEvent", KEY_EVENT_RECORD),
                ("MouseEvent", MOUSE_EVENT_RECORD),
                ("WindowBufferSizeEvent", COORD),
            ]

        class INPUT_RECORD(ctypes.Structure):
            _anonymous_ = ("Event",)
            _fields_ = [("EventType", wintypes.WORD), ("Event", EVENT_UNION)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL
        kernel32.GetConsoleScreenBufferInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO),
        ]
        kernel32.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
        kernel32.ReadConsoleInputW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(INPUT_RECORD),
            wintypes.DWORD,
            wintypes.LPDWORD,
        ]
        kernel32.ReadConsoleInputW.restype = wintypes.BOOL
        input_handle = kernel32.GetStdHandle(0xFFFFFFF6)
        output_handle = kernel32.GetStdHandle(0xFFFFFFF5)
        invalid_handle = ctypes.c_void_p(-1).value
        if input_handle in {None, invalid_handle} or output_handle in {None, invalid_handle}:
            raise OSError("console handles are unavailable")

        old_input_mode = wintypes.DWORD()
        old_output_mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(input_handle, ctypes.byref(old_input_mode)):
            raise OSError(ctypes.get_last_error(), "stdin is not a Windows console")
        if not kernel32.GetConsoleMode(output_handle, ctypes.byref(old_output_mode)):
            raise OSError(ctypes.get_last_error(), "stdout is not a Windows console")

        enable_extended_flags = 0x0080
        enable_quick_edit_mode = 0x0040
        enable_window_input = 0x0008
        enable_mouse_input = 0x0010
        enable_virtual_terminal_processing = 0x0004
        input_mode = (
            old_input_mode.value
            | enable_extended_flags
            | enable_window_input
            | enable_mouse_input
        ) & ~enable_quick_edit_mode
        if not kernel32.SetConsoleMode(input_handle, input_mode):
            raise OSError(ctypes.get_last_error(), "could not enable console mouse input")
        if not kernel32.SetConsoleMode(
            output_handle,
            old_output_mode.value | enable_virtual_terminal_processing,
        ):
            kernel32.SetConsoleMode(input_handle, old_input_mode.value)
            raise OSError(ctypes.get_last_error(), "could not enable terminal rendering")

        self._kernel32 = kernel32
        self._input_handle = input_handle
        self._output_handle = output_handle
        self._old_input_mode = old_input_mode.value
        self._old_output_mode = old_output_mode.value
        self._types = {
            "ctypes": ctypes,
            "DWORD": wintypes.DWORD,
            "CSBI": CONSOLE_SCREEN_BUFFER_INFO,
            "INPUT_RECORD": INPUT_RECORD,
        }
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._kernel32 is None:
            return
        if self._old_input_mode is not None:
            self._kernel32.SetConsoleMode(self._input_handle, self._old_input_mode)
        if self._old_output_mode is not None:
            self._kernel32.SetConsoleMode(self._output_handle, self._old_output_mode)

    def geometry(self) -> TerminalGeometry:
        ctypes = self._types["ctypes"]
        info = self._types["CSBI"]()
        if not self._kernel32.GetConsoleScreenBufferInfo(
            self._output_handle,
            ctypes.byref(info),
        ):
            columns, rows = terminal_size()
            return TerminalGeometry(0, None, columns, rows)
        window = info.srWindow
        return TerminalGeometry(
            origin_x=max(0, info.dwCursorPosition.X - window.Left),
            origin_y=max(0, info.dwCursorPosition.Y - window.Top),
            columns=max(1, window.Right - window.Left + 1),
            rows=max(1, window.Bottom - window.Top + 1),
        )

    def _viewport_mouse_position(self, x: int, y: int) -> tuple[int, int]:
        ctypes = self._types["ctypes"]
        info = self._types["CSBI"]()
        if not self._kernel32.GetConsoleScreenBufferInfo(
            self._output_handle,
            ctypes.byref(info),
        ):
            return x, y
        return x - info.srWindow.Left, y - info.srWindow.Top

    def read_event(self) -> dict[str, Any]:
        ctypes = self._types["ctypes"]
        record = self._types["INPUT_RECORD"]()
        count = self._types["DWORD"]()
        key_names = {
            0x08: "backspace",
            0x09: "tab",
            0x0D: "return",
            0x23: "end",
            0x24: "home",
            0x25: "left",
            0x26: "up",
            0x27: "right",
            0x28: "down",
            0x2E: "delete",
        }
        while True:
            if not self._kernel32.ReadConsoleInputW(
                self._input_handle,
                ctypes.byref(record),
                1,
                ctypes.byref(count),
            ):
                raise OSError(ctypes.get_last_error(), "could not read console input")
            if record.EventType == self.KEY_EVENT:
                key = record.KeyEvent
                if not key.bKeyDown:
                    continue
                repeat = max(1, int(key.wRepeatCount))
                char = key.UnicodeChar
                if char == "\x03":
                    return {"kind": "key", "name": "interrupt", "repeat": repeat}
                name = key_names.get(int(key.wVirtualKeyCode))
                if name:
                    return {"kind": "key", "name": name, "repeat": repeat}
                if char and char >= " ":
                    return {"kind": "text", "text": char * repeat}
                continue
            if record.EventType == self.MOUSE_EVENT:
                mouse = record.MouseEvent
                if mouse.dwEventFlags == 0 and mouse.dwButtonState & 0x0001:
                    x, y = self._viewport_mouse_position(
                        int(mouse.dwMousePosition.X),
                        int(mouse.dwMousePosition.Y),
                    )
                    return {
                        "kind": "mouse",
                        "x": x,
                        "y": y,
                        "button": "left",
                        "action": "press",
                    }
                continue
            if record.EventType == self.WINDOW_BUFFER_SIZE_EVENT:
                geometry = self.geometry()
                return {
                    "kind": "resize",
                    "columns": geometry.columns,
                    "rows": geometry.rows,
                }
