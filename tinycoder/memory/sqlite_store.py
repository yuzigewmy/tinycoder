from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .embeddings import cosine_similarity
from .models import (
    VALID_STATUSES,
    MemoryItem,
    MemoryQuery,
    MemorySearchResult,
    UpsertOutcome,
    utc_now,
)
from .policy import validate_memory_content


SCHEMA_VERSION = 2
_ITEM_COLUMNS = (
    "id",
    "project_id",
    "scope",
    "kind",
    "canonical_key",
    "content",
    "confidence",
    "status",
    "sensitivity",
    "created_at",
    "updated_at",
    "last_verified_at",
    "expires_at",
    "source_session_id",
    "source_event_id",
    "source_uri",
    "source_hash",
    "extractor_version",
    "revision",
)


class SQLiteMemoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), timeout=5)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self.fts_enabled = False
        try:
            self._migrate()
        except Exception:
            self._connection.close()
            raise
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        with self._connection:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS memory_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            version_row = self._connection.execute(
                "SELECT value FROM memory_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if version_row:
                try:
                    existing_version = int(version_row["value"])
                except (TypeError, ValueError) as error:
                    raise RuntimeError("invalid memory schema version") from error
                if existing_version > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"memory schema {existing_version} is newer than supported {SCHEMA_VERSION}"
                    )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    scope TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_verified_at TEXT,
                    expires_at TEXT,
                    source_session_id TEXT,
                    source_event_id TEXT,
                    source_uri TEXT,
                    source_hash TEXT,
                    extractor_version TEXT,
                    revision INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS memory_lookup_idx
                ON memory_items(project_id, scope, canonical_key, status)
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_relations (
                    source_memory_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    target_memory_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(source_memory_id, relation_type, target_memory_id),
                    FOREIGN KEY(source_memory_id) REFERENCES memory_items(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_memory_id) REFERENCES memory_items(id) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    source_uri TEXT,
                    excerpt_hash TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES memory_items(id) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_usage (
                    memory_id TEXT PRIMARY KEY,
                    retrieval_count INTEGER NOT NULL DEFAULT 0,
                    last_retrieved_at TEXT,
                    last_query_hash TEXT,
                    FOREIGN KEY(memory_id) REFERENCES memory_items(id) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_extraction_jobs (
                    job_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(memory_id, provider),
                    FOREIGN KEY(memory_id) REFERENCES memory_items(id) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_graph_entities (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, entity_type, normalized_name)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_graph_edges (
                    source_entity_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_entity_id TEXT NOT NULL,
                    memory_id TEXT,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(source_entity_id, relation, target_entity_id, memory_id),
                    FOREIGN KEY(source_entity_id) REFERENCES memory_graph_entities(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_entity_id) REFERENCES memory_graph_entities(id) ON DELETE CASCADE,
                    FOREIGN KEY(memory_id) REFERENCES memory_items(id) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO memory_metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            try:
                self._connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                    USING fts5(memory_id UNINDEXED, canonical_key, content, tokenize='unicode61')
                    """
                )
                self.fts_enabled = True
            except sqlite3.OperationalError:
                self.fts_enabled = False

    def _row_to_item(self, row: sqlite3.Row | None) -> MemoryItem | None:
        if row is None:
            return None
        return MemoryItem(**{column: row[column] for column in _ITEM_COLUMNS})

    def _audit(
        self,
        operation: str,
        memory_id: str,
        *,
        before: MemoryItem | None = None,
        after: MemoryItem | None = None,
        reason: str | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO memory_audit(operation, memory_id, before_json, after_json, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                operation,
                memory_id,
                json.dumps(before.to_dict(), ensure_ascii=False) if before else None,
                json.dumps(after.to_dict(), ensure_ascii=False) if after else None,
                reason,
                utc_now(),
            ),
        )

    def _write_fts(self, item: MemoryItem) -> None:
        if not self.fts_enabled:
            return
        self._connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (item.id,))
        if item.status == "active":
            self._connection.execute(
                "INSERT INTO memory_fts(memory_id, canonical_key, content) VALUES (?, ?, ?)",
                (item.id, item.canonical_key, item.content),
            )

    def _insert(self, item: MemoryItem) -> None:
        values = tuple(getattr(item, column) for column in _ITEM_COLUMNS)
        placeholders = ", ".join("?" for _ in _ITEM_COLUMNS)
        self._connection.execute(
            f"INSERT INTO memory_items({', '.join(_ITEM_COLUMNS)}) VALUES ({placeholders})",
            values,
        )
        self._write_fts(item)

    def upsert(self, item: MemoryItem) -> UpsertOutcome:
        item.content = validate_memory_content(item.content)
        with self._connection:
            existing_rows = self._connection.execute(
                """
                SELECT * FROM memory_items
                WHERE project_id IS ? AND scope = ? AND canonical_key = ?
                  AND status IN ('active', 'pending_review', 'disputed')
                ORDER BY updated_at DESC
                """,
                (item.project_id, item.scope, item.canonical_key),
            ).fetchall()
            same = next((self._row_to_item(row) for row in existing_rows if row["content"] == item.content), None)
            if same:
                assert same is not None
                before = MemoryItem(**same.to_dict())
                same.confidence = max(same.confidence, item.confidence)
                if same.status == "pending_review" and item.status == "active":
                    same.status = "active"
                same.updated_at = utc_now()
                same.last_verified_at = item.last_verified_at or same.last_verified_at
                same.revision += 1
                self._update(same)
                self._audit("merge", same.id, before=before, after=same)
                return UpsertOutcome("merged", same.id)

            conflicting_items = [
                candidate
                for row in existing_rows
                if (candidate := self._row_to_item(row)) is not None
            ]
            if conflicting_items:
                has_active = any(candidate.status == "active" for candidate in conflicting_items)
                all_pending = all(
                    candidate.status == "pending_review"
                    for candidate in conflicting_items
                )
                if item.status == "pending_review" and has_active:
                    item.status = "disputed"
                elif item.status == "active" and all_pending:
                    for conflicting in conflicting_items:
                        before = MemoryItem(**conflicting.to_dict())
                        conflicting.status = "superseded"
                        conflicting.updated_at = utc_now()
                        conflicting.revision += 1
                        self._update(conflicting)
                        self._audit(
                            "supersede_pending",
                            conflicting.id,
                            before=before,
                            after=conflicting,
                        )
                else:
                    item.status = "disputed"
                    for conflicting in conflicting_items:
                        if conflicting.status == "disputed":
                            continue
                        before = MemoryItem(**conflicting.to_dict())
                        conflicting.status = "disputed"
                        conflicting.updated_at = utc_now()
                        conflicting.revision += 1
                        self._update(conflicting)
                        self._audit(
                            "dispute",
                            conflicting.id,
                            before=before,
                            after=conflicting,
                        )
                self._insert(item)
                now = utc_now()
                self._connection.executemany(
                    """
                    INSERT OR IGNORE INTO memory_relations
                    (source_memory_id, relation_type, target_memory_id, created_at)
                    VALUES (?, 'contradicts', ?, ?)
                    """,
                    [
                        relation
                        for conflicting in conflicting_items
                        for relation in (
                            (item.id, conflicting.id, now),
                            (conflicting.id, item.id, now),
                        )
                    ],
                )
                self._audit("insert_conflict", item.id, after=item)
                return UpsertOutcome(
                    "conflict",
                    item.id,
                    conflicting_items[0].id,
                )

            self._insert(item)
            self._audit("insert", item.id, after=item)
            return UpsertOutcome("inserted", item.id)

    def _update(self, item: MemoryItem) -> None:
        assignments = ", ".join(f"{column} = ?" for column in _ITEM_COLUMNS if column != "id")
        values = tuple(getattr(item, column) for column in _ITEM_COLUMNS if column != "id")
        self._connection.execute(
            f"UPDATE memory_items SET {assignments} WHERE id = ?",
            (*values, item.id),
        )
        self._write_fts(item)

    def get(self, memory_id: str) -> MemoryItem | None:
        row = self._connection.execute(
            "SELECT * FROM memory_items WHERE id = ?",
            (memory_id,),
        ).fetchone()
        return self._row_to_item(row)

    def relations(self, memory_id: str, *, relation_type: str | None = None) -> list[str]:
        if relation_type:
            rows = self._connection.execute(
                """
                SELECT target_memory_id FROM memory_relations
                WHERE source_memory_id = ? AND relation_type = ?
                ORDER BY target_memory_id
                """,
                (memory_id, relation_type),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """
                SELECT target_memory_id FROM memory_relations
                WHERE source_memory_id = ? ORDER BY target_memory_id
                """,
                (memory_id,),
            ).fetchall()
        return [str(row["target_memory_id"]) for row in rows]

    def _expire_due(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rows = self._connection.execute(
            """
            SELECT * FROM memory_items
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (now,),
        ).fetchall()
        for row in rows:
            item = self._row_to_item(row)
            if not item:
                continue
            before = MemoryItem(**item.to_dict())
            item.status = "expired"
            item.updated_at = utc_now()
            item.revision += 1
            self._update(item)
            self._audit("expire", item.id, before=before, after=item)

    def _scope_rows(
        self,
        project_id: str,
        session_id: str | None,
    ) -> list[sqlite3.Row]:
        return self._connection.execute(
            """
            SELECT * FROM memory_items
            WHERE status = 'active'
              AND (
                project_id = ?
                OR (project_id IS NULL AND scope IN ('managed', 'user'))
              )
              AND (scope != 'session' OR source_session_id = ?)
            ORDER BY updated_at DESC
            LIMIT 2000
            """,
            (project_id, session_id),
        ).fetchall()

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {token.casefold() for token in re.findall(r"[\w.-]{2,}", value)}

    def _fts_ids(self, query: MemoryQuery) -> set[str]:
        if not self.fts_enabled:
            return set()
        tokens = sorted(self._tokens(query.user_text))
        if not tokens:
            return set()
        expression = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:20])
        try:
            rows = self._connection.execute(
                "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ? LIMIT 100",
                (expression,),
            ).fetchall()
        except sqlite3.OperationalError:
            return set()
        return {str(row["memory_id"]) for row in rows}

    def search(self, query: MemoryQuery) -> list[MemorySearchResult]:
        with self._connection:
            self._expire_due()
        rows = self._scope_rows(query.project_id, query.session_id)
        fts_ids = self._fts_ids(query)
        query_tokens = self._tokens(
            " ".join([query.user_text, *query.recent_messages, *query.active_paths, *query.active_symbols])
        )
        results: list[MemorySearchResult] = []
        for row in rows:
            item = self._row_to_item(row)
            if not item:
                continue
            item_tokens = self._tokens(f"{item.canonical_key} {item.content}")
            overlap = len(query_tokens & item_tokens) / max(1, len(query_tokens))
            exact = 1.0 if item.canonical_key.casefold() in query.user_text.casefold() else 0.0
            fts = 1.0 if item.id in fts_ids else 0.0
            if query_tokens and not (overlap or exact or fts):
                continue
            scope_score = 1.0 if item.project_id == query.project_id else 0.75
            score = 0.35 * max(overlap, fts) + 0.25 * exact + 0.25 * item.confidence + 0.15 * scope_score
            reason = "exact" if exact else "fts" if fts else "lexical"
            results.append(MemorySearchResult(item=item, score=round(score, 6), reason=reason))
        results.sort(key=lambda result: (-result.score, -result.item.confidence, result.item.id))
        return results[: query.max_items]

    def list(
        self,
        *,
        project_id: str,
        statuses: Iterable[str] | None = None,
        scope: str | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        statuses = tuple(statuses or ("active", "pending_review", "disputed", "stale", "expired"))
        placeholders = ", ".join("?" for _ in statuses)
        clauses = [
            f"status IN ({placeholders})",
            "(project_id = ? OR (project_id IS NULL AND scope IN ('managed', 'user')))",
        ]
        params: list[Any] = [*statuses, project_id]
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        params.append(max(1, min(int(limit), 500)))
        rows = self._connection.execute(
            f"SELECT * FROM memory_items WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [item for row in rows if (item := self._row_to_item(row))]

    def delete(self, memory_id: str, *, reason: str) -> bool:
        with self._connection:
            item = self.get(memory_id)
            if not item or item.status == "deleted":
                return False
            before = MemoryItem(**item.to_dict())
            item.status = "deleted"
            item.updated_at = utc_now()
            item.revision += 1
            self._update(item)
            self._audit("delete", item.id, before=before, after=item, reason=reason)
            return True

    def set_status(self, memory_id: str, status: str, *, reason: str) -> bool:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid memory status: {status}")
        with self._connection:
            item = self.get(memory_id)
            if not item:
                return False
            before = MemoryItem(**item.to_dict())
            item.status = status  # type: ignore[assignment]
            item.updated_at = utc_now()
            item.revision += 1
            self._update(item)
            self._audit("status", item.id, before=before, after=item, reason=reason)
            return True

    def resolve_conflict(self, winner_id: str, *, reason: str) -> int:
        with self._connection:
            winner = self.get(winner_id)
            if not winner or winner.status not in {"active", "disputed", "pending_review"}:
                return 0
            related_ids = self.relations(winner_id, relation_type="contradicts")
            losers = [
                item
                for related_id in related_ids
                if (item := self.get(related_id)) is not None
                and item.status in {"active", "disputed", "pending_review"}
            ]
            if not losers:
                return 0
            if winner.status != "active":
                winner_before = MemoryItem(**winner.to_dict())
                winner.status = "active"
                winner.updated_at = utc_now()
                winner.revision += 1
                self._update(winner)
                self._audit(
                    "resolve_winner",
                    winner.id,
                    before=winner_before,
                    after=winner,
                    reason=reason,
                )
            for loser in losers:
                loser_before = MemoryItem(**loser.to_dict())
                loser.status = "superseded"
                loser.updated_at = utc_now()
                loser.revision += 1
                self._update(loser)
                self._audit(
                    "resolve_loser",
                    loser.id,
                    before=loser_before,
                    after=loser,
                    reason=reason,
                )
            return len(losers)

    def audit_history(self, memory_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT operation, memory_id, before_json, after_json, reason, created_at
            FROM memory_audit WHERE memory_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (memory_id, max(1, min(int(limit), 500))),
        ).fetchall()
        return [
            {
                "operation": row["operation"],
                "memoryId": row["memory_id"],
                "before": json.loads(row["before_json"]) if row["before_json"] else None,
                "after": json.loads(row["after_json"]) if row["after_json"] else None,
                "reason": row["reason"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def add_evidence(
        self,
        memory_id: str,
        *,
        evidence_type: str,
        source_uri: str | None,
        excerpt_hash: str | None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO memory_evidence(
                    memory_id, evidence_type, source_uri, excerpt_hash, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (memory_id, evidence_type, source_uri, excerpt_hash, utc_now()),
            )

    def begin_extraction_job(
        self,
        job_key: str,
        *,
        project_id: str,
        session_id: str | None,
        max_attempts: int = 3,
    ) -> bool:
        now = utc_now()
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO memory_extraction_jobs(
                    job_key, project_id, session_id, state, attempts,
                    error, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', 0, NULL, ?, ?)
                """,
                (job_key, project_id, session_id, now, now),
            )
            row = self._connection.execute(
                "SELECT state, attempts FROM memory_extraction_jobs WHERE job_key = ?",
                (job_key,),
            ).fetchone()
            if not row or row["state"] == "completed" or int(row["attempts"]) >= max_attempts:
                return False
            self._connection.execute(
                """
                UPDATE memory_extraction_jobs
                SET state = 'running', attempts = attempts + 1,
                    error = NULL, updated_at = ?
                WHERE job_key = ?
                """,
                (now, job_key),
            )
            return True

    def complete_extraction_job(self, job_key: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE memory_extraction_jobs
                SET state = 'completed', error = NULL, updated_at = ?
                WHERE job_key = ?
                """,
                (utc_now(), job_key),
            )

    def fail_extraction_job(self, job_key: str, error: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE memory_extraction_jobs
                SET state = 'failed', error = ?, updated_at = ?
                WHERE job_key = ?
                """,
                (str(error)[:2_000], utc_now(), job_key),
            )

    def record_retrieval(self, memory_ids: list[str], *, query_hash: str) -> None:
        now = utc_now()
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO memory_usage(
                    memory_id, retrieval_count, last_retrieved_at, last_query_hash
                ) VALUES (?, 1, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    retrieval_count = retrieval_count + 1,
                    last_retrieved_at = excluded.last_retrieved_at,
                    last_query_hash = excluded.last_query_hash
                """,
                ((memory_id, now, query_hash) for memory_id in memory_ids),
            )

    def save_embedding(
        self,
        memory_id: str,
        *,
        provider: str,
        vector: list[float],
        content_hash: str,
    ) -> None:
        if not vector or len(vector) > 4_096 or any(not math.isfinite(value) for value in vector):
            raise ValueError("embedding vector must contain 1..4096 finite values")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO memory_embeddings(
                    memory_id, provider, dimensions, vector_json,
                    content_hash, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id, provider) DO UPDATE SET
                    dimensions = excluded.dimensions,
                    vector_json = excluded.vector_json,
                    content_hash = excluded.content_hash,
                    updated_at = excluded.updated_at
                """,
                (
                    memory_id,
                    provider,
                    len(vector),
                    json.dumps(vector, separators=(",", ":")),
                    content_hash,
                    utc_now(),
                ),
            )

    def vector_search(
        self,
        query: MemoryQuery,
        *,
        provider: str,
        vector: list[float],
    ) -> list[MemorySearchResult]:
        rows = self._connection.execute(
            """
            SELECT i.*, e.vector_json
            FROM memory_embeddings e
            JOIN memory_items i ON i.id = e.memory_id
            WHERE e.provider = ? AND i.status = 'active'
              AND (
                i.project_id = ?
                OR (i.project_id IS NULL AND i.scope IN ('managed', 'user'))
              )
              AND (i.scope != 'session' OR i.source_session_id = ?)
            ORDER BY i.updated_at DESC
            LIMIT 2000
            """,
            (provider, query.project_id, query.session_id),
        ).fetchall()
        results: list[MemorySearchResult] = []
        for row in rows:
            item = self._row_to_item(row)
            if not item:
                continue
            try:
                candidate_vector = [float(value) for value in json.loads(row["vector_json"])]
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            similarity = max(0.0, cosine_similarity(vector, candidate_vector))
            if similarity <= 0:
                continue
            results.append(
                MemorySearchResult(
                    item=item,
                    score=round(0.75 * similarity + 0.25 * item.confidence, 6),
                    reason="vector",
                )
            )
        results.sort(key=lambda result: (-result.score, result.item.id))
        return results[: query.max_items]

    def _upsert_graph_entity(
        self,
        *,
        entity_id: str,
        project_id: str,
        entity_type: str,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        now = utc_now()
        normalized = " ".join(name.casefold().split())
        self._connection.execute(
            """
            INSERT INTO memory_graph_entities(
                id, project_id, entity_type, name, normalized_name,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, entity_type, normalized_name) DO UPDATE SET
                name = excluded.name,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                entity_id,
                project_id,
                entity_type,
                name,
                normalized,
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        row = self._connection.execute(
            """
            SELECT id FROM memory_graph_entities
            WHERE project_id = ? AND entity_type = ? AND normalized_name = ?
            """,
            (project_id, entity_type, normalized),
        ).fetchone()
        return str(row["id"])

    def index_memory_graph(self, item: MemoryItem) -> None:
        if not item.project_id:
            return
        import hashlib

        def entity_id(entity_type: str, name: str) -> str:
            material = f"{item.project_id}\0{entity_type}\0{name.casefold()}"
            return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

        with self._connection:
            project_entity = self._upsert_graph_entity(
                entity_id=entity_id("project", item.project_id),
                project_id=item.project_id,
                entity_type="project",
                name=item.project_id,
            )
            memory_entity = self._upsert_graph_entity(
                entity_id=entity_id("memory_key", item.canonical_key),
                project_id=item.project_id,
                entity_type="memory_key",
                name=item.canonical_key,
                metadata={"kind": item.kind, "scope": item.scope},
            )
            self._connection.execute(
                """
                INSERT OR REPLACE INTO memory_graph_edges(
                    source_entity_id, relation, target_entity_id,
                    memory_id, confidence, created_at
                ) VALUES (?, 'contains_memory', ?, ?, ?, ?)
                """,
                (
                    project_entity,
                    memory_entity,
                    item.id,
                    item.confidence,
                    utc_now(),
                ),
            )

    def project_graph(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        entities = self._connection.execute(
            """
            SELECT id, entity_type, name, metadata_json
            FROM memory_graph_entities WHERE project_id = ?
            ORDER BY entity_type, name
            LIMIT 500
            """,
            (project_id,),
        ).fetchall()
        edges = self._connection.execute(
            """
            SELECT e.source_entity_id, e.relation, e.target_entity_id,
                   e.memory_id, e.confidence
            FROM memory_graph_edges e
            JOIN memory_graph_entities source ON source.id = e.source_entity_id
            JOIN memory_items item ON item.id = e.memory_id
            WHERE source.project_id = ? AND item.status = 'active'
            ORDER BY e.relation, e.source_entity_id, e.target_entity_id
            LIMIT 500
            """,
            (project_id,),
        ).fetchall()
        return {
            "entities": [
                {
                    "id": row["id"],
                    "type": row["entity_type"],
                    "name": row["name"],
                    "metadata": json.loads(row["metadata_json"]),
                }
                for row in entities
            ],
            "edges": [
                {
                    "source": row["source_entity_id"],
                    "relation": row["relation"],
                    "target": row["target_entity_id"],
                    "memoryId": row["memory_id"],
                    "confidence": row["confidence"],
                }
                for row in edges
            ],
        }

    def stats(self, project_id: str) -> dict[str, Any]:
        status_rows = self._connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM memory_items
            WHERE project_id = ?
               OR (project_id IS NULL AND scope IN ('managed', 'user'))
            GROUP BY status
            """,
            (project_id,),
        ).fetchall()
        job_rows = self._connection.execute(
            """
            SELECT state, COUNT(*) AS count
            FROM memory_extraction_jobs
            WHERE project_id = ?
            GROUP BY state
            """,
            (project_id,),
        ).fetchall()
        usage = self._connection.execute(
            """
            SELECT COALESCE(SUM(u.retrieval_count), 0) AS retrievals
            FROM memory_usage u
            JOIN memory_items i ON i.id = u.memory_id
            WHERE i.project_id = ?
               OR (i.project_id IS NULL AND i.scope IN ('managed', 'user'))
            """,
            (project_id,),
        ).fetchone()
        return {
            "itemsByStatus": {
                str(row["status"]): int(row["count"]) for row in status_rows
            },
            "extractionJobsByState": {
                str(row["state"]): int(row["count"]) for row in job_rows
            },
            "retrievalCount": int(usage["retrievals"]) if usage else 0,
        }
