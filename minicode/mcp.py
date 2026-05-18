from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import read_mcp_tokens_file
from .tool import ToolDefinition

JsonRpcProtocol = str
MCP_INITIALIZE_TIMEOUT_MS = 10.0
MCP_PROTOCOL_CACHE_PATH = Path.home() / ".mini-code" / "mcp-protocol-cache.json"


def sanitize_tool_segment(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9_-]+", "_", value.lower())).strip("_") or "tool"


def normalize_input_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    return schema if isinstance(schema, dict) else {"type": "object", "additionalProperties": True}


def summarize_server_endpoint(config: dict[str, Any]) -> str:
    remote_url = str(config.get("url") or "").strip()
    if remote_url:
        return remote_url
    command = str(config.get("command") or "").strip()
    args = " ".join(str(x) for x in (config.get("args") or []))
    return f"{command} {args}".strip()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _format_content_block(block: Any) -> str:
    if not isinstance(block, dict):
        return json.dumps(block, ensure_ascii=False, indent=2)
    if block.get("type") == "text" and "text" in block:
        return str(block.get("text") or "")
    return json.dumps(block, ensure_ascii=False, indent=2)


def format_tool_call_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"ok": True, "output": json.dumps(result, ensure_ascii=False, indent=2)}
    parts: list[str] = []
    if isinstance(result.get("content"), list) and result["content"]:
        parts.append("\n\n".join(_format_content_block(block) for block in result["content"]))
    if result.get("structuredContent") is not None:
        parts.append("STRUCTURED_CONTENT:\n" + json.dumps(result.get("structuredContent"), ensure_ascii=False, indent=2))
    if not parts:
        parts.append(json.dumps(result, ensure_ascii=False, indent=2))
    return {"ok": not bool(result.get("isError")), "output": "\n\n".join(parts).strip()}


def format_read_resource_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"ok": False, "output": json.dumps(result, ensure_ascii=False, indent=2)}
    contents = result.get("contents") or []
    if not contents:
        return {"ok": True, "output": "No resource contents returned."}
    rendered: list[str] = []
    for item in contents:
        if not isinstance(item, dict):
            rendered.append(json.dumps(item, ensure_ascii=False, indent=2))
            continue
        header = [f"URI: {item.get('uri') or '(unknown)'}"]
        if item.get("mimeType"):
            header.append(f"MIME: {item.get('mimeType')}")
        body = item.get("text") if isinstance(item.get("text"), str) else ("BLOB:\n" + item.get("blob") if isinstance(item.get("blob"), str) else json.dumps(item, ensure_ascii=False, indent=2))
        rendered.append("\n".join(header) + "\n\n" + str(body))
    return {"ok": True, "output": "\n\n".join(rendered)}


def format_prompt_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"ok": False, "output": json.dumps(result, ensure_ascii=False, indent=2)}
    chunks: list[str] = []
    if result.get("description"):
        chunks.append(f"DESCRIPTION: {result.get('description')}")
    for message in result.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "unknown"
        content = message.get("content")
        if isinstance(content, str):
            body = content
        elif isinstance(content, list):
            body = "\n".join(str(part.get("text")) if isinstance(part, dict) and "text" in part else json.dumps(part, ensure_ascii=False, indent=2) for part in content)
        else:
            body = json.dumps(content, ensure_ascii=False, indent=2)
        chunks.append(f"[{role}]\n{body}")
    return {"ok": True, "output": "\n\n".join(chunks).strip() or json.dumps(result, ensure_ascii=False, indent=2)}


