from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tinycoder.memory.embeddings import LocalHashEmbeddingProvider
from tinycoder.memory.service import MemoryService
from tinycoder.memory.settings import MemorySettings


def completed_turn(user: str, assistant: str = "Done.") -> list[dict[str, object]]:
    return [
        {"role": "system", "content": "system"},
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


class MemoryExtractionLifecycleTests(unittest.TestCase):
    def test_explicit_memory_is_active_and_extraction_job_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = MemoryService(
                project,
                store_path=root / "memory.db",
                settings=MemorySettings(mode="suggest"),
            )
            try:
                first = service.capture_turn(
                    completed_turn("Please remember that this project uses unittest."),
                    session_id="s1",
                    event_id="s1:3",
                )
                second = service.capture_turn(
                    completed_turn("Please remember that this project uses unittest."),
                    session_id="s1",
                    event_id="s1:3",
                )
                items = service.list()
            finally:
                service.close()

        self.assertEqual(first.stored, 1)
        self.assertFalse(first.skipped)
        self.assertTrue(second.skipped)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, "active")
        self.assertIn("unittest", items[0].content)

    def test_internal_continuation_does_not_hide_original_user_directive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = MemoryService(
                project,
                store_path=root / "memory.db",
                settings=MemorySettings(mode="suggest"),
            )
            try:
                report = service.capture_turn(
                    [
                        {
                            "role": "user",
                            "content": "Remember that release checks use unittest.",
                        },
                        {"role": "assistant_progress", "content": "Checking."},
                        {
                            "role": "user",
                            "content": "Continue immediately.",
                            "synthetic": True,
                            "contextKind": "agent_recovery",
                        },
                        {"role": "assistant", "content": "Done."},
                    ],
                    session_id="s1",
                    event_id="event-1",
                )
                item = service.list()[0]
            finally:
                service.close()

        self.assertEqual(report.stored, 1)
        self.assertIn("release checks use unittest", item.content)

    def test_implicit_preference_requires_review_in_suggest_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = MemoryService(
                project,
                store_path=root / "memory.db",
                settings=MemorySettings(mode="suggest"),
            )
            try:
                report = service.capture_turn(
                    completed_turn("I prefer concise answers with a short test summary."),
                    session_id="s1",
                    event_id="s1:3",
                )
                item = service.list()[0]
                before = service.recall("concise test summary")
                service.set_status(item.id, "active", reason="user approved")
                after = service.recall("concise test summary")
                history = service.history(item.id)
                status = service.status()
            finally:
                service.close()

        self.assertEqual(report.pending_review, 1)
        self.assertEqual(item.status, "pending_review")
        self.assertEqual(before.results, [])
        self.assertEqual(len(after.results), 1)
        self.assertTrue(any(entry["operation"] == "status" for entry in history))
        self.assertEqual(status["telemetry"]["itemsByStatus"]["active"], 1)
        self.assertGreaterEqual(status["telemetry"]["retrievalCount"], 1)

    def test_progress_only_and_secret_directives_are_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = MemoryService(
                project,
                store_path=root / "memory.db",
                settings=MemorySettings(mode="suggest"),
            )
            try:
                progress = service.capture_turn(
                    [
                        {"role": "user", "content": "Remember that we use pytest."},
                        {"role": "assistant_progress", "content": "Still working."},
                    ],
                    session_id="s1",
                    event_id="s1:2",
                )
                secret = service.capture_turn(
                    completed_turn("Remember that api_key=sk-test-12345678901234567890"),
                    session_id="s1",
                    event_id="s1:5",
                )
                items = service.list()
            finally:
                service.close()

        self.assertTrue(progress.skipped)
        self.assertEqual(secret.rejected, 1)
        self.assertEqual(items, [])

    def test_successful_test_tool_call_becomes_a_reviewable_procedure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = MemoryService(
                project,
                store_path=root / "memory.db",
                settings=MemorySettings(mode="suggest"),
            )
            try:
                report = service.capture_turn(
                    [
                        {"role": "user", "content": "Run the test suite."},
                        {
                            "role": "assistant_tool_call",
                            "toolUseId": "tool-1",
                            "toolName": "run_command",
                            "input": {
                                "command": "python",
                                "args": ["-m", "unittest", "discover"],
                            },
                        },
                        {
                            "role": "tool_result",
                            "toolUseId": "tool-1",
                            "toolName": "run_command",
                            "content": "53 tests passed",
                            "isError": False,
                        },
                        {"role": "assistant", "content": "All tests passed."},
                    ],
                    session_id="s1",
                    event_id="s1:4",
                )
                item = service.list()[0]
            finally:
                service.close()

        self.assertEqual(report.pending_review, 1)
        self.assertEqual(item.kind, "procedure")
        self.assertEqual(item.canonical_key, "project.test.command")
        self.assertIn("python -m unittest discover", item.content)

    def test_conflict_resolution_activates_winner_and_supersedes_loser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = MemoryService(
                project,
                store_path=root / "memory.db",
                settings=MemorySettings(mode="suggest"),
            )
            try:
                first, _ = service.add(
                    scope="project_local",
                    kind="fact",
                    canonical_key="project.python.version",
                    content="The project uses Python 3.11.",
                )
                second, outcome = service.add(
                    scope="project_local",
                    kind="fact",
                    canonical_key="project.python.version",
                    content="The project uses Python 3.13.",
                )
                resolved = service.resolve_conflict(second.id)
                winner = service.get(second.id)
                loser = service.get(first.id)
            finally:
                service.close()

        self.assertEqual(outcome.action, "conflict")
        self.assertEqual(resolved, 1)
        self.assertEqual(winner.status, "active")
        self.assertEqual(loser.status, "superseded")

    def test_unreviewed_conflict_does_not_deactivate_existing_active_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = MemoryService(
                project,
                store_path=root / "memory.db",
                settings=MemorySettings(mode="suggest"),
            )
            try:
                trusted, _ = service.add(
                    scope="project_local",
                    kind="procedure",
                    canonical_key="project.test.command",
                    content="Verified test command: python -m unittest discover",
                )
                candidate, outcome = service.add(
                    scope="project_local",
                    kind="procedure",
                    canonical_key="project.test.command",
                    content="Verified test command: pytest",
                    status="pending_review",
                    confidence=0.7,
                )
                trusted_after = service.get(trusted.id)
                candidate_after = service.get(candidate.id)
                recalled = service.recall("project test command unittest")
            finally:
                service.close()

        self.assertEqual(outcome.action, "conflict")
        self.assertEqual(trusted_after.status, "active")
        self.assertEqual(candidate_after.status, "disputed")
        self.assertEqual([result.item.id for result in recalled.results], [trusted.id])

    def test_export_and_import_preserve_visible_memory_without_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_project = root / "project"
            first_project.mkdir()
            first = MemoryService(
                first_project,
                store_path=root / "first.db",
                settings=MemorySettings(mode="suggest"),
            )
            second = MemoryService(
                first_project,
                store_path=root / "second.db",
                settings=MemorySettings(mode="suggest"),
            )
            try:
                first.add(
                    scope="project_local",
                    kind="procedure",
                    canonical_key="project.test.command",
                    content="Run python -m unittest.",
                )
                payload = first.export_json()
                imported = second.import_json(payload)
                item = second.list()[0]
            finally:
                first.close()
                second.close()

        self.assertEqual(imported, 1)
        self.assertEqual(json.loads(payload)["schemaVersion"], 1)
        self.assertEqual(item.canonical_key, "project.test.command")


