from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tinycoder.tui import chrome
from tinycoder.tui.markdown import MarkdownStreamPrinter, render_markdownish
from tinycoder.tui.theme import strip_ansi
from tinycoder.tui.transcript import render_transcript_lines


class TuiRenderingTests(unittest.TestCase):
    def test_banner_is_compact_and_respects_narrow_terminal_width(self) -> None:
        with (
            patch.dict(os.environ, {"TINYCODER_COLOR": "0"}),
            patch.object(chrome.shutil, "get_terminal_size", return_value=os.terminal_size((42, 24))),
        ):
            rendered = chrome.render_banner(
                {"provider": "qwen", "model": "qwen-plus-long-name"},
                "/Users/example/a-very-long-project-name",
            )

        lines = rendered.splitlines()
        self.assertLessEqual(len(lines), 11)
        self.assertTrue(all(chrome.string_display_width(line) <= 42 for line in lines))
        self.assertIn("tinycoder", rendered)
        self.assertNotIn("_____", rendered)

    def test_no_color_mode_removes_ansi_from_all_chrome(self) -> None:
        with patch.dict(os.environ, {"TINYCODER_COLOR": "0", "TINYCODER_MARKDOWN_COLOR": "0"}):
            rendered = chrome.render_banner({"provider": "mock", "model": "mock"}, "/tmp/project")
            transcript = "\n".join(
                render_transcript_lines([{"kind": "assistant", "body": "**ready**"}])
            )

        self.assertNotIn("\x1b[", rendered)
        self.assertNotIn("\x1b[", transcript)

    def test_wrapped_cjk_transcript_lines_keep_the_visual_rail(self) -> None:
        with (
            patch.dict(os.environ, {"TINYCODER_COLOR": "0", "TINYCODER_MARKDOWN_COLOR": "0"}),
            patch.object(chrome.shutil, "get_terminal_size", return_value=os.terminal_size((36, 24))),
        ):
            lines = render_transcript_lines(
                [{"kind": "assistant", "body": "窄终端中的长中文内容也必须保持在视觉轨道之内。"}]
            )

        self.assertGreater(len(lines), 3)
        self.assertTrue(all(line.startswith("  │") for line in lines[1:-1]))
        self.assertTrue(all(chrome.string_display_width(line) <= 36 for line in lines))

    def test_markdown_code_blocks_use_the_same_quiet_rail(self) -> None:
        rendered = render_markdownish("```python\nprint('ok')\n```", color=False)
        self.assertEqual(rendered, "╭─ code · python\n│  print('ok')\n╰─")

    def test_streamed_answers_open_and_close_one_response_frame(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"TINYCODER_COLOR": "0", "TINYCODER_MARKDOWN_COLOR": "0"}),
            redirect_stdout(output),
        ):
            printer = MarkdownStreamPrinter(framed=True)
            printer.write("完成。\n")
            printer.write("- 已验证")
            printer.finish()

        rendered = strip_ansi(output.getvalue())
        self.assertEqual(rendered.count("╭─ tinycoder"), 1)
        self.assertEqual(rendered.count("╰─"), 1)
        self.assertIn("  │  • 已验证", rendered)


if __name__ == "__main__":
    unittest.main()
