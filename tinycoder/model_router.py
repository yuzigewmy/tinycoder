from __future__ import annotations

import os
from typing import Any

from .anthropic_adapter import AnthropicModelAdapter
from .mock_model import MockModelAdapter
from .qwen_adapter import QwenModelAdapter
from .tool import ToolRegistry


class ModelRouter:
    """Runtime model router.

    The router reads the current runtime config before every model turn, so slash
    commands can switch provider/model/API key without restarting TinyCoder.
    """

    def __init__(self, tools: ToolRegistry, get_runtime_config: Any) -> None:
        self.tools = tools
        self.get_runtime_config = get_runtime_config
        self._mock = MockModelAdapter()
        self._adapters: dict[str, Any] = {}

    def _provider_key(self, provider: str | None) -> str:
        raw = (provider or "anthropic").strip().lower()
        if raw in {"qwen", "dashscope", "aliyun"}:
            return "qwen"
        if raw in {"anthropic", "claude"}:
            return "anthropic"
        return raw

    def _get_adapter(self, provider: str) -> Any:
        provider = self._provider_key(provider)
        if provider not in self._adapters:
            if provider == "qwen":
                self._adapters[provider] = QwenModelAdapter(self.tools, self.get_runtime_config)
            elif provider == "anthropic":
                self._adapters[provider] = AnthropicModelAdapter(self.tools, self.get_runtime_config)
            else:
                raise RuntimeError(f"Unsupported model provider: {provider}. Use anthropic or qwen.")
        return self._adapters[provider]

    async def current_runtime(self) -> dict[str, Any]:
        return await self.get_runtime_config()

    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        if os.environ.get("TINYCODER_MODEL_MODE") == "mock":
            return await self._mock.next(messages)
        runtime = await self.get_runtime_config()
        provider = self._provider_key(str(runtime.get("provider") or "anthropic"))
        return await self._get_adapter(provider).next(messages)
