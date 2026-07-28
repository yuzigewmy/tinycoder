from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import MemoryItem, MemoryQuery, MemorySearchResult, UpsertOutcome, utc_now
from .policy import validate_memory_content


SCHEMA_VERSION = 1
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
        self._migrate()
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
                same.updated_at = utc_now()
                same.last_verified_at = item.last_verified_at or same.last_verified_at
                same.revision += 1
                self._update(same)
                self._audit("merge", same.id, before=before, after=same)
                return UpsertOutcome("merged", same.id)

            conflicting = next((self._row_to_item(row) for row in existing_rows), None)
            if conflicting:
                assert conflicting is not None
                before = MemoryItem(**conflicting.to_dict())
                conflicting.status = "disputed"
                conflicting.updated_at = utc_now()
                conflicting.revision += 1
                item.status = "disputed"
                self._update(conflicting)
                self._insert(item)
                now = utc_now()
                self._connection.executemany(
                    """
                    INSERT OR IGNORE INTO memory_relations
                    (source_memory_id, relation_type, target_memory_id, created_at)
                    VALUES (?, 'contradicts', ?, ?)
                    """,
                    ((item.id, conflicting.id, now), (conflicting.id, item.id, now)),
                )
                self._audit("dispute", conflicting.id, before=before, after=conflicting)
                self._audit("insert_disputed", item.id, after=item)
                return UpsertOutcome("conflict", item.id, conflicting.id)

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

    def _scope_rows(self, project_id: str) -> list[sqlite3.Row]:
        return self._connection.execute(
            """
            SELECT * FROM memory_items
            WHERE status = 'active'
              AND (
                project_id = ?
                OR (project_id IS NULL AND scope IN ('managed', 'user'))
              )
            """,
            (project_id,),
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
        rows = self._scope_rows(query.project_id)
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
