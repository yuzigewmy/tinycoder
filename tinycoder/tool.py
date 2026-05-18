from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

ToolContext = dict[str, Any]
ToolResult = dict[str, Any]
BackgroundTaskResult = dict[str, Any]

Validator = Callable[[Any], Any]
Runner = Callable[[Any, ToolContext], Awaitable[ToolResult]]
Disposer = Callable[[], Awaitable[None]]


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Runner
    validator: Validator | None = None

    @property
    def inputSchema(self) -> dict[str, Any]:
        return self.input_schema

    def validate(self, value: Any) -> Any:
        if self.validator is None:
            return value
        return self.validator(value)


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition], metadata: dict[str, Any] | None = None, disposer: Disposer | None = None) -> None:
        self._tools = list(tools)
        self._metadata = dict(metadata or {})
        self._disposers: list[Disposer] = []
        if disposer:
            self._disposers.append(disposer)

    def list(self) -> list[ToolDefinition]:
        return self._tools

    def get_skills(self) -> list[dict[str, Any]]:
        return self._metadata.get("skills") or []

    def getSkills(self) -> list[dict[str, Any]]:
        return self.get_skills()

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        return self._metadata.get("mcpServers") or []

    def getMcpServers(self) -> list[dict[str, Any]]:
        return self.get_mcp_servers()

    def set_mcp_servers(self, servers: list[dict[str, Any]]) -> None:
        self._metadata = {**self._metadata, "mcpServers": list(servers)}

    def setMcpServers(self, servers: list[dict[str, Any]]) -> None:
        self.set_mcp_servers(servers)

    def add_tools(self, next_tools: list[ToolDefinition]) -> None:
        existing = {tool.name for tool in self._tools}
        for tool in next_tools:
            if tool.name in existing:
                continue
            self._tools.append(tool)
            existing.add(tool.name)

    def addTools(self, next_tools: list[ToolDefinition]) -> None:
        self.add_tools(next_tools)

    def add_disposer(self, disposer: Disposer) -> None:
        self._disposers.append(disposer)

    def addDisposer(self, disposer: Disposer) -> None:
        self.add_disposer(disposer)

    def find(self, name: str) -> ToolDefinition | None:
        return next((tool for tool in self._tools if tool.name == name), None)

    async def execute(self, tool_name: str, input_value: Any, context: ToolContext) -> ToolResult:
        tool = self.find(tool_name)
        if not tool:
            return {"ok": False, "output": f"Unknown tool: {tool_name}"}
        try:
            parsed = tool.validate(input_value)
        except Exception as error:
            return {"ok": False, "output": str(error)}
        try:
            return await tool.run(parsed, context)
        except Exception as error:
            return {"ok": False, "output": str(error)}

    async def dispose(self) -> None:
        for disposer in list(self._disposers):
            try:
                await disposer()
            except Exception:
                pass