class MemoryExtensionTests(unittest.TestCase):
    def test_embedding_failure_degrades_to_lexical_memory(self) -> None:
        class FailingEmbeddingProvider:
            name = "failing"
            dimensions = 64
            is_external = False

            def embed(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("embedding unavailable")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = MemoryService(
                project,
                store_path=root / "memory.db",
                settings=MemorySettings(mode="auto", embedding_enabled=True),
                embedding_provider=FailingEmbeddingProvider(),
            )
            try:
                item, _ = service.add(
                    scope="project_local",
                    kind="fact",
                    canonical_key="project.test.framework",
                    content="The project test framework is unittest.",
                )
                recall = service.recall("project test framework unittest")
                status = service.status()
            finally:
                service.close()

        self.assertEqual(recall.results[0].item.id, item.id)
        self.assertGreaterEqual(status["extensionFailures"]["embedding"], 2)

    def test_session_scope_and_shared_sensitivity_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = MemoryService(
                project,
                store_path=root / "memory.db",
                settings=MemorySettings(mode="suggest"),
            )
            try:
                service.add(
                    scope="session",
                    kind="fact",
                    canonical_key="session.current.target",
                    content="The current target is module alpha.",
                    source_session_id="session-a",
                )
                visible = service.recall("current target module", session_id="session-a")
                hidden = service.recall("current target module", session_id="session-b")
                shared, _ = service.add(
                    scope="project_shared",
                    kind="procedure",
                    canonical_key="project.test.command",
                    content="Run the shared test command.",
                )
                with self.assertRaises(ValueError):
                    service.add(
                        scope="project_shared",
                        kind="fact",
                        canonical_key="project.private.note",
                        content="A private note.",
                        sensitivity="private",
                    )
            finally:
                service.close()

        self.assertEqual(len(visible.results), 1)
        self.assertEqual(hidden.results, [])
        self.assertEqual(shared.sensitivity, "team")

    def test_local_embedding_and_graph_extensions_are_available_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = MemoryService(
                project,
                store_path=root / "memory.db",
                settings=MemorySettings(
                    mode="auto",
                    embedding_enabled=True,
                    graph_enabled=True,
                ),
                embedding_provider=LocalHashEmbeddingProvider(dimensions=64),
            )
            try:
                service.add(
                    scope="project_local",
                    kind="fact",
                    canonical_key="project.frontend.framework",
                    content="The dashboard is implemented with React.",
                    source_uri="repo:package.json",
                )
                recall = service.recall("Which framework implements the dashboard?")
                graph = service.project_graph()
                status = service.status()
            finally:
                service.close()

        self.assertEqual(len(recall.results), 1)
        self.assertIn(recall.results[0].reason, {"lexical", "fts", "vector", "hybrid"})
        self.assertTrue(any(edge["relation"] == "contains_memory" for edge in graph["edges"]))
        self.assertEqual(status["embeddingProvider"], "local-hash-v1")
