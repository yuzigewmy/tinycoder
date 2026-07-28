from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tinycoder.memory.identity import normalize_git_remote, resolve_project_identity
from tinycoder.memory.models import MemoryItem, MemoryQuery
from tinycoder.memory.policy import MemoryPolicyError, detect_secret, validate_memory_content
from tinycoder.memory.sqlite_store import SQLiteMemoryStore


class MemoryModelTests(unittest.TestCase):
    def test_memory_item_validates_confidence_and_required_fields(self) -> None:
        with self.assertRaises(ValueError):
            MemoryItem.create(
                project_id="project-a",
                scope="project_local",
                kind="fact",
                canonical_key="",
                content="Uses unittest.",
                confidence=0.9,
            )
        with self.assertRaises(ValueError):
            MemoryItem.create(
                project_id="project-a",
                scope="project_local",
                kind="fact",
                canonical_key="project.expiry",
                content="Temporary fact.",
                confidence=0.9,
                expires_at="not-a-timestamp",
            )
        with self.assertRaises(ValueError):
            MemoryItem.create(
                project_id="project-a",
                scope="project_local",
                kind="fact",
                canonical_key="project.test.framework",
                content="Uses unittest.",
                confidence=1.1,
            )
        with self.assertRaises(ValueError):
            MemoryItem.create(
                project_id="project-a",
                scope="project_local",
                kind="fact",
                canonical_key="x" * 257,
                content="Uses unittest.",
                confidence=0.9,
            )

    def test_query_applies_safe_recall_limits(self) -> None:
        query = MemoryQuery(
            project_id="project-a",
            user_text="How do I run tests?",
            max_items=999,
            max_tokens=999_999,
        )
        self.assertEqual(query.max_items, 20)
        self.assertEqual(query.max_tokens, 8_000)


class MemoryIdentityTests(unittest.TestCase):
    def test_remote_normalization_removes_credentials_and_protocol_differences(self) -> None:
        https = normalize_git_remote("https://token@example.com/acme/tinycoder.git")
        ssh = normalize_git_remote("git@example.com:acme/tinycoder.git")
        self.assertEqual(https, "example.com/acme/tinycoder")
        self.assertEqual(ssh, "example.com/acme/tinycoder")
        self.assertNotIn("token", https)

    def test_non_git_workspaces_have_stable_distinct_ids(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_identity = resolve_project_identity(first)
            repeated_identity = resolve_project_identity(first)
            second_identity = resolve_project_identity(second)
        self.assertEqual(first_identity.project_id, repeated_identity.project_id)
        self.assertNotEqual(first_identity.project_id, second_identity.project_id)
        self.assertEqual(first_identity.source, "path")


class MemoryPolicyTests(unittest.TestCase):
    def test_secret_material_is_rejected_before_persistence(self) -> None:
        secret = "ANTHROPIC_API_KEY=sk-ant-" + ("a" * 32)
        self.assertIsNotNone(detect_secret(secret))
        with self.assertRaises(MemoryPolicyError):
            validate_memory_content(secret)

    def test_normal_project_fact_is_allowed(self) -> None:
        self.assertEqual(
            validate_memory_content("Run tests with python -m unittest discover."),
            "Run tests with python -m unittest discover.",
        )


class SQLiteMemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteMemoryStore(Path(self.tempdir.name) / "memory.db")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_store_refuses_to_downgrade_a_future_schema(self) -> None:
        path = Path(self.tempdir.name) / "future.db"
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE memory_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO memory_metadata(key, value) VALUES('schema_version', '999')"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(RuntimeError):
            SQLiteMemoryStore(path)

    def test_upsert_persists_and_searches_only_the_requested_project(self) -> None:
        item = MemoryItem.create(
            project_id="project-a",
            scope="project_local",
            kind="fact",
            canonical_key="project.test.framework",
            content="Tests use unittest discovery.",
            confidence=0.88,
        )
        self.store.upsert(item)
        self.store.upsert(
            MemoryItem.create(
                project_id="project-b",
                scope="project_local",
                kind="fact",
                canonical_key="project.test.framework",
                content="Tests use pytest.",
                confidence=0.9,
            )
        )

        reopened = SQLiteMemoryStore(self.store.path)
        try:
            results = reopened.search(
                MemoryQuery(project_id="project-a", user_text="How are tests run?")
            )
        finally:
            reopened.close()

        self.assertEqual([result.item.id for result in results], [item.id])

    def test_same_scope_conflict_is_disputed_instead_of_overwritten(self) -> None:
        first = MemoryItem.create(
            project_id="project-a",
            scope="project_local",
            kind="fact",
            canonical_key="project.package_manager",
            content="Use npm.",
            confidence=0.9,
        )
        second = MemoryItem.create(
            project_id="project-a",
            scope="project_local",
            kind="fact",
            canonical_key="project.package_manager",
            content="Use pnpm.",
            confidence=0.9,
        )
        self.store.upsert(first)
        outcome = self.store.upsert(second)

        self.assertEqual(outcome.action, "conflict")
        self.assertEqual(self.store.get(first.id).status, "disputed")
        self.assertEqual(self.store.get(second.id).status, "disputed")
        self.assertEqual(
            self.store.relations(second.id, relation_type="contradicts"),
            [first.id],
        )

    def test_expired_and_deleted_memories_are_not_recalled(self) -> None:
        expired = MemoryItem.create(
            project_id="project-a",
            scope="project_local",
            kind="episode",
            canonical_key="debug.old_failure",
            content="An old build failure.",
            confidence=0.8,
            expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        )
        active = MemoryItem.create(
            project_id="project-a",
            scope="project_local",
            kind="procedure",
            canonical_key="project.build.command",
            content="Build with python -m build.",
            confidence=0.9,
        )
        self.store.upsert(expired)
        self.store.upsert(active)
        self.store.delete(active.id, reason="user requested")

        results = self.store.search(
            MemoryQuery(project_id="project-a", user_text="build failure command")
        )

        self.assertEqual(results, [])
        self.assertEqual(self.store.get(expired.id).status, "expired")
        self.assertEqual(self.store.get(active.id).status, "deleted")
