from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tinycoder.memory.runtime import (
    active_paths_from_messages,
    create_memory_context_provider,
    create_memory_service,
    latest_user_event_id,
    latest_user_text,
)


class MemoryRuntimeTests(unittest.TestCase):
    def test_active_paths_are_inferred_from_user_and_tool_messages(self) -> None:
        paths = active_paths_from_messages(
            [
                {"role": "user", "content": "Please update tinycoder/index.py and README.md."},
                {
                    "role": "assistant_tool_call",
                    "toolName": "read_file",
                    "input": {"path": "tinycoder/memory/service.py"},
                },
            ]
        )
        self.assertEqual(
            paths,
            ["tinycoder/memory/service.py", "tinycoder/index.py", "README.md"],
        )

    def test_factory_applies_config_and_provider_recalls_for_latest_real_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            service = create_memory_service(
                project,
                {"memory": {"mode": "suggest", "maxRecallItems": 3}},
                store_path=root / "memory.db",
            )
            try:
                service.add(
                    scope="project_local",
                    kind="procedure",
                    canonical_key="project.test.command",
                    content="Run unittest discovery.",
                )
                provider = create_memory_context_provider(service, session_id="session-1")
                rendered = provider(
                    [
                        {"role": "system", "content": "system"},
                        {
                            "role": "user",
                            "content": "ignore this synthetic query",
                            "synthetic": True,
                            "contextKind": "instructions",
                        },
                        {"role": "user", "content": "How do I run project tests?"},
                    ]
                )
            finally:
                service.close()

        self.assertEqual(service.settings.max_recall_items, 3)
        self.assertIn("Run unittest discovery.", rendered)

    def test_latest_user_text_ignores_synthetic_context(self) -> None:
        self.assertEqual(
            latest_user_text(
                [
                    {"role": "user", "content": "real task"},
                    {
                        "role": "user",
                        "content": "memory",
                        "synthetic": True,
                        "contextKind": "memory",
                    },
                ]
            ),
            "real task",
        )
        self.assertEqual(
            latest_user_event_id(
                [
                    {"role": "user", "content": "real task", "eventId": "event-1"},
                    {
                        "role": "user",
                        "content": "continue",
                        "synthetic": True,
                        "contextKind": "agent_recovery",
                    },
                ]
            ),
            "event-1",
        )
