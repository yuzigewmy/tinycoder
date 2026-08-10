"""Main sandbox entry point — mirrors qwen-code sandbox.ts.

When sandbox mode is enabled, TinyCoder re-launches itself inside a
Docker/Podman container. The container mounts:
- The workspace directory (cwd)
- ~/.tinycoder (user settings, auth, permissions)
- The system temp directory
- Any custom SANDBOX_MOUNTS paths

Network and environment variables are forwarded as needed.
"""

from __future__ import annotations

import os
import sys
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .config import SandboxConfig, LOCAL_DEV_SANDBOX_IMAGE_NAME
from .image import ensure_sandbox_image
from .provider import is_inside_sandbox


def _container_path(host_path: str) -> str:
    """Convert a host path to its container-side equivalent (Windows support)."""
    if sys.platform != "win32":
        return host_path
    with_forward = host_path.replace("\\", "/")
    import re
    match = re.match(r"^([A-Za-z]):/(.*)", with_forward)
    if match:
        return f"/{match.group(1).lower()}/{match.group(2)}"
    return host_path


def _passthrough_env_args() -> list[str]:
    """Collect env vars that should be forwarded into the sandbox."""
    passthrough = [
        "TINYCODER_DEBUG",
        "TINYCODER_MODEL_PROVIDER",
        "TINYCODER_MODEL",
        "TINYCODER_MAX_OUTPUT_TOKENS",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_AUTH_TOKEN",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_MODEL",
        "QWEN_API_KEY",
        "QWEN_AUTH_TOKEN",
        "QWEN_BASE_URL",
        "QWEN_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "TERM",
        "COLORTERM",
        "NO_PROXY",
        "no_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ]
    args: list[str] = []
    for var in passthrough:
        if os.environ.get(var):
            args.extend(["--env", f"{var}={os.environ[var]}"])
    return args


def _ports_args() -> list[str]:
    """Parse SANDBOX_PORTS into --publish args."""
    ports_env = os.environ.get("SANDBOX_PORTS", "")
    ports = [p.strip() for p in ports_env.split(",") if p.strip()]
    args: list[str] = []
    for port in ports:
        args.extend(["--publish", f"{port}:{port}"])
    return args


def _mounts_args(workdir: str, tinycoder_home: str) -> list[str]:
    """Build docker volume mount arguments."""
    container_workdir = _container_path(workdir)
    container_tmp = _container_path(tempfile.gettempdir())
    container_home = _container_path(tinycoder_home)
    container_home_node = "/home/node/.tinycoder"

    args = [
        "--volume", f"{workdir}:{container_workdir}",
        "--volume", f"{tempfile.gettempdir()}:{container_tmp}",
        "--volume", f"{tinycoder_home}:{container_home_node}",
        "--volume", f"{tinycoder_home}:{container_home}",
    ]

    # Custom mounts from SANDBOX_MOUNTS env var (format: from:to:opts)
    mounts_env = os.environ.get("SANDBOX_MOUNTS", "")
    for mount_spec in mounts_env.split(","):
        mount_spec = mount_spec.strip()
        if not mount_spec:
            continue
        parts = mount_spec.split(":")
        if len(parts) < 1:
            continue
        from_path = parts[0]
        to_path = parts[1] if len(parts) > 1 else from_path
        opts = parts[2] if len(parts) > 2 else "ro"
        if not os.path.isabs(from_path):
            print(f"SANDBOX_MOUNTS: path '{from_path}' must be absolute, skipping", file=sys.stderr)
            continue
        if not os.path.exists(from_path):
            print(f"SANDBOX_MOUNTS: path '{from_path}' does not exist, skipping", file=sys.stderr)
            continue
        print(f"SANDBOX_MOUNTS: {from_path} -> {to_path} ({opts})", file=sys.stderr)
        args.extend(["--volume", f"{from_path}:{to_path}:{opts}"])

    return args


def _resolve_tinycoder_entrypoint(cli_args: list[str]) -> list[str]:
    """Build the container entrypoint that re-invokes tinycoder."""
    # Inside the container we run 'tinycoder' with the same args
    # (minus sandbox flags, because we ARE already inside the sandbox).
    filtered_args: list[str] = []
    skip_next = False
    for i, arg in enumerate(cli_args):
        if skip_next:
            skip_next = False
            continue
        if arg in ("-s", "--sandbox"):
            # Skip the flag and its possible value
            continue
        if arg == "--sandbox-image":
            skip_next = True
            continue
        if arg.startswith("--sandbox="):
            continue
        if arg.startswith("--sandbox-image="):
            continue
        filtered_args.append(arg)

    quoted = [shlex.quote(a) for a in filtered_args]
    cmd = "tinycoder " + " ".join(quoted)
    return ["bash", "-c", cmd]


def start_sandbox(
    config: SandboxConfig,
    cli_args: Optional[list[str]] = None,
    workdir: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> int:
    """Launch TinyCoder inside a sandbox container.

    Returns the container's exit code.
    """
    if is_inside_sandbox():
        print("Already inside sandbox — skipping re-entry.", file=sys.stderr)
        return 0

    if cli_args is None:
        cli_args = sys.argv[1:]
    if workdir is None:
        workdir = os.getcwd()

    print(f"Entering sandbox (command: {config.command})...", file=sys.stderr)

    # Ensure the sandbox image is available
    if not ensure_sandbox_image(config.command, config.image):
        remedy = (
            "Build it locally with: docker build -t tinycoder-sandbox ."
            if config.image == LOCAL_DEV_SANDBOX_IMAGE_NAME
            else "Check the image name, your network connection, or set TINYCODER_SANDBOX_IMAGE."
        )
        print(
            f"Sandbox image '{config.image}' is missing or could not be pulled. {remedy}",
            file=sys.stderr,
        )
        return 1

    container_workdir = _container_path(workdir)
    tinycoder_home = os.environ.get("TINYCODER_HOME", str(Path.home() / ".tinycoder"))

    # Build docker run arguments
    args = [
        config.command,
        "run",
        "-i", "--rm", "--init",
        "--workdir", container_workdir,
    ]

    # TTY only when stdin is a TTY
    if sys.stdin.isatty():
        args.append("-t")

    # Allow host.docker.internal access
    args.extend(["--add-host", "host.docker.internal:host-gateway"])

    # Mounts
    args.extend(_mounts_args(workdir, tinycoder_home))

    # Pass TINYCODER_HOME so the sandboxed process resolves config correctly
    args.extend(["--env", f"TINYCODER_HOME={_container_path(tinycoder_home)}"])

    # Ports
    args.extend(_ports_args())

    # Custom SANDBOX_FLAGS
    sandbox_flags = os.environ.get("SANDBOX_FLAGS", "").strip()
    if sandbox_flags:
        args.extend(shlex.split(sandbox_flags))

    # Environment passthrough
    args.extend(_passthrough_env_args())

    # SANDBOX_ENV: comma-separated KEY=VALUE pairs
    sandbox_env = os.environ.get("SANDBOX_ENV", "").strip()
    if sandbox_env:
        for pair in sandbox_env.split(","):
            pair = pair.strip()
            if "=" in pair:
                args.extend(["--env", pair])

    # Mark that we're inside the sandbox
    container_name = f"tinycoder-sandbox-{os.getpid()}"
    args.extend(["--env", f"TINYCODER_SANDBOX={container_name}"])
    args.extend(["--name", container_name, "--hostname", container_name])

    # Extra env from caller
    if env:
        for key, value in env.items():
            args.extend(["--env", f"{key}={value}"])

    # Container image
    args.append(config.image)

    # Entrypoint
    args.extend(_resolve_tinycoder_entrypoint(cli_args))

    # Spawn and wait
    print(f"Starting sandbox container '{container_name}'...", file=sys.stderr)
    try:
        process = subprocess.run(args)
    except FileNotFoundError:
        print(f"Command '{config.command}' not found.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSandbox interrupted.", file=sys.stderr)
        return 130

    return process.returncode
