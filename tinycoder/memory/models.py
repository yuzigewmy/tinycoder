from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


MemoryScope = Literal["managed", "user", "project_shared", "project_local", "session"]
MemoryKind = Literal["preference", "fact", "decision", "procedure", "episode", "warning"]
MemoryStatus = Literal[
    "active",
    "pending_review",
    "disputed",
    "superseded",
    "stale",
    "expired",
    "quarantined",
    "deleted",
]
Sensitivity = Literal["public", "team", "private", "confidential", "secret_forbidden"]

VALID_SCOPES = {"managed", "user", "project_shared", "project_local", "session"}
VALID_KINDS = {"preference", "fact", "decision", "procedure", "episode", "warning"}
VALID_STATUSES = {
    "active",
    "pending_review",
    "disputed",
    "superseded",
    "stale",
    "expired",
    "quarantined",
    "deleted",
}
VALID_SENSITIVITIES = {"public", "team", "private", "confidential", "secret_forbidden"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryItem:
    id: str
    project_id: str | None
    scope: MemoryScope
    kind: MemoryKind
    canonical_key: str
    content: str
    confidence: float
    status: MemoryStatus = "active"
    sensitivity: Sensitivity = "private"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    last_verified_at: str | None = None
    expires_at: str | None = None
    source_session_id: str | None = None
    source_event_id: str | None = None
    source_uri: str | None = None
    source_hash: str | None = None
    extractor_version: str | None = None
    revision: int = 1

    def __post_init__(self) -> None:
        self.canonical_key = self.canonical_key.strip()
        self.content = self.content.strip()
        if not self.id:
            raise ValueError("memory id is required")
        if not self.canonical_key:
            raise ValueError("canonical_key is required")
        if not self.content:
            raise ValueError("memory content is required")
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"invalid memory scope: {self.scope}")
        if self.kind not in VALID_KINDS:
            raise ValueError(f"invalid memory kind: {self.kind}")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid memory status: {self.status}")
        if self.sensitivity not in VALID_SENSITIVITIES:
            raise ValueError(f"invalid memory sensitivity: {self.sensitivity}")
        if not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.scope in {"project_shared", "project_local", "session"} and not self.project_id:
            raise ValueError(f"project_id is required for scope={self.scope}")

    @classmethod
    def create(
        cls,
        *,
        project_id: str | None,
        scope: MemoryScope,
        kind: MemoryKind,
        canonical_key: str,
        content: str,
        confidence: float,
        status: MemoryStatus = "active",
        sensitivity: Sensitivity = "private",
        expires_at: str | None = None,
        source_session_id: str | None = None,
        source_event_id: str | None = None,
        source_uri: str | None = None,
        source_hash: str | None = None,
        extractor_version: str | None = None,
    ) -> "MemoryItem":
        return cls(
            id=str(uuid.uuid4()),
            project_id=project_id,
            scope=scope,
            kind=kind,
            canonical_key=canonical_key,
            content=content,
            confidence=float(confidence),
            status=status,
            sensitivity=sensitivity,
            expires_at=expires_at,
            source_session_id=source_session_id,
            source_event_id=source_event_id,
            source_uri=source_uri,
            source_hash=source_hash,
            extractor_version=extractor_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


@dataclass
class MemoryQuery:
    project_id: str
    user_text: str
    session_id: str | None = None
    recent_messages: list[str] = field(default_factory=list)
    active_paths: list[str] = field(default_factory=list)
    active_symbols: list[str] = field(default_factory=list)
    task_kind: str | None = None
    max_items: int = 8
    max_tokens: int = 1500

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("project_id is required")
        self.user_text = str(self.user_text or "").strip()
        self.max_items = max(1, min(int(self.max_items), 20))
        self.max_tokens = max(128, min(int(self.max_tokens), 8_000))


@dataclass(frozen=True)
class MemorySearchResult:
    item: MemoryItem
    score: float
    reason: str


@dataclass(frozen=True)
class UpsertOutcome:
    action: Literal["inserted", "merged", "conflict"]
    item_id: str
    related_id: str | None = None
