"""Sandbox image management — mirrors qwen-code sandbox.ts image handling."""

from __future__ import annotations

import subprocess
import sys
from typing import Optional


def image_exists(sandbox_command: str, image: str) -> bool:
    """Check whether a container image exists locally."""
    try:
        result = subprocess.run(
            [sandbox_command, "images", "-q", image],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return bool(result.stdout.strip())


def pull_image(sandbox_command: str, image: str) -> bool:
    """Pull a container image. Returns True on success."""
    print(f"Pulling sandbox image '{image}' via {sandbox_command}...", file=sys.stderr)
    try:
        result = subprocess.run(
            [sandbox_command, "pull", image],
            stdout=sys.stderr,
            stderr=sys.stderr,
            timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"Failed to pull image: {exc}", file=sys.stderr)
        return False
    if result.returncode == 0:
        print(f"Successfully pulled '{image}'.", file=sys.stderr)
        return True
    print(f"Failed to pull '{image}' (exit code {result.returncode}).", file=sys.stderr)
    return False


def ensure_sandbox_image(sandbox_command: str, image: str) -> bool:
    """Ensure the sandbox image is available locally, pulling if needed.

    Returns True when the image is ready, False otherwise.
    """
    print(f"Checking for sandbox image: {image}", file=sys.stderr)
    if image_exists(sandbox_command, image):
        print(f"Sandbox image '{image}' found locally.", file=sys.stderr)
        return True

    print(f"Sandbox image '{image}' not found locally.", file=sys.stderr)

    # For local dev builds, the user must build the image themselves.
    if image == "tinycoder-sandbox":
        print(
            "This is a local dev image. Build it with: docker build -t tinycoder-sandbox .",
            file=sys.stderr,
        )
        return False

    if pull_image(sandbox_command, image):
        if image_exists(sandbox_command, image):
            return True
        print(
            f"Image '{image}' still not available after pull — "
            "registry may be unreachable or the image name is wrong.",
            file=sys.stderr,
        )
        return False

    return False


def build_sandbox_image(
    sandbox_command: str,
    image: str,
    dockerfile_path: str,
    context_path: str = ".",
) -> bool:
    """Build the sandbox Docker image locally."""
    print(f"Building sandbox image '{image}' via {sandbox_command}...", file=sys.stderr)
    try:
        result = subprocess.run(
            [
                sandbox_command,
                "build",
                "-t",
                image,
                "-f",
                dockerfile_path,
                context_path,
            ],
            stdout=sys.stderr,
            stderr=sys.stderr,
            timeout=600,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return False
    if result.returncode == 0:
        print(f"Successfully built '{image}'.", file=sys.stderr)
        return True
    print(f"Build failed (exit code {result.returncode}).", file=sys.stderr)
    return False
