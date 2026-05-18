from __future__ import annotations

import time
from typing import Any

ChatMessage = dict[str, Any]
AgentStep = dict[str, Any]


def _last_user_message(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _last_tool_message(messages: list[ChatMessage]) -> ChatMessage | None:
    for message in reversed(messages):
        if message.get("role") == "tool_result":
            return message
    return None


def _extract_latest_assistant_call(messages: list[ChatMessage]) -> str | None:
    for message in reversed(messages):
        if message.get("role") == "assistant_tool_call":
            return message.get("toolName")
    return None


class MockModelAdapter:
    async def next(self, messages: list[ChatMessage]) -> AgentStep:
        tool_message = _last_tool_message(messages)
        if tool_message is not None:
            last_call = _extract_latest_assistant_call(messages)
            if last_call == "list_files":
                return {"type": "assistant", "content": f"目录内容如下：\n\n{tool_message.get('content', '')}"}
            if last_call == "read_file":
                return {"type": "assistant", "content": f"文件内容如下：\n\n{tool_message.get('content', '')}"}
            if last_call in {"write_file", "edit_file"}:
                return {"type": "assistant", "content": str(tool_message.get("content") or "")}
            return {"type": "assistant", "content": f"我拿到了工具结果：\n\n{tool_message.get('content', '')}"}

        user_text = _last_user_message(messages).strip()
        if user_text == "/tools":
            return {"type": "assistant", "content": "可用工具：ask_user, list_files, grep_files, read_file, write_file, edit_file, patch_file, modify_file, run_command, web_fetch, web_search"}

        def call(tool_name: str, input_value: dict[str, Any]) -> AgentStep:
            return {"type": "tool_calls", "calls": [{"id": f"mock-{int(time.time() * 1000)}", "toolName": tool_name, "input": input_value}]}

        if user_text.startswith("/ls"):
            directory = user_text.replace("/ls", "", 1).strip()
            return call("list_files", {"path": directory} if directory else {})
        if user_text.startswith("/grep "):
            payload = user_text[len("/grep "):].strip()
            parts = payload.split("::", 1)
            return call("grep_files", {"pattern": parts[0].strip(), "path": parts[1].strip() if len(parts) > 1 and parts[1].strip() else None})
        if user_text.startswith("/read "):
            return call("read_file", {"path": user_text[len("/read "):].strip()})
        if user_text.startswith("/cmd "):
            parts = user_text[len("/cmd "):].strip().split()
            if not parts:
                return {"type": "assistant", "content": "用法: /cmd 命令 [参数...]"}
            return call("run_command", {"command": parts[0], "args": parts[1:]})
        if user_text.startswith("/write "):
            payload = user_text[len("/write "):]
            split_at = payload.find("::")
            if split_at == -1:
                return {"type": "assistant", "content": "用法: /write 路径::内容"}
            return call("write_file", {"path": payload[:split_at].strip(), "content": payload[split_at + 2:]})
        if user_text.startswith("/edit "):
            parts = user_text[len("/edit "):].split("::")
            if len(parts) < 3 or not parts[0].strip():
                return {"type": "assistant", "content": "用法: /edit 路径::查找文本::替换文本"}
            return call("edit_file", {"path": parts[0].strip(), "search": parts[1], "replace": "::".join(parts[2:])})

        return {"type": "assistant", "content": "\n".join([
            "这是一个 Python 复刻版本。",
            "你可以试试：",
            "/tools",
            "/ls",
            "/grep pattern::src",
            "/read README.md",
            "/cmd pwd",
            "/write notes.txt::hello",
            "/edit notes.txt::hello::hello world",
        ])}
