from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tinycoder.agent_loop import run_agent_turn
from tinycoder.cli_commands import try_handle_local_command
from tinycoder.config import merge_settings
from tinycoder.memory.service import MemoryService
from tinycoder.memory.settings import MemorySettings
from tinycoder.prompt import build_instruction_context, build_system_prompt


class CapturingModel:
    def __init__(self) -> None:
        self.seen: list[list[dict[str, Any]]] = []

    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.seen.append([dict(message) for message in messages])
        return {"type": "assistant", "content": "done"}


class EmptyTools:
    def list(self) -> list[Any]:
        return []


class MemoryConfigTests(unittest.TestCase):
    def test_memory_settings_are_merged_without_losing_base_fields(self) -> None:
        merged = merge_settings(
            {"memory": {"mode": "suggest", "maxRecallTokens": 900}},
            {"memory": {"mode": "read_only"}},
        )
        self.assertEqual(
            merged["memory"],
            {"mode": "read_only", "maxRecallTokens": 900},
        )


class PromptTrustBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_documents_are_user_context_not_system_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            tinycoder_home = root / "tiny-home"
            project = root / "project"
            home.mkdir()
            tinycoder_home.mkdir()
            project.mkdir()
            (project / "CLAUDE.md").write_text("project instruction", encoding="utf-8")

            system = await build_system_prompt(
                str(project),
                extras={"userHome": str(home), "tinycoderHome": str(tinycoder_home)},
            )
            instructions = await build_instruction_context(
                str(project),
                extras={"userHome": str(home), "tinycoderHome": str(tinycoder_home)},
            )

        self.assertNotIn("project instruction", system)
        self.assertIn("project instruction", instructions)
        self.assertIn("untrusted user-authored context", instructions)


class MemoryCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_commands_add_list_show_and_forget(self) -> None:
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
                added = await try_handle_local_command(
                    "/memory add project_local procedure project.test.command::Run unittest discovery.",
                    {"memory": service},
                )
                memory_id = added.split("id=", 1)[1].split()[0]
                listed = await try_handle_local_command("/memory list", {"memory": service})
                shown = await try_handle_local_command(f"/memory show {memory_id}", {"memory": service})
                forgotten = await try_handle_local_command(
                    f"/memory forget {memory_id}",
                    {"memory": service},
                )
            finally:
                service.close()

        self.assertIn("stored", added)
        self.assertIn("project.test.command", listed)
        self.assertIn("Run unittest discovery.", shown)
        self.assertIn("forgotten", forgotten)

    async def test_memory_review_and_audit_commands(self) -> None:
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
                service.capture_turn(
                    [
                        {"role": "user", "content": "I prefer concise technical reports."},
                        {"role": "assistant", "content": "Understood."},
                    ],
                    session_id="s1",
                    event_id="s1:2",
                )
                item = service.list()[0]
                pending = await try_handle_local_command("/memory pending", {"memory": service})
                approved = await try_handle_local_command(
                    f"/memory approve {item.id}",
                    {"memory": service},
                )
                history = await try_handle_local_command(
                    f"/memory history {item.id}",
                    {"memory": service},
                )
                exported = await try_handle_local_command("/memory export", {"memory": service})
            finally:
                service.close()

        self.assertIn(item.id, pending)
        self.assertIn("approved", approved)
        self.assertIn("operation=status", history)
        self.assertEqual(json.loads(exported)["schemaVersion"], 1)


class AgentLoopMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_loop_projects_instructions_and_memory_without_persisting_them(self) -> None:
        model = CapturingModel()
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "current task"},
        ]
        provider_calls = 0

        def memory_provider(current_messages: list[dict[str, Any]]) -> str:
            nonlocal provider_calls
            provider_calls += 1
            self.assertEqual(current_messages[-1]["content"], "current task")
            return "[Retrieved memory]\n- remembered fact"

        result = await run_agent_turn(
            {
                "model": model,
                "tools": EmptyTools(),
                "messages": messages,
                "cwd": ".",
                "instructionContext": "[Project instructions]\n- run tests",
                "memoryContextProvider": memory_provider,
            }
        )

        projected = model.seen[0]
        self.assertEqual(provider_calls, 1)
        self.assertEqual(projected[-1]["content"], "current task")
        self.assertEqual(
            [message.get("contextKind") for message in projected if message.get("synthetic")],
            ["instructions", "memory"],
        )
        self.assertFalse(any(message.get("synthetic") for message in result))
        self.assertFalse(any(message.get("synthetic") for message in messages))
