"""Sandbox configuration — mirrors qwen-code sandboxConfig.ts.

Resolves the sandbox command (docker / podman) and image from:
1. TINYCODER_SANDBOX env var (highest precedence)
2. --sandbox CLI flag
3. settings.json tools.sandbox

Image sources:
1. --sandbox-image CLI flag
2. TINYCODER_SANDBOX_IMAGE env var
3. settings.json tools.sandboxImage
4. Built-in default
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

LOCAL_DEV_SANDBOX_IMAGE_NAME = "tinycoder-sandbox"
DEFAULT_SANDBOX_IMAGE = "tinycoder/tinycoder-sandbox:latest"
VALID_SANDBOX_COMMANDS = frozenset({"docker", "podman"})
SANDBOX_PROBE_TIMEOUT_SECONDS = 5

_probe_cache: dict[str, Optional[str]] = {}


@dataclass
class SandboxConfig:
    """Resolved sandbox configuration."""
    command: str = ""
    image: str = ""


def _probe_sandbox_command(command: str) -> Optional[str]:
    """Confirm a sandbox command can actually contact its daemon."""
    if command not in VALID_SANDBOX_COMMANDS:
        return "unknown sandbox command"
    if command in _probe_cache:
        return _probe_cache[command]
    try:
        result = subprocess.run(
            [command, "version"],
            capture_output=True,
            text=True,
            timeout=SANDBOX_PROBE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        failure = f"'{command}' not found on PATH"
        _probe_cache[command] = failure
        return failure
    except subprocess.TimeoutExpired:
        failure = f"'{command} version' timed out after {SANDBOX_PROBE_TIMEOUT_SECONDS}s"
        _probe_cache[command] = failure
        return failure
    except OSError as exc:
        failure = f"'{command}' failed: {exc}"
        _probe_cache[command] = failure
        return failure
    if result.returncode == 0:
        _probe_cache[command] = None
        return None
    output = (result.stderr or "") + "\n" + (result.stdout or "")
    first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
    failure = first_line or f"'{command} version' exited with {result.returncode}"
    _probe_cache[command] = failure
    return failure


def _resolve_sandbox_command(sandbox_value: Optional[str]) -> str:
    """Determine which container runtime to use. Returns '' when disabled."""
    if os.environ.get("TINYCODER_SANDBOX"):
        return ""
    env_sandbox = os.environ.get("TINYCODER_SANDBOX", "").strip().lower()
    if env_sandbox:
        sandbox_value = env_sandbox
    if sandbox_value in ("1", "true"):
        sandbox_value = "true"
    elif sandbox_value in ("0", "false", "", None):
        return ""
    if sandbox_value == "true":
        for candidate in ("docker", "podman"):
            if shutil.which(candidate) and _probe_sandbox_command(candidate) is None:
                return candidate
        raise RuntimeError(
            "Sandbox is enabled but no working container runtime was found. "
            "Install Docker or Podman, or set TINYCODER_SANDBOX=docker|podman."
        )
    if sandbox_value not in VALID_SANDBOX_COMMANDS:
        raise RuntimeError(
            f"Invalid sandbox command '{sandbox_value}'. "
            f"Must be one of: {', '.join(sorted(VALID_SANDBOX_COMMANDS))}."
        )
    if not shutil.which(sandbox_value):
        raise RuntimeError(f"Sandbox command '{sandbox_value}' not found on PATH.")
    failure = _probe_sandbox_command(sandbox_value)
    if failure:
        raise RuntimeError(
            f"Sandbox command '{sandbox_value}' is installed but cannot run: {failure}"
        )
    return sandbox_value


def _resolve_sandbox_image(
    sandbox_image: Optional[str] = None,
    settings_image: Optional[str] = None,
) -> str:
    """Resolve the sandbox container image."""
    image = (
        sandbox_image
        or os.environ.get("TINYCODER_SANDBOX_IMAGE", "").strip()
        or settings_image
        or DEFAULT_SANDBOX_IMAGE
    )
    return image


def load_sandbox_config(
    *,
    sandbox: Optional[str] = None,
    sandbox_image: Optional[str] = None,
    settings: Optional[dict] = None,
) -> Optional[SandboxConfig]:
    """Load sandbox configuration. Returns None when sandbox is disabled."""
    if settings is None:
        settings = {}
    tools = settings.get("tools") or {}
    sandbox_value = sandbox or tools.get("sandbox")
    if sandbox_value is not None:
        sandbox_value = str(sandbox_value)
    try:
        command = _resolve_sandbox_command(sandbox_value)
    except RuntimeError:
        raise
    if not command:
        return None
    image = _resolve_sandbox_image(
        sandbox_image=sandbox_image,
        settings_image=tools.get("sandboxImage"),
    )
    return SandboxConfig(command=command, image=image)
