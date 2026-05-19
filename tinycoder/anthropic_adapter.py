from __future__ import annotations

import asyncio
import json
import os
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


def get_retry_limit() -> int:
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


def normalize_anthropic_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total = input_tokens + output_tokens
    if total <= 0:
        return None
    return {"inputTokens": input_tokens, "outputTokens": output_tokens, "totalTokens": total, "source": "anthropic"}


def to_text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def to_assistant_text(message: dict[str, Any]) -> str:
    if message.get("role") == "assistant_progress":
        return f"<progress>\n{message.get('content', '')}\n</progress>"
    return str(message.get("content") or "")


def push_anthropic_message(messages: list[dict[str, Any]], role: str, block: dict[str, Any]) -> None:
    if messages and messages[-1].get("role") == role:
        messages[-1].setdefault("content", []).append(block)
    else:
        messages.append({"role": role, "content": [block]})


def to_anthropic_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        if role == "user":
            push_anthropic_message(converted, "user", to_text_block(str(message.get("content") or "")))
        elif role == "assistant_thinking":
            for block in message.get("blocks") or []:
                if isinstance(block, dict):
                    push_anthropic_message(converted, "assistant", block)
        elif role in {"assistant", "assistant_progress"}:
            push_anthropic_message(converted, "assistant", to_text_block(to_assistant_text(message)))
        elif role == "assistant_tool_call":
            push_anthropic_message(converted, "assistant", {"type": "tool_use", "id": message.get("toolUseId"), "name": message.get("toolName"), "input": message.get("input")})
        elif role == "context_summary":
            push_anthropic_message(converted, "user", to_text_block(f"[Context Summary from earlier conversation]\n{message.get('content', '')}"))
        elif role == "snip_boundary":
            push_anthropic_message(converted, "user", to_text_block(build_anthropic_snip_boundary_text()))
        else:
            push_anthropic_message(converted, "user", {"type": "tool_result", "tool_use_id": message.get("toolUseId"), "content": str(message.get("content") or ""), "is_error": bool(message.get("isError"))})
    return {"system": system, "messages": converted}


class AnthropicModelAdapter:
    def __init__(self, tools: ToolRegistry, get_runtime_config: Any) -> None:
        self.tools = tools
        self.get_runtime_config = get_runtime_config

    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        runtime = await self.get_runtime_config()
        payload = to_anthropic_messages(messages)
        base_url = str(runtime.get("baseUrl") or "https://api.anthropic.com").rstrip("/")
        url = f"{base_url}/v1/messages"
        max_output_tokens = resolve_max_output_tokens(str(runtime.get("model") or ""), runtime.get("maxOutputTokens"))
        headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
        if runtime.get("authToken"):
            headers["Authorization"] = f"Bearer {runtime.get('authToken')}"
        elif runtime.get("apiKey"):
            headers["x-api-key"] = str(runtime.get("apiKey"))
        request_body = {
            "model": runtime.get("model"),
            "system": payload["system"],
            "messages": payload["messages"],
            "tools": [{"name": tool.name, "description": tool.description, "input_schema": tool.input_schema} for tool in self.tools.list()],
            "max_tokens": max_output_tokens,
        }
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

        tool_calls: list[dict[str, Any]] = []
        text_parts: list[str] = []
        thinking_blocks: list[dict[str, Any]] = []
        block_types: list[str] = []
        ignored_block_types: set[str] = set()
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            block_types.append(block_type)
            if block_type == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
            elif block_type == "tool_use" and isinstance(block.get("id"), str) and isinstance(block.get("name"), str):
                tool_calls.append({"id": block["id"], "toolName": block["name"], "input": block.get("input")})
            elif block_type in {"thinking", "redacted_thinking"}:
                thinking_blocks.append(block)
            else:
                ignored_block_types.add(block_type)
        parsed_text = parse_assistant_text("\n".join(text_parts).strip())
        diagnostics = {"stopReason": data.get("stop_reason"), "blockTypes": block_types, "ignoredBlockTypes": sorted(ignored_block_types)}
        usage = normalize_anthropic_usage(data.get("usage"))
        if tool_calls:
            step = {"type": "tool_calls", "calls": tool_calls, "content": parsed_text.get("content") or None, "contentKind": "progress" if parsed_text.get("kind") == "progress" else None, "thinkingBlocks": thinking_blocks, "diagnostics": diagnostics, "usage": usage}
            return {k: v for k, v in step.items() if v is not None and v != []}
        return {"type": "assistant", "content": parsed_text.get("content") or "", "kind": parsed_text.get("kind"), "thinkingBlocks": thinking_blocks, "diagnostics": diagnostics, "usage": usage}


toAnthropicMessages = to_anthropic_messages
parseAssistantText = parse_assistant_text
normalizeAnthropicUsage = normalize_anthropic_usage
AnthropicAdapter = AnthropicModelAdapter



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


