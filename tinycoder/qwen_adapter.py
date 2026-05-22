from __future__ import annotations

import asyncio
import json
import random
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Any

from .compact.snip_compact import build_anthropic_snip_boundary_text
from .tool import ToolRegistry
from .utils.context import resolve_max_output_tokens

DEFAULT_MAX_RETRIES = 4
BASE_RETRY_DELAY_MS = 500
MAX_RETRY_DELAY_MS = 8000


def _sleep_ms(ms: int) -> None:
    time.sleep(max(0, ms) / 1000.0)


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return json.dumps(str(value), ensure_ascii=False)


def _parse_json_arguments(arguments: Any) -> Any:
    if isinstance(arguments, dict):
        return arguments
    if arguments is None:
        return {}
    text = str(arguments).strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text}


def build_chat_completions_url(base_url: str) -> str:
    cleaned = str(base_url or "").strip().rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


def get_retry_limit() -> int:
    import os

    try:
        value = int(float(os.environ.get("TINYCODER_MAX_RETRIES", "")))
        return value if value >= 0 else DEFAULT_MAX_RETRIES
    except Exception:
        return DEFAULT_MAX_RETRIES


def should_retry_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def parse_retry_after_ms(retry_after: str | None) -> int | None:
    if not retry_after:
        return None
    try:
        seconds = float(retry_after)
        if seconds >= 0:
            return int(seconds * 1000)
    except Exception:
        pass
    try:
        at = parsedate_to_datetime(retry_after).timestamp() * 1000
        return max(0, int(at - time.time() * 1000))
    except Exception:
        return None


def get_retry_delay_ms(attempt: int, retry_after_ms: int | None) -> int:
    if retry_after_ms is not None:
        return retry_after_ms
    base = min(BASE_RETRY_DELAY_MS * (2 ** max(0, attempt - 1)), MAX_RETRY_DELAY_MS)
    return int(base + random.random() * 0.25 * base)


def extract_error_message(data: Any, status: int) -> str:
    if isinstance(data, str) and data.strip():
        return data.strip()
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str) and error["message"].strip():
            return error["message"].strip()
        if isinstance(error, str) and error.strip():
            return error.strip()
        if isinstance(data.get("message"), str) and data["message"].strip():
            return data["message"].strip()
    return f"Model request failed: {status}"


def parse_assistant_text(content: str) -> dict[str, Any]:
    trimmed = content.strip()
    if not trimmed:
        return {"content": ""}
    markers = [("<final>", "final"), ("[FINAL]", "final"), ("<progress>", "progress"), ("[PROGRESS]", "progress")]
    for prefix, kind in markers:
        if trimmed.startswith(prefix):
            raw = trimmed[len(prefix):].strip()
            closing = "</progress>" if kind == "progress" else "</final>"
            return {"content": raw.replace(closing, "").strip(), "kind": kind}
    return {"content": trimmed}


def normalize_openai_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    if total <= 0:
        return None
    return {"inputTokens": input_tokens, "outputTokens": output_tokens, "totalTokens": total, "source": "qwen"}


def to_openai_tools(tools: ToolRegistry) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools.list():
        converted.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        })
    return converted


def to_assistant_text(message: dict[str, Any]) -> str:
    if message.get("role") == "assistant_progress":
        return f"<progress>\n{message.get('content', '')}\n</progress>"
    return str(message.get("content") or "")


def _append_tool_call_group(converted: list[dict[str, Any]], group: list[dict[str, Any]]) -> None:
    if not group:
        return
    tool_calls: list[dict[str, Any]] = []
    for message in group:
        tool_calls.append({
            "id": str(message.get("toolUseId") or ""),
            "type": "function",
            "function": {
                "name": str(message.get("toolName") or ""),
                "arguments": _json_dumps(message.get("input") or {}),
            },
        })
    converted.append({"role": "assistant", "content": None, "tool_calls": tool_calls})


