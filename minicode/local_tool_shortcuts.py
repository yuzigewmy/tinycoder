from __future__ import annotations

import re
from typing import Any

LocalToolShortcut = dict[str, Any]


def parse_local_tool_shortcut(input_text: str) -> LocalToolShortcut | None:
    text = input_text
    if text == "/ls" or text.startswith("/ls "):
        directory = text[len("/ls"):].strip()
        return {"toolName": "list_files", "input": {"path": directory} if directory else {}}

    if text.startswith("/grep "):
        payload = text[len("/grep "):].strip()
        parts = payload.split("::", 1)
        pattern = parts[0].strip() if parts else ""
        if not pattern:
            return None
        return {"toolName": "grep_files", "input": {"pattern": pattern, "path": parts[1].strip() if len(parts) > 1 and parts[1].strip() else None}}

    if text.startswith("/read "):
        file_path = text[len("/read "):].strip()
        if not file_path:
            return None
        return {"toolName": "read_file", "input": {"path": file_path}}

    if text.startswith("/write "):
        payload = text[len("/write "):]
        split_at = payload.find("::")
        if split_at == -1:
            return None
        target_path = payload[:split_at].strip()
        if not target_path:
            return None
        return {"toolName": "write_file", "input": {"path": target_path, "content": payload[split_at + 2:]}}

    if text.startswith("/modify "):
        payload = text[len("/modify "):]
        split_at = payload.find("::")
        if split_at == -1:
            return None
        target_path = payload[:split_at].strip()
        if not target_path:
            return None
        return {"toolName": "modify_file", "input": {"path": target_path, "content": payload[split_at + 2:]}}

    if text.startswith("/edit "):
        payload = text[len("/edit "):]
        parts = payload.split("::")
        if len(parts) < 3 or not parts[0].strip():
            return None
        return {"toolName": "edit_file", "input": {"path": parts[0].strip(), "search": parts[1], "replace": "::".join(parts[2:])}}

    if text.startswith("/cmd "):
        payload = text[len("/cmd "):].strip()
        split_at = payload.find("::")
        if split_at == -1:
            command_text = payload
            command_cwd = None
        else:
            command_cwd = payload[:split_at].strip() or None
            command_text = payload[split_at + 2:].strip()
        parts = re.split(r"\s+", command_text) if command_text else []
        if not parts or not parts[0]:
            return None
        return {"toolName": "run_command", "input": {"command": parts[0], "args": parts[1:], "cwd": command_cwd}}

    if text.startswith("/patch "):
        payload = text[len("/patch "):]
        parts = payload.split("::")
        if not parts or not parts[0].strip() or len(parts[1:]) < 2 or len(parts[1:]) % 2 != 0:
            return None
        replacements = []
        ops = parts[1:]
        for i in range(0, len(ops), 2):
            replacements.append({"search": ops[i], "replace": ops[i + 1]})
        return {"toolName": "patch_file", "input": {"path": parts[0].strip(), "replacements": replacements}}

    return None


parseLocalToolShortcut = parse_local_tool_shortcut
