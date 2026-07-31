from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from tinycoder.tty_app import (
    AssistantStreamFilter,
    _format_session_option,
    _render_tool_result,
    _render_tool_start,
)
from tinycoder.tui.chrome import (
    render_activity_line,
    render_banner,
    render_panel,
    render_permission_prompt,
    render_session_picker,
    render_status_line,
    string_display_width,
    strip_ansi,
)
from tinycoder.tui.input import render_input_prompt
from tinycoder.tui.markdown import render_markdownish
from tinycoder.tui.theme import ACCENT, glyph, style
from tinycoder.tui.transcript import render_transcript_lines


RUNTIME = {
    "provider": "deepseek",
    "model": "deepseek-v4-pro",
    "permissionMode": "auto_approve",
    "permissionModeLabel": "替我审批",
}


class _RecordingPrinter:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def write(self, value: str) -> None:
        self.parts.append(value)


class TuiShowcaseTests(unittest.TestCase):
    def render_banner_at(self, width: int, *, runtime: dict | None = None) -> str:
        with (
            patch("tinycoder.tui.chrome.shutil.get_terminal_size", return_value=os.terminal_size((width, 40))),
            patch.dict(
                os.environ,
                {
                    "TINYCODER_COLOR": "never",
                    "TINYCODER_UNICODE": "always",
                },
                clear=False,
            ),
        ):
            return render_banner(runtime or RUNTIME, r"C:\workspace\tinycoder-demo")

    def assert_lines_fit(self, rendered: str, width: int) -> None:
        for line in rendered.splitlines():
            self.assertLessEqual(
                string_display_width(line),
                width,
                msg=f"line exceeds {width} columns: {strip_ansi(line)!r}",
            )

    def test_wide_banner_has_a_branded_hero_and_runtime_surface(self) -> None:
        rendered = self.render_banner_at(100)
        plain = strip_ansi(rendered)

        self.assertIn("█████", plain)
        self.assertIn("AGENTIC ENGINEERING TERMINAL", plain)
        self.assertIn("RUNTIME", plain)
        self.assertIn("MODEL", plain)
        self.assertIn("deepseek-v4-pro", plain)
        self.assertIn("PROVIDER", plain)
        self.assertIn("deepseek", plain)
        self.assertIn("MODE", plain)
        self.assertIn("替我审批", plain)
        self.assertIn("WORKSPACE", plain)
        self.assertIn("READY", plain)
        self.assertIn("/resume", plain)
        self.assertNotIn("作者:", plain)
        self.assertNotIn("核心能力：", plain)
        self.assert_lines_fit(rendered, 100)

    def test_banner_uses_a_compact_brand_lockup_on_narrow_terminals(self) -> None:
        rendered = self.render_banner_at(48)
        plain = strip_ansi(rendered)

        self.assertIn("TinyCoder", plain)
        self.assertIn("AGENTIC ENGINEERING", plain)
        self.assertNotIn("█████", plain)
        self.assertIn("deepseek-v4-pro", plain)
        self.assertIn("替我审批", plain)
        self.assert_lines_fit(rendered, 48)

    def test_banner_never_overflows_common_roadshow_widths(self) -> None:
        for width in (40, 56, 80, 120):
            with self.subTest(width=width):
                rendered = self.render_banner_at(width)
                self.assert_lines_fit(rendered, min(width, 104))

    def test_full_access_is_visible_as_a_high_risk_runtime_state(self) -> None:
        runtime = {
            **RUNTIME,
            "permissionMode": "full_access",
            "permissionModeLabel": "完全访问",
        }
        plain = strip_ansi(self.render_banner_at(80, runtime=runtime))

        self.assertIn("完全访问", plain)
        self.assertIn("审批已关闭", plain)
        self.assertIn("HIGH RISK", plain)

    def test_theme_supports_color_opt_out_and_legacy_glyphs(self) -> None:
        with patch.dict(os.environ, {"NO_COLOR": "1", "TINYCODER_COLOR": "always"}):
            self.assertEqual(style("TinyCoder", ACCENT), "TinyCoder")
        with patch.dict(os.environ, {"TINYCODER_UNICODE": "never"}, clear=False):
            self.assertEqual(glyph("◆", "*"), "*")

    def test_gbk_keeps_supported_frame_glyphs_and_falls_back_per_symbol(self) -> None:
        with patch.dict(os.environ, {"TINYCODER_UNICODE": "auto"}, clear=False):
            self.assertEqual(glyph("╭", "+", encoding="gbk"), "╭")
            self.assertEqual(glyph("❯", ">", encoding="gbk"), ">")
            self.assertEqual(glyph("›", ">", encoding="gbk"), ">")

    def test_markdown_uses_the_shared_theme_and_respects_no_color(self) -> None:
        with patch.dict(os.environ, {"TINYCODER_COLOR": "always"}, clear=False):
            os.environ.pop("NO_COLOR", None)
            colored = render_markdownish("# Architecture")
        with patch.dict(
            os.environ,
            {"TINYCODER_COLOR": "always", "NO_COLOR": "1"},
            clear=False,
        ):
            plain = render_markdownish("# Architecture")

        self.assertIn(ACCENT, colored)
        self.assertNotIn("\u001b[", plain)
        self.assertEqual(plain, "Architecture")

    def test_legacy_mode_remains_gbk_encodable(self) -> None:
        with (
            patch("tinycoder.tui.chrome.shutil.get_terminal_size", return_value=os.terminal_size((80, 40))),
            patch.dict(
                os.environ,
                {
                    "TINYCODER_COLOR": "never",
                    "TINYCODER_UNICODE": "never",
                },
                clear=False,
            ),
        ):
            rendered = render_banner(RUNTIME, r"C:\工作区\TinyCoder")

        rendered.encode("gbk")
        self.assertNotIn("█", rendered)
        self.assertNotIn("╭", rendered)

    def test_panel_embeds_title_in_its_frame_without_empty_padding_rows(self) -> None:
        with (
            patch("tinycoder.tui.chrome.shutil.get_terminal_size", return_value=os.terminal_size((72, 40))),
            patch.dict(os.environ, {"TINYCODER_COLOR": "never"}, clear=False),
        ):
            rendered = render_panel("RUNTIME", "model  deepseek-v4-pro")

        lines = rendered.splitlines()
        self.assertIn("RUNTIME", lines[0])
        self.assertEqual(len(lines), 3)
        self.assertIn("model  deepseek-v4-pro", lines[1])

    def test_status_line_keeps_model_permission_workspace_and_context_visible(self) -> None:
        stats = {
            "utilization": 0.42,
            "warningLevel": "normal",
            "accounting": {"source": "provider_usage"},
        }
        with (
            patch("tinycoder.tui.chrome.shutil.get_terminal_size", return_value=os.terminal_size((80, 40))),
            patch.dict(os.environ, {"TINYCODER_COLOR": "never"}, clear=False),
        ):
            rendered = render_status_line(RUNTIME, r"C:\workspace\tinycoder-demo", stats)
        plain = strip_ansi(rendered)

        self.assertIn("deepseek-v4-pro", plain)
        self.assertIn("替我审批", plain)
        self.assertIn("42%", plain)
        self.assertIn("tinycoder-demo", plain)
        self.assert_lines_fit(rendered, 80)

    def test_activity_rail_has_distinct_running_success_and_error_states(self) -> None:
        with patch.dict(os.environ, {"TINYCODER_COLOR": "never"}, clear=False):
            running = strip_ansi(render_activity_line("read_file", "agent_loop.py", "running"))
            success = strip_ansi(render_activity_line("read_file", "184 lines", "success"))
            failure = strip_ansi(render_activity_line("run_command", "exit code 1", "error"))

        self.assertIn("TOOL", running)
        self.assertIn("read_file", running)
        self.assertIn("RUNNING", running)
        self.assertIn("DONE", success)
        self.assertIn("184 lines", success)
        self.assertIn("FAILED", failure)
        self.assertIn("exit code 1", failure)

    def test_live_tool_callbacks_use_the_activity_rail_not_debug_tags(self) -> None:
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            patch.dict(os.environ, {"TINYCODER_COLOR": "never"}, clear=False),
        ):
            _render_tool_start("read_file", {"path": "tinycoder/agent_loop.py"})
            _render_tool_result("read_file", "184 lines", False)
            _render_tool_result("run_command", "exit code 1", True)

        plain = strip_ansi(output.getvalue())
        self.assertNotIn("[tool", plain)
        self.assertIn("RUNNING", plain)
        self.assertIn("DONE", plain)
        self.assertIn("FAILED", plain)

    def test_transcript_uses_branded_roles_and_compact_activity_groups(self) -> None:
        entries = [
            {"kind": "user", "body": "分析 agent loop"},
            {"kind": "assistant", "body": "我先读取入口。"},
            {
                "kind": "tool",
                "toolName": "read_file",
                "status": "success",
                "body": "184 lines",
            },
        ]
        with patch.dict(os.environ, {"TINYCODER_COLOR": "never"}, clear=False):
            rendered = "\n".join(render_transcript_lines(entries))

        plain = strip_ansi(rendered)
        self.assertIn("YOU", plain)
        self.assertIn("TINYCODER", plain)
        self.assertIn("DONE", plain)
        self.assertNotIn("\n\n·\n\n", plain)
        self.assertNotIn("assistant\n", plain)

    def test_streamed_assistant_heading_is_emitted_once_before_visible_text(self) -> None:
        printer = _RecordingPrinter()
        stream_filter = AssistantStreamFilter(printer)  # type: ignore[arg-type]
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            patch.dict(os.environ, {"TINYCODER_COLOR": "never"}, clear=False),
        ):
            stream_filter.write("<progress>checking</progress>")
            stream_filter.write("<final>Hello ")
            stream_filter.write("world</final>")
            stream_filter.finish()

        self.assertEqual(strip_ansi(output.getvalue()).count("TINYCODER"), 1)
        self.assertEqual("".join(printer.parts), "Hello world")

    def test_input_surface_has_brand_focus_and_operational_shortcuts(self) -> None:
        with patch.dict(
            os.environ,
            {"TINYCODER_COLOR": "never", "TINYCODER_UNICODE": "always"},
            clear=False,
        ):
            rendered = strip_ansi(render_input_prompt("修复这个问题", 2))

        self.assertIn("ASK TINYCODER", rendered)
        self.assertIn("Enter", rendered)
        self.assertIn("Tab", rendered)
        self.assertIn("Esc", rendered)
        self.assertNotIn("tinycoder>", rendered)

    def test_permission_surface_exposes_risk_action_choices_and_feedback(self) -> None:
        request = {
            "summary": "运行测试命令",
            "details": ["命令: python -m unittest", "目录: C:\\workspace"],
            "risk": "high",
            "choices": [
                {"key": "1", "label": "允许一次", "decision": "allow_once"},
                {"key": "2", "label": "拒绝并反馈", "decision": "deny_with_feedback"},
            ],
        }
        with (
            patch("tinycoder.tui.chrome.shutil.get_terminal_size", return_value=os.terminal_size((80, 40))),
            patch.dict(os.environ, {"TINYCODER_COLOR": "never"}, clear=False),
        ):
            rendered = strip_ansi(render_permission_prompt(request, feedback_input="改用只读方案"))

        self.assertIn("PERMISSION REVIEW", rendered)
        self.assertIn("HIGH", rendered)
        self.assertIn("运行测试命令", rendered)
        self.assertIn("允许一次", rendered)
        self.assertIn("改用只读方案", rendered)

    def test_session_picker_presents_titles_before_internal_ids(self) -> None:
        sessions = [
            {"id": "abc123", "title": "TUI 路演优化", "messageCount": 18},
            {"id": "def456", "title": "Agent Loop 审查", "messageCount": 31},
        ]
        with (
            patch("tinycoder.tui.chrome.shutil.get_terminal_size", return_value=os.terminal_size((80, 40))),
            patch.dict(os.environ, {"TINYCODER_COLOR": "never"}, clear=False),
        ):
            rendered = strip_ansi(render_session_picker(sessions, 0))

        self.assertIn("RESUME SESSION", rendered)
        self.assertIn("TUI 路演优化", rendered)
        self.assertIn("18 条消息", rendered)
        self.assertLess(rendered.index("TUI 路演优化"), rendered.index("abc123"))
        self.assertNotIn("messages=", _format_session_option(sessions[0], True))


if __name__ == "__main__":
    unittest.main()
