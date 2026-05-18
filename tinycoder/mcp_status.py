from __future__ import annotations

from typing import Any


def summarize_mcp_servers(mcp_servers: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"total": 0, "connected": 0, "connecting": 0, "error": 0, "toolCount": 0}
    for server in mcp_servers:
        summary["total"] += 1
        summary["toolCount"] += int(server.get("toolCount") or 0)
        status = server.get("status")
        if status == "connected":
            summary["connected"] += 1
        elif status == "connecting":
            summary["connecting"] += 1
        elif status == "error":
            summary["error"] += 1
    return summary


summarizeMcpServers = summarize_mcp_servers
