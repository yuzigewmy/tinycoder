from __future__ import annotations

import unittest
from io import StringIO

from tinycoder.line_editor import LineEditor, text_display_width
from tinycoder.terminal_input import PromptRenderer, TerminalGeometry
from tinycoder.tty_app import _apply_editor_event, _history_candidates
from tinycoder.tui.input_parser import parse_input_chunk


class LineEditorTests(unittest.TestCase):
    def test_inserts_and_deletes_at_the_cursor_instead_of_only_at_the_end(self) -> None:
        editor = LineEditor("helo")

        self.assertTrue(editor.move_left())
        editor.insert("l")
        self.assertEqual(editor.text, "hello")
        self.assertEqual(editor.cursor, 4)

        self.assertTrue(editor.backspace())
        self.assertEqual(editor.text, "helo")
        self.assertEqual(editor.cursor, 3)

        self.assertTrue(editor.delete())
        self.assertEqual(editor.text, "hel")

    def test_left_and_right_are_clamped_to_the_text_boundaries(self) -> None:
        editor = LineEditor("abc", cursor=0)

        self.assertFalse(editor.move_left())
        self.assertTrue(editor.move_right())
        self.assertEqual(editor.cursor, 1)
        editor.move_end()
        self.assertFalse(editor.move_right())
        editor.move_home()
        self.assertEqual(editor.cursor, 0)

    def test_up_and_down_follow_visual_rows_of_wrapped_input(self) -> None:
        editor = LineEditor("abcdefghijklmno")

        self.assertTrue(
            editor.move_vertical(
                -1,
                terminal_columns=10,
                prompt_width=3,
                origin_x=0,
            )
        )
        self.assertEqual(editor.cursor, 5)

        self.assertTrue(
            editor.move_vertical(
                1,
                terminal_columns=10,
                prompt_width=3,
                origin_x=0,
            )
        )
        self.assertEqual(editor.cursor, len(editor.text))

    def test_vertical_navigation_keeps_the_preferred_screen_column(self) -> None:
        editor = LineEditor("abcdefghijklmnopqrstuvwxyz", cursor=25)

        editor.move_vertical(-1, terminal_columns=10, prompt_width=2, origin_x=0)
        self.assertEqual(editor.cursor, 15)
        editor.move_vertical(-1, terminal_columns=10, prompt_width=2, origin_x=0)
        self.assertEqual(editor.cursor, 5)
        editor.move_vertical(1, terminal_columns=10, prompt_width=2, origin_x=0)
        self.assertEqual(editor.cursor, 15)

    def test_mouse_click_maps_screen_coordinates_to_a_unicode_cursor_boundary(self) -> None:
        editor = LineEditor("甲乙abc")
        self.assertEqual(text_display_width(editor.text), 7)

        self.assertTrue(
            editor.move_to_screen_position(
                x=8,
                y=4,
                origin_x=0,
                origin_y=4,
                terminal_columns=20,
                prompt_width=5,
            )
        )
        self.assertEqual(editor.cursor, 2)

        self.assertFalse(
            editor.move_to_screen_position(
                x=0,
                y=3,
                origin_x=0,
                origin_y=4,
                terminal_columns=20,
                prompt_width=5,
            )
        )

    def test_click_can_target_a_wrapped_visual_row(self) -> None:
        editor = LineEditor("abcdefghijklmnop")

        self.assertTrue(
            editor.move_to_screen_position(
                x=3,
                y=2,
                origin_x=0,
                origin_y=1,
                terminal_columns=10,
                prompt_width=3,
            )
        )
        self.assertEqual(editor.cursor, 10)


class TerminalInputParserTests(unittest.TestCase):
    def test_parses_arrow_keys_and_sgr_left_mouse_click(self) -> None:
        parsed = parse_input_chunk("", "\u001b[D\u001b[C\u001b[A\u001b[B\u001b[<0;12;7M")

        self.assertEqual(
            parsed["events"],
            [
                {"kind": "key", "name": "left", "ctrl": False, "meta": False},
                {"kind": "key", "name": "right", "ctrl": False, "meta": False},
                {"kind": "key", "name": "up", "ctrl": False, "meta": False},
                {"kind": "key", "name": "down", "ctrl": False, "meta": False},
                {
                    "kind": "mouse",
                    "x": 11,
                    "y": 6,
                    "button": "left",
                    "action": "press",
                },
            ],
        )
        self.assertEqual(parsed["rest"], "")


class TtyEditorIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output = StringIO()
        self.renderer = PromptRenderer(
            "> ",
            TerminalGeometry(0, 2, 20, 10),
            output=self.output,
        )
        self.history_state = {
            "candidates": _history_candidates(["/status", "/help"]),
            "index": None,
            "draft": "",
        }

    def test_keyboard_events_edit_at_the_moved_cursor(self) -> None:
        editor = LineEditor("abc")

        self.assertEqual(
            _apply_editor_event(
                editor,
                {"kind": "key", "name": "left"},
                self.history_state,
                self.renderer,
            ),
            "redraw",
        )
        _apply_editor_event(
            editor,
            {"kind": "text", "text": "X"},
            self.history_state,
            self.renderer,
        )

        self.assertEqual(editor.text, "abXc")
        self.assertEqual(editor.cursor, 3)

    def test_up_keeps_slash_history_when_there_is_no_wrapped_row(self) -> None:
        editor = LineEditor("/draft")

        _apply_editor_event(
            editor,
            {"kind": "key", "name": "up"},
            self.history_state,
            self.renderer,
        )

        self.assertEqual(editor.text, "/help")
        self.assertEqual(editor.cursor, len("/help"))

    def test_left_mouse_press_repositions_the_same_editor_cursor(self) -> None:
        editor = LineEditor("abcdef")

        _apply_editor_event(
            editor,
            {
                "kind": "mouse",
                "x": 4,
                "y": 2,
                "button": "left",
                "action": "press",
            },
            self.history_state,
            self.renderer,
        )

        self.assertEqual(editor.cursor, 2)

    def test_relative_renderer_finishes_at_the_end_of_the_edited_text(self) -> None:
        output = StringIO()
        renderer = PromptRenderer(
            "> ",
            TerminalGeometry(0, None, 20, 10),
            output=output,
        )
        editor = LineEditor("abc", cursor=1)

        renderer.redraw(editor)
        renderer.finish(editor)

        self.assertTrue(output.getvalue().endswith("\r> abc\x1b[K\n"))


if __name__ == "__main__":
    unittest.main()
