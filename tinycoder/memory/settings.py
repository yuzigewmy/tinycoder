from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal


MemoryMode = Literal["off", "read_only", "suggest", "auto"]


def _positive_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


@dataclass(frozen=True)
class MemorySettings:
    mode: MemoryMode = "suggest"
    default_scope: str = "project_local"
    max_recall_tokens: int = 1500
    max_recall_items: int = 8
    max_candidates_per_turn: int = 5
    external_embedding_allowed: bool = False
    embedding_enabled: bool = False
    graph_enabled: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"off", "read_only", "suggest", "auto"}:
            raise ValueError(f"invalid memory mode: {self.mode}")
        if self.default_scope not in {"user", "project_shared", "project_local"}:
            raise ValueError(f"invalid default memory scope: {self.default_scope}")
        object.__setattr__(self, "max_recall_tokens", max(128, min(int(self.max_recall_tokens), 8_000)))
        object.__setattr__(self, "max_recall_items", max(1, min(int(self.max_recall_items), 20)))
        object.__setattr__(self, "max_candidates_per_turn", max(1, min(int(self.max_candidates_per_turn), 20)))

    @property
    def can_recall(self) -> bool:
        return self.mode != "off"

    @property
    def can_write(self) -> bool:
        return self.mode in {"suggest", "auto"}

    @classmethod
    def from_mapping(
        cls,
        value: dict[str, Any] | None,
        *,
        environ: dict[str, str] | None = None,
    ) -> "MemorySettings":
        value = value if isinstance(value, dict) else {}
        env = os.environ if environ is None else environ
        mode = str(env.get("TINYCODER_MEMORY_MODE") or value.get("mode") or "suggest").strip().lower()
        if env.get("TINYCODER_DISABLE_MEMORY") == "1":
            mode = "off"
        return cls(
            mode=mode,  # type: ignore[arg-type]
            default_scope=str(value.get("defaultScope") or "project_local"),
            max_recall_tokens=_positive_int(value.get("maxRecallTokens"), 1500, 8_000),
            max_recall_items=_positive_int(value.get("maxRecallItems"), 8, 20),
            max_candidates_per_turn=_positive_int(value.get("maxCandidatesPerTurn"), 5, 20),
            external_embedding_allowed=bool(value.get("externalEmbeddingAllowed", False)),
            embedding_enabled=bool(value.get("embeddingEnabled", False))
            and env.get("TINYCODER_DISABLE_EMBEDDINGS") != "1",
            graph_enabled=bool(value.get("graphEnabled", False))
            and env.get("TINYCODER_DISABLE_GRAPH_MEMORY") != "1",
        )