class StdioMcpClient:
    def __init__(self, server_name: str, config: dict[str, Any], cwd: str, protocol: str = "content-length") -> None:
        self.server_name = server_name
        self.config = config
        self.cwd = cwd
        self.protocol = protocol
        self.process: subprocess.Popen[bytes] | None = None
        self.next_id = 1
        self.pending: dict[int, queue.Queue[Any]] = {}
        self.stderr_lines: list[str] = []
        self.reader_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None
        self.closed = False

    def get_protocol(self) -> str | None:
        return self.protocol

    def get_server_name(self) -> str:
        return self.server_name

    async def start(self) -> None:
        command = str(self.config.get("command") or "").strip()
        if not command:
            raise RuntimeError(f'MCP server "{self.server_name}" has no command.')
        args = [str(arg) for arg in (self.config.get("args") or [])]
        env = os.environ.copy()
        for key, value in (self.config.get("env") or {}).items():
            env[str(key)] = os.path.expandvars(str(value))
        preferred = self.config.get("protocol")
        protocols = [preferred] if preferred in {"content-length", "newline-json"} else ["content-length", "newline-json"]
        last_error: Exception | None = None
        for protocol in protocols:
            try:
                self.protocol = protocol
                self.process = subprocess.Popen([command, *args], cwd=self.cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self._start_threads()
                await self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "mini-code-python", "version": "0.1.0"}}, timeout=MCP_INITIALIZE_TIMEOUT_MS)
                self.notify("notifications/initialized", {})
                return
            except Exception as error:
                last_error = error
                await self.close()
                self.closed = False
                self.pending.clear()
        raise RuntimeError(f"Failed to initialize MCP server {self.server_name}: {last_error}")

    def _start_threads(self) -> None:
        assert self.process is not None
        self.reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.reader_thread.start()
        self.stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self.stderr_thread.start()

    def _stderr_loop(self) -> None:
        proc = self.process
        if not proc or not proc.stderr:
            return
        for raw in iter(proc.stderr.readline, b""):
            text = raw.decode("utf-8", "replace").strip()
            if text:
                self.stderr_lines.append(text)
                self.stderr_lines = self.stderr_lines[-20:]

    def _read_loop(self) -> None:
        try:
            if self.protocol == "newline-json":
                self._read_newline_json()
            else:
                self._read_content_length()
        except Exception as error:
            for pending in list(self.pending.values()):
                pending.put({"error": {"code": -32000, "message": str(error)}})

    def _read_newline_json(self) -> None:
        proc = self.process
        if not proc or not proc.stdout:
            return
        for line in iter(proc.stdout.readline, b""):
            if not line.strip():
                continue
            try:
                self._handle_message(json.loads(line.decode("utf-8")))
            except Exception:
                continue

    def _read_content_length(self) -> None:
        proc = self.process
        if not proc or not proc.stdout:
            return
        buf = b""
        while not self.closed:
            chunk = proc.stdout.read(1)
            if not chunk:
                return
            buf += chunk
            while b"\r\n\r\n" in buf or b"\n\n" in buf:
                sep = b"\r\n\r\n" if b"\r\n\r\n" in buf else b"\n\n"
                header, rest = buf.split(sep, 1)
                match = re.search(br"Content-Length:\s*(\d+)", header, re.I)
                if not match:
                    buf = rest
                    continue
                length = int(match.group(1))
                while len(rest) < length:
                    more = proc.stdout.read(length - len(rest))
                    if not more:
                        return
                    rest += more
                body = rest[:length]
                buf = rest[length:]
                try:
                    self._handle_message(json.loads(body.decode("utf-8")))
                except Exception:
                    continue

    def _handle_message(self, message: dict[str, Any]) -> None:
        msg_id = message.get("id")
        if isinstance(msg_id, int) and msg_id in self.pending:
            self.pending[msg_id].put(message)

    def _send(self, message: dict[str, Any]) -> None:
        proc = self.process
        if not proc or not proc.stdin:
            raise RuntimeError("MCP process is not running")
        payload = _json_dumps(message).encode("utf-8")
        if self.protocol == "newline-json":
            proc.stdin.write(payload + b"\n")
        else:
            proc.stdin.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
        proc.stdin.flush()

    def notify(self, method: str, params: Any | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def request(self, method: str, params: Any | None = None, timeout: float = 30.0) -> Any:
        return await asyncio.to_thread(self._request_blocking, method, params or {}, timeout)

    def _request_blocking(self, method: str, params: Any, timeout: float) -> Any:
        req_id = self.next_id
        self.next_id += 1
        pending: queue.Queue[Any] = queue.Queue(maxsize=1)
        self.pending[req_id] = pending
        try:
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            try:
                response = pending.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f"request timed out for {method}")
            if isinstance(response, dict) and response.get("error"):
                error = response["error"]
                raise RuntimeError(error.get("message") if isinstance(error, dict) else str(error))
            return response.get("result") if isinstance(response, dict) else response
        finally:
            self.pending.pop(req_id, None)

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self.request("tools/list")
        return (result or {}).get("tools") or [] if isinstance(result, dict) else []

    async def list_resources(self) -> list[dict[str, Any]]:
        try:
            result = await self.request("resources/list")
            return (result or {}).get("resources") or [] if isinstance(result, dict) else []
        except Exception:
            return []

    async def read_resource(self, uri: str) -> dict[str, Any]:
        return format_read_resource_result(await self.request("resources/read", {"uri": uri}))

    async def list_prompts(self) -> list[dict[str, Any]]:
        try:
            result = await self.request("prompts/list")
            return (result or {}).get("prompts") or [] if isinstance(result, dict) else []
        except Exception:
            return []

    async def get_prompt(self, name: str, args: dict[str, str] | None = None) -> dict[str, Any]:
        return format_prompt_result(await self.request("prompts/get", {"name": name, "arguments": args or {}}))

    async def call_tool(self, name: str, input_value: Any) -> dict[str, Any]:
        return format_tool_call_result(await self.request("tools/call", {"name": name, "arguments": input_value or {}}))

    async def close(self) -> None:
        self.closed = True
        proc = self.process
        self.process = None
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