def to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    converted: list[dict[str, Any]] = []
    if system:
        converted.append({"role": "system", "content": system})

    pending_tool_calls: list[dict[str, Any]] = []

    def flush_tool_calls() -> None:
        nonlocal pending_tool_calls
        if pending_tool_calls:
            _append_tool_call_group(converted, pending_tool_calls)
            pending_tool_calls = []

    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        if role == "assistant_tool_call":
            pending_tool_calls.append(message)
            continue

        flush_tool_calls()

        if role == "user":
            converted.append({"role": "user", "content": str(message.get("content") or "")})
        elif role == "assistant_thinking":
            # OpenAI-compatible Chat API does not accept Anthropic thinking blocks.
            continue
        elif role in {"assistant", "assistant_progress"}:
            converted.append({"role": "assistant", "content": to_assistant_text(message)})
        elif role == "context_summary":
            converted.append({"role": "user", "content": f"[Context Summary from earlier conversation]\n{message.get('content', '')}"})
        elif role == "snip_boundary":
            converted.append({"role": "user", "content": build_anthropic_snip_boundary_text()})
        elif role == "tool_result":
            content = str(message.get("content") or "")
            if message.get("isError"):
                content = "[tool error]\n" + content
            converted.append({"role": "tool", "tool_call_id": str(message.get("toolUseId") or ""), "content": content})
        else:
            # Unknown internal roles are preserved as user-visible context instead of being dropped.
            converted.append({"role": "user", "content": str(message.get("content") or "")})

    flush_tool_calls()
    return converted


class QwenModelAdapter:
    """Qwen/DashScope adapter implemented through the OpenAI-compatible Chat API."""

    def __init__(self, tools: ToolRegistry, get_runtime_config: Any) -> None:
        self.tools = tools
        self.get_runtime_config = get_runtime_config

    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        runtime = await self.get_runtime_config()
        base_url = str(runtime.get("baseUrl") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        url = build_chat_completions_url(base_url)
        model = str(runtime.get("model") or "qwen-plus")
        max_output_tokens = resolve_max_output_tokens(model, runtime.get("maxOutputTokens"))
        headers = {"content-type": "application/json"}
        if runtime.get("authToken"):
            headers["Authorization"] = f"Bearer {runtime.get('authToken')}"
        elif runtime.get("apiKey"):
            headers["Authorization"] = f"Bearer {runtime.get('apiKey')}"

        openai_tools = to_openai_tools(self.tools)
        request_body: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(messages),
            "max_tokens": max_output_tokens,
        }
        if openai_tools:
            request_body["tools"] = openai_tools
            request_body["tool_choice"] = "auto"

        status = 0
        data: Any = None
        response_headers: dict[str, str] = {}
        max_retries = get_retry_limit()
        for attempt in range(max_retries + 1):
            def do_request() -> tuple[int, dict[str, str], Any]:
                request = urllib.request.Request(url, data=json.dumps(request_body).encode("utf-8"), headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(request, timeout=120) as response:
                        text = response.read().decode("utf-8", "replace")
                        return response.status, dict(response.headers), json.loads(text) if text.strip() else {}
                except urllib.error.HTTPError as error:
                    text = error.read().decode("utf-8", "replace")
                    try:
                        parsed: Any = json.loads(text) if text.strip() else {}
                    except Exception:
                        parsed = {"error": {"message": text.strip()}}
                    return error.code, dict(error.headers), parsed

            status, response_headers, data = await asyncio.to_thread(do_request)
            if 200 <= status < 300:
                break
            if not should_retry_status(status) or attempt >= max_retries:
                break
            _sleep_ms(get_retry_delay_ms(attempt + 1, parse_retry_after_ms(response_headers.get("retry-after") or response_headers.get("Retry-After"))))

        if not (200 <= status < 300):
            raise RuntimeError(extract_error_message(data, status))

        choices = data.get("choices") if isinstance(data, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message") if isinstance(first, dict) else {}
        if not isinstance(message, dict):
            message = {}

        text_parts: list[str] = []
        content = message.get("content")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])

        tool_calls: list[dict[str, Any]] = []
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            tool_calls.append({
                "id": str(call.get("id") or f"qwen_tool_{len(tool_calls) + 1}"),
                "toolName": name,
                "input": _parse_json_arguments(function.get("arguments")),
            })

        parsed_text = parse_assistant_text("\n".join(text_parts).strip())
        finish_reason = first.get("finish_reason") if isinstance(first, dict) else None
        diagnostics = {"stopReason": finish_reason, "blockTypes": ["text"] if text_parts else [], "ignoredBlockTypes": []}
        usage = normalize_openai_usage(data.get("usage") if isinstance(data, dict) else None)

        if tool_calls:
            return {
                "type": "tool_calls",
                "calls": tool_calls,
                "content": parsed_text.get("content") or None,
                "contentKind": "progress" if parsed_text.get("kind") == "progress" else parsed_text.get("kind"),
                "diagnostics": diagnostics,
                "usage": usage,
            }

        return {
            "type": "assistant",
            "content": parsed_text.get("content") or "",
            "kind": parsed_text.get("kind"),
            "diagnostics": diagnostics,
            "usage": usage,
        }



