from __future__ import annotations

import re
from typing import Any

ESC = "\u001b"
CTRL_CHAR_TO_NAME = {
    "\u0001": "a",
    "\u0003": "c",
    "\u0005": "e",
    "\u000e": "n",
    "\u000f": "o",
    "\u0010": "p",
    "\u0015": "u",
}


def is_multiline_paste_chunk(input_text: str) -> bool:
    return bool(re.search(r"[\r\n]", input_text) and re.search(r"[^\r\n]", input_text))


def maybe_need_more_for_escape_sequence(input_text: str) -> bool:
    if not input_text.startswith(ESC):
        return False
    if input_text == ESC or input_text == "\u001b[":
        return True
    if re.match(r"^\u001b\[[<\d;?]*$", input_text):
        return True
    if input_text.startswith("\u001bO") and len(input_text) < 3:
        return True
    return False


def parse_escape_sequence(input_text: str) -> dict[str, Any] | None:
    match = re.match(r"^\u001b\[<(\d+);(\d+);(\d+)([Mm])", input_text)
    if match:
        button = int(match.group(1)); x = int(match.group(2)) - 1; y = int(match.group(3)) - 1; released = match.group(4) == "m"
        length = len(match.group(0))
        if (button & 0x43) == 0x40:
            return {"event": {"kind": "wheel", "direction": "up"}, "length": length}
        if (button & 0x43) == 0x41:
            return {"event": {"kind": "wheel", "direction": "down"}, "length": length}
        btn_code = button & 0x43
        is_drag = (button & 0x20) != 0
        button_name = "left" if btn_code == 0 else "middle" if btn_code == 1 else "right"
        return {"event": {"kind": "mouse", "x": x, "y": y, "button": button_name, "action": "release" if released else "drag" if is_drag else "press"}, "length": length}

    if re.match(r"^\u001b\[M...", input_text):
        seq = input_text[:6]
        button = ord(seq[3]) - 32
        if (button & 0x43) == 0x40:
            return {"event": {"kind": "wheel", "direction": "up"}, "length": 6}
        if (button & 0x43) == 0x41:
            return {"event": {"kind": "wheel", "direction": "down"}, "length": 6}
        return {"event": None, "length": 6}

    match = re.match(r"^\u001b\[(?:1;(\d+))?([ABCDHF])", input_text)
    if match:
        modifier = int(match.group(1) or "1")
        name_map = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
        return {"event": {"kind": "key", "name": name_map[match.group(2)], "ctrl": modifier == 5, "meta": modifier == 3}, "length": len(match.group(0))}

    match = re.match(r"^\u001b\[(\d+)~", input_text)
    if match:
        name_map = {"1": "home", "3": "delete", "4": "end", "5": "pageup", "6": "pagedown", "7": "home", "8": "end"}
        name = name_map.get(match.group(1))
        return {"event": {"kind": "key", "name": name, "ctrl": False, "meta": False} if name else None, "length": len(match.group(0))}

    match = re.match(r"^\u001bO([ABCDHF])", input_text)
    if match:
        name_map = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
        return {"event": {"kind": "key", "name": name_map[match.group(1)], "ctrl": False, "meta": False}, "length": len(match.group(0))}

    if input_text.startswith("\u001b\t"):
        return {"event": {"kind": "key", "name": "tab", "ctrl": False, "meta": True}, "length": 2}
    if len(input_text) >= 2:
        char = input_text[1]
        if char not in {"[", "O"}:
            return {"event": {"kind": "text", "text": char, "ctrl": False, "meta": True}, "length": 2}
    return {"event": {"kind": "key", "name": "escape", "ctrl": False, "meta": False}, "length": 1}


def parse_input_chunk(previous_rest: str, chunk: bytes | str) -> dict[str, Any]:
    chunk_text = chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk)
    input_text = previous_rest + chunk_text
    treat_newlines_as_text = is_multiline_paste_chunk(chunk_text)
    events: list[dict[str, Any]] = []
    index = 0
    while index < len(input_text):
        remaining = input_text[index:]
        if remaining.startswith(ESC):
            if maybe_need_more_for_escape_sequence(remaining):
                return {"events": events, "rest": remaining}
            parsed = parse_escape_sequence(remaining)
            if parsed:
                if parsed.get("event"):
                    events.append(parsed["event"])
                index += parsed["length"]
                continue
        char = remaining[0]
        if char in {"\r", "\n"}:
            events.append({"kind": "text", "text": "\n", "ctrl": False, "meta": False} if treat_newlines_as_text else {"kind": "key", "name": "return", "ctrl": False, "meta": False})
            if (char == "\r" and len(remaining) > 1 and remaining[1] == "\n") or (char == "\n" and len(remaining) > 1 and remaining[1] == "\r"):
                index += 2
            else:
                index += 1
            continue
        if char == "\t":
            events.append({"kind": "key", "name": "tab", "ctrl": False, "meta": False})
            index += 1; continue
        if char in {"\u007f", "\b"}:
            events.append({"kind": "key", "name": "backspace", "ctrl": False, "meta": False})
            index += 1; continue
        if "\u0001" <= char <= "\u001a":
            name = CTRL_CHAR_TO_NAME.get(char)
            if name:
                events.append({"kind": "text", "text": name, "ctrl": True, "meta": False})
            index += 1; continue
        if char < " ":
            index += 1; continue
        events.append({"kind": "text", "text": char, "ctrl": False, "meta": False})
        index += 1
    return {"events": events, "rest": ""}


parseInputChunk = parse_input_chunk