class HttpMcpClient:
    def __init__(self, server_name: str, config: dict[str, Any], cwd: str) -> None:
        self.server_name = server_name
        self.config = config
        self.cwd = cwd
        self.next_id = 1
        self.headers: dict[str, str] = {str(k): os.path.expandvars(str(v)) for k, v in (config.get("headers") or {}).items()}

    def get_protocol(self) -> str | None:
        return "streamable-http"

    def get_server_name(self) -> str:
        return self.server_name

    async def start(self) -> None:
        token = (await read_mcp_tokens_file()).get(self.server_name)
        if token and "authorization" not in {k.lower(): v for k, v in self.headers.items()}:
            self.headers["Authorization"] = f"Bearer {token.strip()}"
        await self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "mini-code-python", "version": "0.1.0"}}, timeout=20)

    async def request(self, method: str, params: Any | None = None, timeout: float = 30.0) -> Any:
        req_id = self.next_id
        self.next_id += 1
        body = _json_dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}).encode("utf-8")
        request = urllib.request.Request(str(self.config.get("url")), data=body, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}, method="POST")
        def do() -> Any:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
                if response.headers.get("content-type", "").startswith("text/event-stream"):
                    data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
                    text = "\n".join(data_lines[-1:]) or text
                parsed = json.loads(text)
                if isinstance(parsed, dict) and parsed.get("error"):
                    raise RuntimeError(parsed["error"].get("message") if isinstance(parsed["error"], dict) else str(parsed["error"]))
                return parsed.get("result") if isinstance(parsed, dict) else parsed
        return await asyncio.to_thread(do)

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self.request("tools/list")
        return (result or {}).get("tools") or [] if isinstance(result, dict) else []

    async def list_resources(self) -> list[dict[str, Any]]:
        try:
            result = await self.request("resources/list")
            return (result or {}).get("resources") or [] if isinstance(result, dict) else []
        except Exception:
            return []

    async def read_resource(self, uri: str) -> dict[str, Any]:
        return format_read_resource_result(await self.request("resources/read", {"uri": uri}))

    async def list_prompts(self) -> list[dict[str, Any]]:
        try:
            result = await self.request("prompts/list")
            return (result or {}).get("prompts") or [] if isinstance(result, dict) else []
        except Exception:
            return []

    async def get_prompt(self, name: str, args: dict[str, str] | None = None) -> dict[str, Any]:
        return format_prompt_result(await self.request("prompts/get", {"name": name, "arguments": args or {}}))

    async def call_tool(self, name: str, input_value: Any) -> dict[str, Any]:
        return format_tool_call_result(await self.request("tools/call", {"name": name, "arguments": input_value or {}}))

    async def close(self) -> None:
        return None


def _validate_object(input_value: Any) -> dict[str, Any]:
    if input_value is None:
        return {}
    if not isinstance(input_value, dict):
        raise ValueError("input must be an object")
    return input_value


def _validate_uri(input_value: Any) -> dict[str, str]:
    if not isinstance(input_value, dict) or not isinstance(input_value.get("uri"), str) or not input_value["uri"]:
        raise ValueError("uri must be a non-empty string")
    return {"uri": input_value["uri"]}


def _validate_prompt(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict) or not isinstance(input_value.get("name"), str) or not input_value["name"]:
        raise ValueError("name must be a non-empty string")
    args = input_value.get("arguments") or input_value.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    return {"name": input_value["name"], "arguments": {str(k): str(v) for k, v in args.items()}}


