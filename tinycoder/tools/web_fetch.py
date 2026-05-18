from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..tool import ToolDefinition
from ..utils.web import fetch_web_page


def _validate(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict) or not isinstance(input_value.get("url"), str):
        raise ValueError("url must be a string")
    parsed = urlparse(input_value["url"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an HTTP or HTTPS URL")
    max_chars = input_value.get("max_chars", 12000)
    if not isinstance(max_chars, int) or max_chars < 500:
        raise ValueError("max_chars must be an integer >= 500")
    return {"url": input_value["url"], "max_chars": max_chars}


async def _run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await fetch_web_page({"url": input_value["url"], "maxChars": input_value.get("max_chars") or 12000})
        if int(result.get("status") or 0) >= 400:
            return {"ok": False, "output": f"HTTP {result.get('status')} {result.get('statusText', '')}: {input_value['url']}"}
        lines = [f"URL: {result.get('finalUrl')}", f"STATUS: {result.get('status')}", f"CONTENT_TYPE: {result.get('contentType')}"]
        if result.get("title"):
            lines.append(f"TITLE: {result.get('title')}")
        lines.extend(["", str(result.get("content") or "")])
        return {"ok": True, "output": "\n".join(lines)}
    except Exception as error:
        return {"ok": False, "output": f"Web fetch failed: {error}"}


web_fetch_tool = ToolDefinition(
    name="web_fetch",
    description="Fetch a web page and extract its readable text content. Use this after web_search when you need the full content of a specific page.",
    input_schema={"type": "object", "properties": {"url": {"type": "string", "description": "HTTP or HTTPS URL to fetch."}, "max_chars": {"type": "number", "description": "Maximum number of characters to return from the page content. Defaults to 12000."}}, "required": ["url"]},
    validator=_validate,
    run=_run,
)

webFetchTool = web_fetch_tool
