from __future__ import annotations

import os
import re
import shutil
import sys

RESET = "\u001b[0m"
DIM = "\u001b[2m"
CYAN = "\u001b[36m"
YELLOW = "\u001b[33m"
MAGENTA = "\u001b[35m"
BOLD = "\u001b[1m"
ITALIC = "\u001b[3m"

MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd", ".mdx"}


def is_markdown_path(path: str | None) -> bool:
    """Return True when a path should be displayed as Markdown in the terminal."""
    value = (path or "").strip().lower()
    return any(value.endswith(ext) for ext in MARKDOWN_EXTENSIONS)


def _markdown_render_enabled() -> bool:
    value = os.environ.get("TINYCODER_MARKDOWN_RENDER", "1").strip().lower()
    return value not in {"0", "false", "off", "no", "raw"}


def _color_enabled(color: bool | None) -> bool:
    if color is not None:
        return color
    if os.environ.get("NO_COLOR"):
        return False
    value = os.environ.get("TINYCODER_MARKDOWN_COLOR", "auto").strip().lower()
    if value in {"0", "false", "off", "no", "plain"}:
        return False
    if value in {"1", "true", "on", "yes", "ansi"}:
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _style(text: str, *codes: str, color: bool) -> str:
    if not color or not text:
        return text
    return "".join(codes) + text + RESET


def _terminal_width(default: int = 100) -> int:
    return max(60, shutil.get_terminal_size((default, 40)).columns)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_separator(line: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", line))


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.count("|") >= 2 and stripped.startswith("|") and stripped.endswith("|")


def _render_table(lines: list[str], *, color: bool) -> list[str]:
    rows = [_split_table_row(line) for line in lines if not _is_table_separator(line)]
    if not rows:
        return []
    max_cols = max(len(row) for row in rows)
    for row in rows:
        row.extend([""] * (max_cols - len(row)))
    widths = [min(32, max(len(row[col]) for row in rows)) for col in range(max_cols)]
    rendered: list[str] = []
    for row_index, row in enumerate(rows):
        cells = [row[col][: widths[col]].ljust(widths[col]) for col in range(max_cols)]
        text = "  ".join(cells).rstrip()
        rendered.append(_style(text, BOLD, color=color) if row_index == 0 else text)
        if row_index == 0 and len(rows) > 1:
            rendered.append(_style("  ".join("─" * width for width in widths).rstrip(), DIM, color=color))
    return rendered


def _render_inline(text: str, *, color: bool) -> str:
    # Links: keep both label and URL, but remove Markdown brackets.
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f"{m.group(1)} {_style('(' + m.group(2) + ')', DIM, color=color)}",
        text,
    )
    # Inline code before emphasis, so asterisks inside code are preserved.
    text = re.sub(r"`([^`]+)`", lambda m: _style(m.group(1), MAGENTA, color=color), text)
    text = re.sub(r"\*\*([^*]+)\*\*", lambda m: _style(m.group(1), BOLD, color=color), text)
    text = re.sub(r"__([^_]+)__", lambda m: _style(m.group(1), BOLD, color=color), text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: _style(m.group(1), ITALIC, color=color), text)
    text = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", lambda m: _style(m.group(1), ITALIC, color=color), text)
    return text


def _flush_table(buffer: list[str], rendered: list[str], *, color: bool) -> None:
    if not buffer:
        return
    rendered.extend(_render_table(buffer, color=color))
    buffer.clear()


