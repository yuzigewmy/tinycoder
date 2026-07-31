from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tinycoder import config
from tinycoder.cli_commands import try_handle_local_command


class RuntimeConfigTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings_path = self.root / "settings.json"
        self.claude_settings_path = self.root / "claude-settings.json"
        self.global_mcp_path = self.root / "mcp.json"
        self.project_mcp_path = self.root / "project-mcp.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_settings(self, settings: dict[str, object]) -> None:
        self.settings_path.write_text(
            json.dumps(settings, ensure_ascii=False),
            encoding="utf-8",
        )

    def config_path_patches(self) -> tuple[patch, ...]:
        return (
            patch.object(config, "TINYCODER_DIR", self.root),
            patch.object(config, "TINYCODER_SETTINGS_PATH", self.settings_path),
            patch.object(
                config,
                "CLAUDE_SETTINGS_PATH",
                self.claude_settings_path,
            ),
            patch.object(config, "TINYCODER_MCP_PATH", self.global_mcp_path),
            patch.object(config, "PROJECT_MCP_PATH", self.project_mcp_path),
        )

    async def test_runtime_model_configuration_ignores_process_environment(
        self,
    ) -> None:
        self.write_settings(
            {
                "env": {
                    "TINYCODER_MODEL_PROVIDER": "deepseek",
                    "TINYCODER_MODEL": "deepseek-v4-pro",
                    "TINYCODER_DEEPSEEK_API_KEY": "settings-api-key",
                    "TINYCODER_DEEPSEEK_BASE_URL": "https://api.deepseek.test/v1",
                },
                "model": "deepseek-v4-pro",
                "maxOutputTokens": 4096,
                "customProviders": {
                    "deepseek": {
                        "type": "openai",
                        "model": "deepseek-v4-pro",
                        "apiKey": "settings-api-key",
                        "baseUrl": "https://api.deepseek.test/v1",
                    }
                },
            }
        )
        environment = {
            "TINYCODER_MODEL_PROVIDER": "qwen",
            "TINYCODER_MODEL": "qwen3.6-plus",
            "QWEN_API_KEY": "environment-api-key",
            "QWEN_BASE_URL": "https://qwen.example/v1",
            "TINYCODER_MAX_OUTPUT_TOKENS": "9999",
        }
        path_patches = self.config_path_patches()

        with ExitStack() as stack:
            for path_patch in path_patches:
                stack.enter_context(path_patch)
            stack.enter_context(patch.dict(os.environ, environment, clear=True))
            runtime = await config.load_runtime_config()

        self.assertEqual(runtime["provider"], "deepseek")
        self.assertEqual(runtime["providerType"], "openai")
        self.assertEqual(runtime["model"], "deepseek-v4-pro")
        self.assertEqual(runtime["baseUrl"], "https://api.deepseek.test/v1")
        self.assertEqual(runtime["apiKey"], "settings-api-key")
        self.assertEqual(runtime["maxOutputTokens"], 4096)
        self.assertNotIn("process.env", runtime["sourceSummary"])
        self.assertNotIn(".claude", runtime["sourceSummary"])

    async def test_model_command_uses_provider_saved_in_tinycoder_settings(
        self,
    ) -> None:
        self.write_settings(
            {
                "env": {
                    "TINYCODER_MODEL_PROVIDER": "deepseek",
                    "TINYCODER_MODEL": "deepseek-v4-pro",
                },
                "model": "deepseek-v4-pro",
                "customProviders": {
                    "deepseek": {
                        "type": "openai",
                        "model": "deepseek-v4-pro",
                        "apiKey": "settings-api-key",
                        "baseUrl": "https://api.deepseek.test/v1",
                    }
                },
            }
        )
        path_patches = self.config_path_patches()

        with ExitStack() as stack:
            for path_patch in path_patches:
                stack.enter_context(path_patch)
            stack.enter_context(
                patch.dict(
                    os.environ,
                    {"TINYCODER_MODEL_PROVIDER": "qwen"},
                    clear=True,
                )
            )
            result = await try_handle_local_command("/model deepseek-v4.1")

        persisted = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertIn("provider=deepseek", result or "")
        self.assertEqual(
            persisted["env"]["TINYCODER_MODEL_PROVIDER"],
            "deepseek",
        )
        self.assertEqual(persisted["model"], "deepseek-v4.1")


if __name__ == "__main__":
    unittest.main()
