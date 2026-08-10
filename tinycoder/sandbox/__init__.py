"""TinyCoder sandbox — process isolation via Docker / Podman containers.

Mirrors the qwen-code sandbox design:
- Detects available container runtimes (docker, podman)
- Builds or reuses a sandbox container image
- Re-launches TinyCoder inside the container when sandbox mode is enabled
- Mounts the workspace, ~/.tinycoder, and temp directories into the container
"""

from .config import SandboxConfig, load_sandbox_config
from .provider import detect_sandbox_provider
from .sandbox import start_sandbox
from .image import ensure_sandbox_image

__all__ = [
    "SandboxConfig",
    "load_sandbox_config",
    "detect_sandbox_provider",
    "start_sandbox",
    "ensure_sandbox_image",
]