def render_markdownish(input_text: str, *, color: bool | None = None) -> str:
    """Render common Markdown syntax into a terminal-friendly form.

    This intentionally avoids external dependencies. It removes visible Markdown
    markers such as heading hashes, emphasis asterisks and code fences, while
    preserving code block content and using ANSI styling when the terminal
    supports it.

    Set TINYCODER_MARKDOWN_RENDER=0 to show raw Markdown.
    Set TINYCODER_MARKDOWN_COLOR=0 or NO_COLOR=1 to disable ANSI colors.
    """
    if not _markdown_render_enabled():
        return input_text

    use_color = _color_enabled(color)
    lines = input_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rendered: list[str] = []
    table_buffer: list[str] = []
    in_code = False
    code_language = ""

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            _flush_table(table_buffer, rendered, color=use_color)
            if not in_code:
                in_code = True
                code_language = stripped[3:].strip()
                if code_language:
                    rendered.append(_style(f"code: {code_language}", DIM, color=use_color))
            else:
                in_code = False
                code_language = ""
            continue

        if in_code:
            # Preserve code exactly except for a small visual indent.
            rendered.append(_style("  " + line, DIM, color=use_color))
            continue

        if _is_table_row(line) or _is_table_separator(line):
            table_buffer.append(line)
            continue
        _flush_table(table_buffer, rendered, color=use_color)

        if not stripped:
            rendered.append("")
            continue

        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            rendered.append(_style("─" * min(72, _terminal_width()), DIM, color=use_color))
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            level = len(heading.group(1))
            prefix = "" if level <= 2 else "· "
            rendered.append(_style(prefix + _render_inline(heading.group(2), color=use_color), CYAN, BOLD, color=use_color))
            continue

        quote = re.match(r"^\s*>\s?(.*)$", line)
        if quote:
            rendered.append(_style("│ " + _render_inline(quote.group(1), color=use_color), DIM, color=use_color))
            continue

        task = re.match(r"^(\s*)[-*+]\s+\[([ xX])\]\s+(.+)$", line)
        if task:
            indent = task.group(1)
            checked = "✓" if task.group(2).lower() == "x" else " "
            rendered.append(f"{indent}{_style('[' + checked + ']', YELLOW, color=use_color)} {_render_inline(task.group(3), color=use_color)}")
            continue

        unordered = re.match(r"^(\s*)[-*+]\s+(.+)$", line)
        if unordered:
            indent = unordered.group(1)
            rendered.append(f"{indent}{_style('•', YELLOW, color=use_color)} {_render_inline(unordered.group(2), color=use_color)}")
            continue

        ordered = re.match(r"^(\s*)\d+[.)]\s+(.+)$", line)
        if ordered:
            indent = ordered.group(1)
            number = re.match(r"^\s*(\d+)", line).group(1)  # type: ignore[union-attr]
            rendered.append(f"{indent}{_style(number + '.', YELLOW, color=use_color)} {_render_inline(ordered.group(2), color=use_color)}")
            continue

        rendered.append(_render_inline(line, color=use_color))

    _flush_table(table_buffer, rendered, color=use_color)
    return "\n".join(rendered)


renderMarkdownish = render_markdownish
isMarkdownPath = is_markdown_path



class MarkdownStreamPrinter:
    """Line-buffered Markdown renderer for streaming model output.

    Model providers usually stream text token by token. Rendering full Markdown on
    every token causes flicker, while printing raw deltas exposes Markdown markers
    such as #, ** and ``` directly. This printer buffers until a newline is seen,
    renders the completed line with render_markdownish(), and flushes the final
    partial line when finish() is called.
    """

    def __init__(self, *, prefix_newline: bool = True, suffix_newline: bool = True) -> None:
        self._buffer = ""
        self._started = False
        self._prefix_newline = prefix_newline
        self._suffix_newline = suffix_newline

    def write(self, delta: str) -> None:
        if not delta:
            return
        if not self._started:
            if self._prefix_newline:
                print("")
            self._started = True
        self._buffer += str(delta).replace("\r\n", "\n").replace("\r", "\n")
        parts = self._buffer.split("\n")
        self._buffer = parts.pop()
        for line in parts:
            print(render_markdownish(line), flush=True)

    def finish(self) -> None:
        if self._buffer:
            if not self._started and self._prefix_newline:
                print("")
                self._started = True
            print(render_markdownish(self._buffer), flush=True)
            self._buffer = ""
        if self._started and self._suffix_newline:
            print("")


markdownStreamPrinter = MarkdownStreamPrinter
