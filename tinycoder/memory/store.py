from __future__ import annotations

from typing import Protocol

from .models import MemoryItem, MemoryQuery, MemorySearchResult, UpsertOutcome


class MemoryStore(Protocol):
    def upsert(self, item: MemoryItem) -> UpsertOutcome: ...

    def get(self, memory_id: str) -> MemoryItem | None: ...

    def search(self, query: MemoryQuery) -> list[MemorySearchResult]: ...

    def delete(self, memory_id: str, *, reason: str) -> bool: ...

    def close(self) -> None: ...
