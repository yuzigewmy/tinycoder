from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from tinycoder.cli_commands import try_handle_local_command
from tinycoder.mcp import create_mcp_helper_tools, create_mcp_tool
from tinycoder.permissions import (
    PermissionManager,
    PermissionMode,
    RiskLevel,
)
from tinycoder.tool import ToolRegistry
from tinycoder.tools.run_command import run_command_tool
from tinycoder.tools.web_search import web_search_tool
from tinycoder.tui.chrome import render_banner


class PermissionModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store_path = self.root / "permissions.json"
        self.requests: list[dict[str, Any]] = []
        self.next_decision = "allow_once"

        async def prompt(request: dict[str, Any]) -> dict[str, Any]:
            self.requests.append(request)
            return {"decision": self.next_decision}

        self.prompt = prompt

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def manager(self, mode: PermissionMode | None = None) -> PermissionManager:
        return PermissionManager(
            str(self.workspace),
            prompt=self.prompt,
            mode=mode,
            store_path=self.store_path,
        )

    async def test_default_mode_is_request_approval_and_mode_persists(self) -> None:
        manager = self.manager()
        self.assertEqual(manager.mode, "request_approval")

        await manager.set_mode("auto_approve")
        reloaded = self.manager()

        self.assertEqual(reloaded.mode, "auto_approve")
        self.assertEqual(
            json.loads(self.store_path.read_text(encoding="utf-8"))["mode"],
            "auto_approve",
        )

    async def test_mode_persistence_preserves_unknown_future_fields(self) -> None:
        self.store_path.write_text(
            json.dumps(
                {
                    "mode": "request_approval",
                    "futurePolicy": {"enabled": True},
                }
            ),
            encoding="utf-8",
        )
        manager = self.manager()

        await manager.set_mode("auto_approve")

        persisted = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["futurePolicy"], {"enabled": True})

    async def test_failed_mode_persistence_rolls_back_runtime_state(self) -> None:
        manager = self.manager()
        manager.session_allowed_paths.add("temporary-allowance")

        with (
            patch.object(
                manager,
                "_persist",
                new=AsyncMock(side_effect=OSError("disk full")),
            ),
            self.assertRaisesRegex(OSError, "disk full"),
        ):
            await manager.set_mode("auto_approve")

        self.assertEqual(manager.mode, "request_approval")
        self.assertIn("temporary-allowance", manager.session_allowed_paths)

    async def test_full_access_requires_explicit_confirmation(self) -> None:
        manager = self.manager()

        with self.assertRaisesRegex(ValueError, "confirmation"):
            await manager.set_mode("full_access")

        await manager.set_mode("full_access", confirm_full_access=True)
        self.assertEqual(manager.mode, "full_access")

    async def test_request_mode_allows_workspace_edit_but_prompts_for_escape(self) -> None:
        manager = self.manager("request_approval")
        target = self.workspace / "app.py"

        await manager.ensure_edit(str(target), "+print('ok')")
        self.assertEqual(self.requests, [])

        outside = self.root / "outside.txt"
        await manager.ensure_path_access(str(outside), "read")

        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["kind"], "path")
        self.assertEqual(self.requests[0]["risk"], "medium")

    async def test_auto_approve_handles_medium_risk_without_prompt(self) -> None:
        manager = self.manager("auto_approve")
        outside = self.root / "public.txt"

        await manager.ensure_path_access(str(outside), "read")
        await manager.ensure_external_action(
            "web_search",
            {"query": "Python documentation"},
            risk="medium",
            reason="public web request",
        )

        self.assertEqual(self.requests, [])
        self.assertEqual(
            [review["decision"] for review in manager.get_review_history()],
            ["allow", "allow"],
        )

    async def test_auto_approve_denies_critical_action_fail_closed(self) -> None:
        manager = self.manager("auto_approve")

        with self.assertRaisesRegex(RuntimeError, "auto-review denied"):
            await manager.ensure_command(
                "git",
                ["reset", "--hard", "HEAD"],
                str(self.workspace),
            )

        with self.assertRaisesRegex(RuntimeError, "auto-review denied"):
            await manager.ensure_external_action(
                "mcp__github__delete_repository",
                {"repository": "important"},
                risk="critical",
                reason="destructive MCP action",
            )

        self.assertEqual(self.requests, [])
        self.assertEqual(
            [review["risk"] for review in manager.get_review_history()],
            ["critical", "critical"],
        )

    async def test_auto_approve_routes_high_risk_to_user(self) -> None:
        manager = self.manager("auto_approve")

        await manager.ensure_command(
            "python",
            ["-c", "print('hello')"],
            str(self.workspace),
        )

        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["risk"], "high")
        self.assertIn("automatic reviewer", self.requests[0]["summary"])

    async def test_network_and_package_code_execution_receive_risk_reviews(self) -> None:
        manager = self.manager("auto_approve")

        await manager.ensure_command(
            "git",
            ["fetch", "origin"],
            str(self.workspace),
        )
        await manager.ensure_command(
            "npm",
            ["install"],
            str(self.workspace),
        )
        await manager.ensure_command(
            "pytest",
            ["-q"],
            str(self.workspace),
        )

        self.assertEqual(
            [record["risk"] for record in manager.get_review_history()],
            ["medium", "high", "high"],
        )
        self.assertEqual(
            [record["decision"] for record in manager.get_review_history()],
            ["allow", "require_user", "require_user"],
        )
        self.assertEqual(
            [request["risk"] for request in self.requests],
            ["high", "high"],
        )

    async def test_auto_approve_denies_high_risk_without_interactive_prompt(self) -> None:
        manager = PermissionManager(
            str(self.workspace),
            mode="auto_approve",
            store_path=self.store_path,
        )

        with self.assertRaisesRegex(RuntimeError, "requires user approval"):
            await manager.ensure_path_access(str(self.root / "outside.txt"), "write")

    async def test_sensitive_payload_is_never_silently_exfiltrated(self) -> None:
        manager = self.manager("auto_approve")

        with self.assertRaisesRegex(RuntimeError, "auto-review denied"):
            await manager.ensure_external_action(
                "web_search",
                {"query": "auth_token=test-secret-value-123456789"},
                risk="medium",
                reason="public web request",
            )

        review = manager.get_review_history()[-1]
        self.assertEqual(review["risk"], "critical")
        self.assertEqual(review["decision"], "deny")

    async def test_full_access_bypasses_application_permission_checks(self) -> None:
        manager = self.manager("full_access")

        await manager.ensure_path_access(str(self.root / ".ssh" / "id_rsa"), "read")
        await manager.ensure_command(
            "git",
            ["reset", "--hard", "HEAD"],
            str(self.root),
        )
        await manager.ensure_external_action(
            "mcp__example__destroy",
            {"confirm": True},
            risk="critical",
            reason="destructive external action",
        )

        self.assertEqual(self.requests, [])

    async def test_permission_command_reports_and_changes_modes(self) -> None:
        manager = self.manager()

        status = await try_handle_local_command(
            "/permissions",
            {"permissions": manager},
        )
        self.assertIn("请求批准", status or "")
        self.assertIn("应用层", status or "")

        changed = await try_handle_local_command(
            "/permissions auto-approve",
            {"permissions": manager},
        )
        self.assertEqual(manager.mode, "auto_approve")
        self.assertIn("替我审批", changed or "")

        warning = await try_handle_local_command(
            "/permissions full-access",
            {"permissions": manager},
        )
        self.assertEqual(manager.mode, "auto_approve")
        self.assertIn("confirm", warning or "")

        changed = await try_handle_local_command(
            "/permissions full-access confirm",
            {"permissions": manager},
        )
        self.assertEqual(manager.mode, "full_access")
        self.assertIn("完全访问", changed or "")

    def test_public_mode_and_risk_contracts_are_closed_sets(self) -> None:
        modes: set[PermissionMode] = {
            "request_approval",
            "auto_approve",
            "full_access",
        }
        risks: set[RiskLevel] = {"low", "medium", "high", "critical"}

        self.assertEqual(len(modes), 3)
        self.assertEqual(len(risks), 4)


class PermissionToolIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.requests: list[dict[str, Any]] = []

        async def prompt(request: dict[str, Any]) -> dict[str, Any]:
            self.requests.append(request)
            return {"decision": "allow_once"}

        self.prompt = prompt

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def manager(self, mode: PermissionMode) -> PermissionManager:
        return PermissionManager(
            str(self.workspace),
            prompt=self.prompt,
            mode=mode,
            store_path=self.root / f"{mode}.json",
        )

    async def test_read_only_command_still_checks_sensitive_arguments(self) -> None:
        manager = self.manager("auto_approve")
        registry = ToolRegistry([run_command_tool])

        result = await registry.execute(
            "run_command",
            {"command": "cat", "args": [str(self.root / ".ssh" / "id_rsa")]},
            {"cwd": str(self.workspace), "permissions": manager},
        )

        self.assertFalse(result["ok"])
        self.assertIn("auto-review denied", result["output"])
        self.assertEqual(manager.get_review_history()[-1]["risk"], "critical")

    async def test_command_path_escape_enters_the_path_permission_policy(self) -> None:
        manager = self.manager("request_approval")
        registry = ToolRegistry([run_command_tool])
        outside = self.root / "public.txt"

        with patch(
            "tinycoder.tools.run_command.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout="public",
                stderr="",
            ),
        ):
            result = await registry.execute(
                "run_command",
                {"command": "cat", "args": [str(outside)]},
                {"cwd": str(self.workspace), "permissions": manager},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(self.requests[0]["kind"], "path")
        self.assertEqual(self.requests[0]["risk"], "medium")

    async def test_web_search_passes_through_external_action_policy(self) -> None:
        manager = self.manager("request_approval")
        registry = ToolRegistry([web_search_tool])

        with patch(
            "tinycoder.tools.web_search.search_duckduckgo_lite",
            new=AsyncMock(return_value={"organic": []}),
        ) as search:
            result = await registry.execute(
                "web_search",
                {"query": "Python docs"},
                {"cwd": str(self.workspace), "permissions": manager},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(self.requests[0]["kind"], "external")
        self.assertEqual(self.requests[0]["risk"], "medium")
        search.assert_awaited_once()

    async def test_mcp_annotations_drive_auto_review_risk(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            async def call_tool(
                self,
                name: str,
                input_value: dict[str, Any],
            ) -> dict[str, Any]:
                self.calls.append((name, input_value))
                return {"ok": True, "output": "done"}

        client = FakeClient()
        manager = self.manager("auto_approve")
        read_tool = create_mcp_tool(
            client,
            "docs",
            {
                "name": "lookup",
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "openWorldHint": True,
                },
            },
        )
        destructive_tool = create_mcp_tool(
            client,
            "admin",
            {
                "name": "delete_all",
                "annotations": {"destructiveHint": True},
            },
        )
        registry = ToolRegistry([read_tool, destructive_tool])

        allowed = await registry.execute(
            read_tool.name,
            {"query": "public docs"},
            {"cwd": str(self.workspace), "permissions": manager},
        )
        denied = await registry.execute(
            destructive_tool.name,
            {"confirm": True},
            {"cwd": str(self.workspace), "permissions": manager},
        )

        self.assertTrue(allowed["ok"])
        self.assertFalse(denied["ok"])
        self.assertIn("auto-review denied", denied["output"])
        self.assertEqual(client.calls, [("lookup", {"query": "public docs"})])
        self.assertEqual(
            [record["risk"] for record in manager.get_review_history()],
            ["medium", "critical"],
        )

    async def test_mcp_resource_helpers_use_the_same_external_policy(self) -> None:
        class FakeClient:
            async def list_resources(self) -> list[dict[str, str]]:
                return [{"uri": "docs://guide", "name": "guide"}]

        manager = self.manager("request_approval")
        helper = create_mcp_helper_tools({"docs": FakeClient()})[0]
        registry = ToolRegistry([helper])

        result = await registry.execute(
            helper.name,
            {},
            {"cwd": str(self.workspace), "permissions": manager},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(self.requests[0]["kind"], "external")
        self.assertEqual(self.requests[0]["scope"], "mcp:helpers:list-resources")

    def test_banner_displays_active_permission_mode(self) -> None:
        rendered = render_banner(
            {
                "provider": "deepseek",
                "model": "deepseek-v4",
                "permissionMode": "auto_approve",
                "permissionModeLabel": "替我审批",
            },
            str(self.workspace),
        )

        self.assertIn("当前权限: 替我审批 (auto_approve)", rendered)


if __name__ == "__main__":
    unittest.main()
