from __future__ import annotations

from typing import Any

from ..tool import ToolDefinition
from ..utils.web import search_duckduckgo_lite


def _validate(input_value: Any) -> dict[str, Any]:
    if not isinstance(input_value, dict) or not isinstance(input_value.get("query"), str) or not input_value["query"].strip():
        raise ValueError("query must be a non-empty string")
    max_results = input_value.get("max_results", 5)
    if not isinstance(max_results, int) or max_results < 1 or max_results > 20:
        raise ValueError("max_results must be an integer between 1 and 20")
    allowed = input_value.get("allowed_domains")
    blocked = input_value.get("blocked_domains")
    if allowed is not None and (not isinstance(allowed, list) or not all(isinstance(x, str) and x for x in allowed)):
        raise ValueError("allowed_domains must be an array of non-empty strings")
    if blocked is not None and (not isinstance(blocked, list) or not all(isinstance(x, str) and x for x in blocked)):
        raise ValueError("blocked_domains must be an array of non-empty strings")
    if allowed and blocked:
        raise ValueError("Cannot specify both allowed_domains and blocked_domains in one request.")
    return {"query": input_value["query"], "max_results": max_results, "allowed_domains": allowed, "blocked_domains": blocked}


async def _run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await search_duckduckgo_lite({
            "query": input_value["query"],
            "maxResults": input_value.get("max_results") or 5,
            "allowedDomains": input_value.get("allowed_domains"),
            "blockedDomains": input_value.get("blocked_domains"),
        })
        organic = result.get("organic") or []
        if not organic:
            return {"ok": True, "output": "No results found."}
        lines = [f"QUERY: {input_value['query']}", ""]
        for i, item in enumerate(organic, 1):
            lines.append(f"[{i}] {item.get('title')}")
            lines.append(f"    URL: {item.get('link')}")
            if item.get("snippet"):
                lines.append(f"    {item.get('snippet')}")
            lines.append("")
        return {"ok": True, "output": "\n".join(lines).rstrip()}
    except Exception as error:
        return {"ok": False, "output": f"Web search failed: {error}"}


web_search_tool = ToolDefinition(
    name="web_search",
    description="Search the public web using DuckDuckGo. Use this for current information, documentation, or anything outside the local workspace.",
    input_schema={"type": "object", "properties": {"query": {"type": "string", "description": "Search query."}, "max_results": {"type": "number", "description": "Maximum number of results to return. Defaults to 5."}, "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "Only return results from these domains."}, "blocked_domains": {"type": "array", "items": {"type": "string"}, "description": "Exclude results from these domains."}}, "required": ["query"]},
    validator=_validate,
    run=_run,
)

webSearchTool = web_search_tool
