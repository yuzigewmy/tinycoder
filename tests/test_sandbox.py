"""Tests for the TinyCoder sandbox module."""

from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tinycoder.sandbox.config import (
    SandboxConfig,
    load_sandbox_config,
    _resolve_sandbox_image,
)
from tinycoder.sandbox.provider import (
    is_inside_sandbox,
    reset_probe_cache_for_test,
)
from tinycoder.sandbox.sandbox import _container_path


class TestContainerPath:
    def test_unix_path_unchanged(self):
        assert _container_path("/home/user/project") == "/home/user/project"

    def test_windows_path_converted(self):
        if sys.platform == "win32":
            assert _container_path("C:\\Users\\test") == "/c/Users/test"

    def test_mixed_slashes(self):
        if sys.platform == "win32":
            result = _container_path("C:/Users/test")
            assert result == "/c/Users/test"


class TestSandboxConfig:
    def setup_method(self):
        reset_probe_cache_for_test()
        for key in ("TINYCODER_SANDBOX", "TINYCODER_SANDBOX_IMAGE"):
            os.environ.pop(key, None)

    def test_disabled_by_default(self):
        config = load_sandbox_config()
        assert config is None

    def test_disabled_when_inside_sandbox(self):
        os.environ["TINYCODER_SANDBOX"] = "container-123"
        config = load_sandbox_config(sandbox="true")
        assert config is None

    def test_resolve_image_default(self):
        image = _resolve_sandbox_image()
        assert image == "tinycoder/tinycoder-sandbox:latest"

    def test_resolve_image_from_env(self):
        os.environ["TINYCODER_SANDBOX_IMAGE"] = "myregistry/sandbox:v1"
        image = _resolve_sandbox_image()
        assert image == "myregistry/sandbox:v1"

    def test_resolve_image_from_settings(self):
        image = _resolve_sandbox_image(settings_image="settings/image:v2")
        assert image == "settings/image:v2"

    def test_resolve_image_cli_wins(self):
        os.environ["TINYCODER_SANDBOX_IMAGE"] = "env/image:v1"
        image = _resolve_sandbox_image(
            sandbox_image="cli/image:v3",
            settings_image="settings/image:v2",
        )
        assert image == "cli/image:v3"


class TestIsInsideSandbox:
    def setup_method(self):
        os.environ.pop("TINYCODER_SANDBOX", None)

    def test_not_inside_by_default(self):
        assert is_inside_sandbox() is False

    def test_inside_when_env_set(self):
        os.environ["TINYCODER_SANDBOX"] = "container-abc"
        assert is_inside_sandbox() is True

    def test_empty_env_not_inside(self):
        os.environ["TINYCODER_SANDBOX"] = ""
        assert is_inside_sandbox() is False


class TestArgParsing:
    def _parse(self, argv):
        from tinycoder.index import _parse_sandbox_args
        return _parse_sandbox_args(argv)

    def test_no_sandbox_args(self):
        sv, si, rest = self._parse(["--resume", "abc"])
        assert sv is None
        assert si is None
        assert rest == ["--resume", "abc"]

    def test_sandbox_flag(self):
        sv, si, rest = self._parse(["-s"])
        assert sv == "true"
        assert si is None

    def test_sandbox_long_flag(self):
        sv, si, rest = self._parse(["--sandbox"])
        assert sv == "true"
        assert rest == []

    def test_sandbox_with_value(self):
        sv, si, rest = self._parse(["--sandbox", "docker"])
        assert sv == "docker"
        assert rest == []

    def test_sandbox_equals_value(self):
        sv, si, rest = self._parse(["--sandbox=podman"])
        assert sv == "podman"

    def test_sandbox_image(self):
        sv, si, rest = self._parse(["--sandbox-image", "myimg:v1"])
        assert sv is None
        assert si == "myimg:v1"

    def test_sandbox_image_equals(self):
        sv, si, rest = self._parse(["--sandbox-image=myimg:v2"])
        assert si == "myimg:v2"

    def test_sandbox_with_other_args(self):
        sv, si, rest = self._parse(["-s", "--resume", "abc"])
        assert sv == "true"
        assert rest == ["--resume", "abc"]

    def test_sandbox_value_with_other_args(self):
        sv, si, rest = self._parse(["--sandbox", "docker", "--resume", "abc"])
        assert sv == "docker"
        assert rest == ["--resume", "abc"]