async def _anthropic_stream_next(self: AnthropicModelAdapter, messages: list[dict[str, Any]], on_text_delta: Any | None = None) -> dict[str, Any]:
    runtime = await self.get_runtime_config()
    payload = to_anthropic_messages(messages)
    base_url = str(runtime.get("baseUrl") or "https://api.anthropic.com").rstrip("/")
    url = f"{base_url}/v1/messages"
    max_output_tokens = resolve_max_output_tokens(str(runtime.get("model") or ""), runtime.get("maxOutputTokens"))
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if runtime.get("authToken"):
        headers["Authorization"] = f"Bearer {runtime.get('authToken')}"
    elif runtime.get("apiKey"):
        headers["x-api-key"] = str(runtime.get("apiKey"))
    request_body = {
        "model": runtime.get("model"),
        "system": payload["system"],
        "messages": payload["messages"],
        "tools": [{"name": tool.name, "description": tool.description, "input_schema": tool.input_schema} for tool in self.tools.list()],
        "max_tokens": max_output_tokens,
        "stream": True,
    }

    request = urllib.request.Request(url, data=json.dumps(request_body).encode("utf-8"), headers=headers, method="POST")
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    thinking_blocks: list[dict[str, Any]] = []
    block_types: list[str] = []
    ignored_block_types: set[str] = set()
    content_blocks: dict[int, dict[str, Any]] = {}
    stop_reason: str | None = None
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
        for event_name, raw_data in _iter_sse_payloads(response):
            if raw_data == "[DONE]":
                continue
            try:
                data = json.loads(raw_data)
            except Exception:
                continue
            event_type = str(data.get("type") or event_name or "")
            if event_type == "error":
                raise RuntimeError(extract_error_message(data, 500))
            if event_type == "message_start":
                usage = normalize_anthropic_usage((data.get("message") or {}).get("usage"))
                continue
            if event_type == "message_delta":
                delta = data.get("delta") or {}
                if isinstance(delta, dict) and delta.get("stop_reason"):
                    stop_reason = str(delta.get("stop_reason"))
                if isinstance(data.get("usage"), dict):
                    usage = normalize_anthropic_usage(data.get("usage")) or usage
                continue
            if event_type == "content_block_start":
                index = int(data.get("index") or 0)
                block = data.get("content_block") or {}
                if not isinstance(block, dict):
                    block = {}
                block_type = str(block.get("type") or "")
                block_types.append(block_type)
                content_blocks[index] = dict(block)
                if block_type == "text" and isinstance(block.get("text"), str) and block.get("text"):
                    delta_text = block["text"]
                    text_parts.append(delta_text)
                    streamed = True
                    await _emit_text_delta(on_text_delta, delta_text)
                continue
            if event_type == "content_block_delta":
                index = int(data.get("index") or 0)
                delta = data.get("delta") or {}
                if not isinstance(delta, dict):
                    continue
                block = content_blocks.setdefault(index, {})
                delta_type = str(delta.get("type") or "")
                if delta_type == "text_delta" and isinstance(delta.get("text"), str):
                    delta_text = delta["text"]
                    text_parts.append(delta_text)
                    streamed = True
                    await _emit_text_delta(on_text_delta, delta_text)
                elif delta_type == "input_json_delta" and isinstance(delta.get("partial_json"), str):
                    block["partial_json"] = str(block.get("partial_json") or "") + delta["partial_json"]
                elif delta_type == "thinking_delta" and isinstance(delta.get("thinking"), str):
                    block["thinking"] = str(block.get("thinking") or "") + delta["thinking"]
                else:
                    ignored_block_types.add(delta_type)
                continue
            if event_type == "content_block_stop":
                index = int(data.get("index") or 0)
                block = content_blocks.get(index) or {}
                block_type = str(block.get("type") or "")
                if block_type == "tool_use" and isinstance(block.get("id"), str) and isinstance(block.get("name"), str):
                    raw_input = block.get("partial_json") if "partial_json" in block else block.get("input")
                    if isinstance(raw_input, str):
                        try:
                            parsed_input = json.loads(raw_input) if raw_input.strip() else {}
                        except Exception:
                            parsed_input = {"_raw": raw_input}
                    else:
                        parsed_input = raw_input or {}
                    tool_calls.append({"id": block["id"], "toolName": block["name"], "input": parsed_input})
                elif block_type in {"thinking", "redacted_thinking"}:
                    thinking_blocks.append(block)
                continue

    parsed_text = parse_assistant_text("".join(text_parts).strip())
    diagnostics = {"stopReason": stop_reason, "blockTypes": block_types, "ignoredBlockTypes": sorted(ignored_block_types)}
    if tool_calls:
        step = {"type": "tool_calls", "calls": tool_calls, "content": parsed_text.get("content") or None, "contentKind": "progress" if parsed_text.get("kind") == "progress" else None, "thinkingBlocks": thinking_blocks, "diagnostics": diagnostics, "usage": usage, "streamed": streamed}
        return {k: v for k, v in step.items() if v is not None and v != []}
    return {"type": "assistant", "content": parsed_text.get("content") or "", "kind": parsed_text.get("kind"), "thinkingBlocks": thinking_blocks, "diagnostics": diagnostics, "usage": usage, "streamed": streamed}


AnthropicModelAdapter.stream_next = _anthropic_stream_next  # type: ignore[attr-defined]
