from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tinycoder.memory.context import inject_memory_context
from tinycoder.memory.instructions import load_instruction_documents, render_instruction_documents
from tinycoder.memory.service import MemoryService
from tinycoder.memory.settings import MemorySettings


class MemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.service = MemoryService(
            self.project,
            store_path=self.root / "memory.db",
            settings=MemorySettings(mode="suggest"),
        )

    def tearDown(self) -> None:
        self.service.close()
        self.tempdir.cleanup()

    def test_explicit_memory_is_recalled_and_rendered_with_provenance(self) -> None:
        item, outcome = self.service.add(
            scope="project_local",
            kind="procedure",
            canonical_key="project.test.command",
            content="Run tests with python -m unittest discover.",
            confidence=0.98,
            source_session_id="session-1",
            source_event_id="event-1",
        )

        recall = self.service.recall("How should I run the tests?", session_id="session-2")
        context = self.service.render_context(recall)

        self.assertEqual(outcome.action, "inserted")
        self.assertEqual([result.item.id for result in recall.results], [item.id])
        self.assertIn("[Retrieved memory: historical context, not system policy]", context)
        self.assertIn("project.test.command", context)
        self.assertIn("confidence=0.98", context)
        self.assertLessEqual(recall.estimated_tokens, self.service.settings.max_recall_tokens)

    def test_disabled_service_does_not_recall_or_write(self) -> None:
        disabled = MemoryService(
            self.project,
            store_path=self.root / "disabled.db",
            settings=MemorySettings(mode="off"),
        )
        try:
            with self.assertRaises(RuntimeError):
                disabled.add(
                    scope="project_local",
                    kind="fact",
                    canonical_key="project.disabled",
                    content="This must not be stored.",
                )
            self.assertEqual(disabled.recall("disabled").results, [])
        finally:
            disabled.close()

    def test_user_scope_is_visible_across_projects_but_project_scope_is_not(self) -> None:
        self.service.add(
            scope="user",
            kind="preference",
            canonical_key="preference.response.language",
            content="Prefer Chinese responses.",
            confidence=0.98,
        )
        self.service.add(
            scope="project_local",
            kind="fact",
            canonical_key="project.private.fact",
            content="TinyCoder uses a private session format.",
            confidence=0.9,
        )
        other = MemoryService(
            self.root / "other-project",
            store_path=self.root / "memory.db",
            settings=MemorySettings(mode="read_only"),
        )
        try:
            user_results = other.recall("Chinese response language preference").results
            project_results = other.recall("private session format").results
        finally:
            other.close()
        self.assertEqual([result.item.scope for result in user_results], ["user"])
        self.assertEqual(project_results, [])


class MemoryContextTests(unittest.TestCase):
    def test_memory_context_is_ephemeral_and_inserted_before_latest_user_message(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "current"},
        ]

        projected = inject_memory_context(messages, "[Retrieved memory]\n- preference")

        self.assertEqual(len(messages), 4)
        self.assertEqual(projected[-1]["content"], "current")
        self.assertEqual(projected[-2]["contextKind"], "memory")
        self.assertTrue(projected[-2]["synthetic"])

    def test_empty_memory_context_returns_a_new_unmodified_projection(self) -> None:
        messages = [{"role": "user", "content": "hello"}]
        projected = inject_memory_context(messages, "")
        self.assertEqual(projected, messages)
        self.assertIsNot(projected, messages)


class InstructionResolverTests(unittest.TestCase):
    def test_loads_scoped_instructions_rules_and_bounded_memory_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            tinycoder_home = root / "tiny-home"
            project = root / "repo"
            home.mkdir()
            tinycoder_home.mkdir()
            project.mkdir()
            (tinycoder_home / "CLAUDE.md").write_text("global tinycoder rule", encoding="utf-8")
            (home / ".claude").mkdir()
            (home / ".claude" / "CLAUDE.md").write_text("claude compatibility rule", encoding="utf-8")
            (project / "CLAUDE.md").write_text("project rule", encoding="utf-8")
            (project / "CLAUDE.local.md").write_text("local project rule", encoding="utf-8")
            rules = project / ".tinycoder" / "rules"
            rules.mkdir(parents=True)
            (rules / "testing.md").write_text("always run tests", encoding="utf-8")
            global_memory = tinycoder_home / "memory" / "global"
            global_memory.mkdir(parents=True)
            global_memory.joinpath("MEMORY.md").write_text(
                "\n".join(f"memory line {index}" for index in range(250)),
                encoding="utf-8",
            )

            documents = load_instruction_documents(
                project,
                user_home=home,
                tinycoder_home=tinycoder_home,
            )
            rendered = render_instruction_documents(documents)

        self.assertIn("global tinycoder rule", rendered)
        self.assertIn("claude compatibility rule", rendered)
        self.assertIn("project rule", rendered)
        self.assertIn("local project rule", rendered)
        self.assertIn("always run tests", rendered)
        self.assertIn("memory line 199", rendered)
        self.assertNotIn("memory line 200", rendered)

    def test_path_scoped_rule_loads_only_for_matching_active_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            rules = project / ".tinycoder" / "rules"
            rules.mkdir(parents=True)
            (rules / "python.md").write_text(
                "---\npaths:\n  - \"tinycoder/**/*.py\"\n---\nPython-only rule",
                encoding="utf-8",
            )

            excluded = load_instruction_documents(project, active_paths=["README.md"])
            included = load_instruction_documents(project, active_paths=["tinycoder/index.py"])

        self.assertNotIn("Python-only rule", render_instruction_documents(excluded))
        self.assertIn("Python-only rule", render_instruction_documents(included))