def create_mcp_helper_tools(clients: dict[str, Any]) -> list[ToolDefinition]:
    async def list_resources(_: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        lines: list[str] = []
        for server_name, client in clients.items():
            resources = await client.list_resources()
            if not resources:
                continue
            lines.append(f"SERVER: {server_name}")
            for item in resources:
                lines.append(f"- {item.get('uri')}" + (f" ({item.get('name')})" if item.get("name") else ""))
                if item.get("description"):
                    lines.append(f"  {item.get('description')}")
        return {"ok": True, "output": "\n".join(lines) if lines else "No MCP resources available."}

    async def read_resource(input_value: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
        uri = input_value["uri"]
        for client in clients.values():
            try:
                return await client.read_resource(uri)
            except Exception:
                continue
        return {"ok": False, "output": f"Resource not found or unreadable: {uri}"}

    async def list_prompts(_: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        lines: list[str] = []
        for server_name, client in clients.items():
            prompts = await client.list_prompts()
            if not prompts:
                continue
            lines.append(f"SERVER: {server_name}")
            for prompt in prompts:
                lines.append(f"- {prompt.get('name')}" + (f": {prompt.get('description')}" if prompt.get("description") else ""))
        return {"ok": True, "output": "\n".join(lines) if lines else "No MCP prompts available."}

    async def get_prompt(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        name = input_value["name"]
        for client in clients.values():
            try:
                return await client.get_prompt(name, input_value.get("arguments") or {})
            except Exception:
                continue
        return {"ok": False, "output": f"Prompt not found or unavailable: {name}"}

    return [
        ToolDefinition("list_mcp_resources", "List resources exposed by connected MCP servers.", {"type": "object", "properties": {}}, list_resources, _validate_object),
        ToolDefinition("read_mcp_resource", "Read a resource exposed by an MCP server by URI.", {"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"]}, read_resource, _validate_uri),
        ToolDefinition("list_mcp_prompts", "List prompts exposed by connected MCP servers.", {"type": "object", "properties": {}}, list_prompts, _validate_object),
        ToolDefinition("get_mcp_prompt", "Get a named prompt from a connected MCP server.", {"type": "object", "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["name"]}, get_prompt, _validate_prompt),
    ]


def create_mcp_tool(client: Any, server_name: str, descriptor: dict[str, Any]) -> ToolDefinition:
    raw_name = str(descriptor.get("name") or "tool")
    safe_name = f"mcp__{sanitize_tool_segment(server_name)}__{sanitize_tool_segment(raw_name)}"
    description = descriptor.get("description") or f"Call MCP tool {raw_name} on server {server_name}."
    async def _run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return await client.call_tool(raw_name, input_value)
    return ToolDefinition(safe_name, str(description), normalize_input_schema(descriptor.get("inputSchema")), _run, _validate_object)


async def create_mcp_backed_tools(args: dict[str, Any]) -> dict[str, Any]:
    cwd = args.get("cwd") or os.getcwd()
    mcp_servers: dict[str, Any] = args.get("mcpServers") or {}
    tools: list[ToolDefinition] = []
    summaries: list[dict[str, Any]] = []
    clients: dict[str, Any] = {}
    for name, config in mcp_servers.items():
        command_label = summarize_server_endpoint(config)
        if config.get("enabled") is False:
            summaries.append({"name": name, "command": command_label, "status": "disabled", "toolCount": 0, "protocol": config.get("protocol") if config.get("protocol") != "auto" else None})
            continue
        client = HttpMcpClient(name, config, cwd) if str(config.get("url") or "").strip() else StdioMcpClient(name, config, cwd)
        try:
            await client.start()
            descriptors = await client.list_tools()
            resources = await client.list_resources()
            prompts = await client.list_prompts()
            clients[name] = client
            for descriptor in descriptors:
                tools.append(create_mcp_tool(client, name, descriptor))
            summaries.append({"name": name, "command": command_label, "status": "connected", "toolCount": len(descriptors), "protocol": client.get_protocol(), "resourceCount": len(resources), "promptCount": len(prompts)})
        except Exception as error:
            summaries.append({"name": name, "command": command_label, "status": "error", "toolCount": 0, "error": str(error), "protocol": config.get("protocol") if config.get("protocol") != "auto" else None})
            try:
                await client.close()
            except Exception:
                pass
    if clients:
        tools.extend(create_mcp_helper_tools(clients))

    async def dispose() -> None:
        for client in clients.values():
            try:
                await client.close()
            except Exception:
                pass

    return {"tools": tools, "servers": summaries, "dispose": dispose}


createMcpBackedTools = create_mcp_backed_tools
createMcpBackedTool = create_mcp_tool
formatToolCallResult = format_tool_call_result
formatReadResourceResult = format_read_resource_result
formatPromptResult = format_prompt_result
sanitizeToolSegment = sanitize_tool_segment
normalizeInputSchema = normalize_input_schema
summarizeServerEndpoint = summarize_server_endpoint