async def _emit_text_delta(callback: Any | None, delta: str) -> None:
    if callback is None or not delta:
        return
    result = callback(delta)
    if hasattr(result, "__await__"):
        await result


def _iter_sse_payloads(response: Any):
    event_name: str | None = None
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", "replace").rstrip("\n")
        if line.endswith("\r"):
            line = line[:-1]
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if data_lines:
        yield event_name, "\n".join(data_lines)


async def _qwen_stream_next(self: QwenModelAdapter, messages: list[dict[str, Any]], on_text_delta: Any | None = None) -> dict[str, Any]:
    runtime = await self.get_runtime_config()
    base_url = str(runtime.get("baseUrl") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    url = build_chat_completions_url(base_url)
    model = str(runtime.get("model") or "qwen-plus")
    max_output_tokens = resolve_max_output_tokens(model, runtime.get("maxOutputTokens"))
    headers = {"content-type": "application/json"}
    if runtime.get("authToken"):
        headers["Authorization"] = f"Bearer {runtime.get('authToken')}"
    elif runtime.get("apiKey"):
        headers["Authorization"] = f"Bearer {runtime.get('apiKey')}"

    openai_tools = to_openai_tools(self.tools)
    request_body: dict[str, Any] = {
        "model": model,
        "messages": to_openai_messages(messages),
        "max_tokens": max_output_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if openai_tools:
        request_body["tools"] = openai_tools
        request_body["tool_choice"] = "auto"

    request = urllib.request.Request(url, data=json.dumps(request_body).encode("utf-8"), headers=headers, method="POST")
    text_parts: list[str] = []
    tool_buffers: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    streamed = False

    try:
        response_ctx = urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace")
        try:
            parsed: Any = json.loads(text) if text.strip() else {}
        except Exception:
            parsed = {"error": {"message": text.strip()}}
        raise RuntimeError(extract_error_message(parsed, error.code)) from None

    with response_ctx as response:
        for _event_name, raw_data in _iter_sse_payloads(response):
            if raw_data == "[DONE]":
                break
            try:
                data = json.loads(raw_data)
            except Exception:
                continue
            if isinstance(data, dict) and data.get("usage"):
                usage = normalize_openai_usage(data.get("usage")) or usage
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                finish_reason = str(choice.get("finish_reason"))
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                text_parts.append(content)
                streamed = True
                await _emit_text_delta(on_text_delta, content)
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                index = int(call.get("index") or 0)
                current = tool_buffers.setdefault(index, {"id": "", "toolName": "", "arguments": ""})
                if call.get("id"):
                    current["id"] = str(call.get("id"))
                function = call.get("function") or {}
                if isinstance(function, dict):
                    if function.get("name"):
                        current["toolName"] = str(function.get("name"))
                    if function.get("arguments"):
                        current["arguments"] = str(current.get("arguments") or "") + str(function.get("arguments"))

    parsed_text = parse_assistant_text("".join(text_parts).strip())
    diagnostics = {"stopReason": finish_reason, "blockTypes": ["text"] if text_parts else [], "ignoredBlockTypes": []}
    tool_calls: list[dict[str, Any]] = []
    for index, current in sorted(tool_buffers.items()):
        name = str(current.get("toolName") or "")
        if not name:
            continue
        tool_calls.append({
            "id": str(current.get("id") or f"qwen_tool_{index + 1}"),
            "toolName": name,
            "input": _parse_json_arguments(current.get("arguments")),
        })

    if tool_calls:
        return {
            "type": "tool_calls",
            "calls": tool_calls,
            "content": parsed_text.get("content") or None,
            "contentKind": "progress" if parsed_text.get("kind") == "progress" else parsed_text.get("kind"),
            "diagnostics": diagnostics,
            "usage": usage,
            "streamed": streamed,
        }
    return {
        "type": "assistant",
        "content": parsed_text.get("content") or "",
        "kind": parsed_text.get("kind"),
        "diagnostics": diagnostics,
        "usage": usage,
        "streamed": streamed,
    }


QwenModelAdapter.stream_next = _qwen_stream_next  # type: ignore[attr-defined]
