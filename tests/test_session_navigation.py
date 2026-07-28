from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tinycoder import cli_commands, session, tty_app


class _FakePermissions:
    prompt = None


def _tty_args(
    cwd: Path,
    messages: list[dict[str, object]],
    *,
    session_id: str,
) -> dict[str, object]:
    return {
        "cwd": str(cwd),
        "messages": messages,
        "permissions": _FakePermissions(),
        "runtime": {"provider": "mock", "model": "mock"},
        "model": object(),
        "tools": object(),
        "sessionId": session_id,
        "alreadySavedCount": 0,
        "contextCollapseState": {"spans": [{"id": "old-span"}]},
        "contentReplacementState": {
            "seenIds": {"old-tool"},
            "replacements": {"old-tool": "old-output"},
        },
        "memory": None,
    }


class SessionNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_newchat_starts_a_new_session_without_deleting_the_previous_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            sessions_root = root / "sessions"
            messages: list[dict[str, object]] = [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "old question"},
                {"role": "assistant", "content": "old answer"},
            ]
            args = _tty_args(project, messages, session_id="old-chat")

            with patch.object(session, "TINYCODER_PROJECTS_DIR", sessions_root):
                await session.save_session(
                    str(project),
                    "old-chat",
                    messages,
                )
                old_session_path = session.session_file_path(str(project), "old-chat")

                output = io.StringIO()
                with (
                    patch.object(
                        tty_app,
                        "_read_interactive_line",
                        side_effect=["/newchat", "/exit"],
                    ),
                    patch.object(tty_app, "load_history_entries", return_value=[]),
                    patch.object(tty_app, "save_history_entries"),
                    patch.object(tty_app, "clear_screen", create=True) as clear_screen,
                    patch.object(tty_app, "render_banner", return_value="BANNER"),
                    redirect_stdout(output),
                ):
                    await tty_app.run_tty_app(args)

            self.assertTrue(old_session_path.exists())
            self.assertNotEqual(args["sessionId"], "old-chat")
            self.assertEqual(messages, [{"role": "system", "content": "system"}])
            self.assertEqual(args["contextCollapseState"]["spans"], [])
            self.assertEqual(args["contentReplacementState"]["seenIds"], set())
            self.assertEqual(args["contentReplacementState"]["replacements"], {})
            clear_screen.assert_called_once_with()
            self.assertEqual(output.getvalue().count("BANNER"), 2)

    async def test_resume_repaints_the_session_workspace_with_the_full_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            sessions_root = root / "sessions"
            target_messages: list[dict[str, object]] = [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "historical question"},
                {
                    "role": "assistant_tool_call",
                    "toolUseId": "tool-1",
                    "toolName": "read_file",
                    "input": {"path": "README.md"},
                },
                {
                    "role": "tool_result",
                    "toolUseId": "tool-1",
                    "toolName": "read_file",
                    "content": "historical tool output",
                    "isError": False,
                },
                {"role": "assistant", "content": "historical answer"},
            ]
            current_messages: list[dict[str, object]] = [
                {"role": "system", "content": "system"},
            ]
            args = _tty_args(project, current_messages, session_id="current")

            with patch.object(session, "TINYCODER_PROJECTS_DIR", sessions_root):
                await session.save_session(
                    str(project),
                    "target-chat",
                    target_messages,
                )

                output = io.StringIO()
                with (
                    patch.object(
                        tty_app,
                        "_read_interactive_line",
                        side_effect=["/resume target-chat", "/exit"],
                    ),
                    patch.object(tty_app, "load_history_entries", return_value=[]),
                    patch.object(tty_app, "save_history_entries"),
                    patch.object(tty_app, "clear_screen", create=True) as clear_screen,
                    patch.object(tty_app, "render_banner", return_value="BANNER"),
                    redirect_stdout(output),
                ):
                    await tty_app.run_tty_app(args)

            rendered = output.getvalue()
            self.assertEqual(args["sessionId"], "target-chat")
            self.assertEqual(current_messages[1]["content"], "historical question")
            self.assertEqual(args["contentReplacementState"]["seenIds"], set())
            clear_screen.assert_called_once_with()
            self.assertEqual(rendered.count("BANNER"), 2)
            self.assertIn("historical question", rendered)
            self.assertIn("README.md", rendered)
            self.assertIn("historical tool output", rendered)
            self.assertIn("historical answer", rendered)

    async def test_transcript_preserves_tool_call_and_tool_result_as_separate_ui_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            sessions_root = root / "sessions"
            messages: list[dict[str, object]] = [
                {"role": "system", "content": "system"},
                {
                    "role": "assistant_tool_call",
                    "toolUseId": "tool-1",
                    "toolName": "read_file",
                    "input": {"path": "README.md"},
                },
                {
                    "role": "tool_result",
                    "toolUseId": "tool-1",
                    "content": "file contents",
                    "isError": False,
                },
            ]

            with patch.object(session, "TINYCODER_PROJECTS_DIR", sessions_root):
                await session.save_session(str(project), "tools-chat", messages)
                entries = await session.load_transcript(str(project), "tools-chat")

            tool_entries = [
                entry for entry in entries or [] if entry.get("kind") == "tool"
            ]
            self.assertEqual(
                [entry.get("status") for entry in tool_entries],
                ["running", "success"],
            )
            self.assertEqual(tool_entries[1].get("toolName"), "read_file")
            self.assertEqual(tool_entries[1].get("body"), "file contents")

    async def test_transcript_keeps_original_messages_after_context_snipping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            sessions_root = root / "sessions"
            messages: list[dict[str, object]] = [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "message retained in the visual history"},
                {"role": "assistant", "content": "answer retained in the visual history"},
            ]

            with patch.object(session, "TINYCODER_PROJECTS_DIR", sessions_root):
                await session.save_session(str(project), "snipped-chat", messages)
                await session.append_snip_boundary(
                    str(project),
                    "snipped-chat",
                    {
                        "role": "snip_boundary",
                        "removedMessageIds": [
                            messages[1]["id"],
                            messages[2]["id"],
                        ],
                        "removedCount": 2,
                        "tokensFreed": 128,
                    },
                )
                entries = await session.load_transcript(str(project), "snipped-chat")

            bodies = [str(entry.get("body") or "") for entry in entries or []]
            self.assertIn("message retained in the visual history", bodies)
            self.assertIn("answer retained in the visual history", bodies)
            self.assertTrue(any("Snipped earlier context" in body for body in bodies))

    async def test_transcript_deduplicates_messages_rewritten_after_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            sessions_root = root / "sessions"
            messages: list[dict[str, object]] = [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "original compacted question"},
                {"role": "assistant", "content": "original compacted answer"},
            ]

            with patch.object(session, "TINYCODER_PROJECTS_DIR", sessions_root):
                await session.save_session(str(project), "compact-chat", messages)
                await session.append_compact_boundary(
                    str(project),
                    "compact-chat",
                    "summary for model context",
                    "manual",
                    1000,
                    200,
                    retained_messages=[messages[1], messages[2]],
                )
                entries = await session.load_transcript(str(project), "compact-chat")

            bodies = [str(entry.get("body") or "") for entry in entries or []]
            self.assertEqual(bodies.count("original compacted question"), 1)
            self.assertEqual(bodies.count("original compacted answer"), 1)
            self.assertTrue(any("Context compacted" in body for body in bodies))

    def test_newchat_is_documented_and_available_for_completion(self) -> None:
        usages = [command["usage"] for command in cli_commands.SLASH_COMMANDS]

        self.assertIn("/newchat", usages)
        self.assertEqual(
            cli_commands.complete_slash_command_name("/newc"),
            "/newchat",
        )


if __name__ == "__main__":
    unittest.main()
