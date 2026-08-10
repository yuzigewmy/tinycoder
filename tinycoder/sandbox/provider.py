"""Sandbox provider detection — mirrors qwen-code sandboxConfig.ts provider detection."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

from .config import SANDBOX_PROBE_TIMEOUT_SECONDS, VALID_SANDBOX_COMMANDS

_probe_cache: dict[str, Optional[str]] = {}


def detect_sandbox_provider() -> Optional[str]:
    """Auto-detect the best available container runtime.

    Returns 'docker' or 'podman' if found and working, or None.
    """
    for candidate in ("docker", "podman"):
        if shutil.which(candidate) is None:
            continue
        if candidate in _probe_cache:
            if _probe_cache[candidate] is None:
                return candidate
            continue
        try:
            result = subprocess.run(
                [candidate, "version"],
                capture_output=True,
                text=True,
                timeout=SANDBOX_PROBE_TIMEOUT_SECONDS,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            _probe_cache[candidate] = "unavailable"
            continue
        if result.returncode == 0:
            _probe_cache[candidate] = None
            return candidate
        _probe_cache[candidate] = f"exit code {result.returncode}"
    return None


def is_inside_sandbox() -> bool:
    """Check whether we are already running inside the sandbox container."""
    return bool(os.environ.get("TINYCODER_SANDBOX", "").strip())


def reset_probe_cache_for_test() -> None:
    """Clear the per-process probe cache so tests stay hermetic."""
    _probe_cache.clear()
