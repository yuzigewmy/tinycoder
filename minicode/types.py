from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Protocol, TypedDict, Union


class ProviderUsage(TypedDict):
    inputTokens: int
    outputTokens: int
    totalTokens: int
    source: str


class ProviderThinkingBlock(TypedDict, total=False):
    type: Literal["thinking", "redacted_thinking"]


ChatMessage = Dict[str, Any]
ToolCall = Dict[str, Any]
AgentStep = Dict[str, Any]
CompressionResult = Dict[str, Any]
StepDiagnostics = Dict[str, Any]


class ModelAdapter(Protocol):
    async def next(self, messages: List[ChatMessage]) -> AgentStep:
        ...
