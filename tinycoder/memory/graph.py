from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .models import MemoryItem

if TYPE_CHECKING:
    from .sqlite_store import SQLiteMemoryStore


@dataclass(frozen=True)
class GraphEntity:
    id: str
    type: str
    name: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class GraphEdge:
    source: str
    relation: str
    target: str
    memory_id: str | None
    confidence: float


@dataclass(frozen=True)
class GraphSnapshot:
    entities: list[GraphEntity]
    edges: list[GraphEdge]

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "entities": [
                {
                    "id": entity.id,
                    "type": entity.type,
                    "name": entity.name,
                    "metadata": entity.metadata,
                }
                for entity in self.entities
            ],
            "edges": [
                {
                    "source": edge.source,
                    "relation": edge.relation,
                    "target": edge.target,
                    "memoryId": edge.memory_id,
                    "confidence": edge.confidence,
                }
                for edge in self.edges
            ],
        }


class KnowledgeGraph(Protocol):
    def index_memory(self, item: MemoryItem) -> None: ...

    def snapshot(self) -> GraphSnapshot: ...


class SQLiteKnowledgeGraph:
    def __init__(self, store: SQLiteMemoryStore, project_id: str) -> None:
        self.store = store
        self.project_id = project_id

    def index_memory(self, item: MemoryItem) -> None:
        self.store.index_memory_graph(item)

    def snapshot(self) -> GraphSnapshot:
        raw = self.store.project_graph(self.project_id)
        return GraphSnapshot(
            entities=[
                GraphEntity(
                    id=str(entity["id"]),
                    type=str(entity["type"]),
                    name=str(entity["name"]),
                    metadata=dict(entity.get("metadata") or {}),
                )
                for entity in raw["entities"]
            ],
            edges=[
                GraphEdge(
                    source=str(edge["source"]),
                    relation=str(edge["relation"]),
                    target=str(edge["target"]),
                    memory_id=(
                        str(edge["memoryId"]) if edge.get("memoryId") is not None else None
                    ),
                    confidence=float(edge["confidence"]),
                )
                for edge in raw["edges"]
            ],
        )
