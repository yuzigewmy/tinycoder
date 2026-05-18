from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .config import TINYCODER_SETTINGS_PATH, load_effective_settings, save_tinycoder_settings


def has_path_entry(target: str) -> bool:
    return target in os.environ.get("PATH", "").split(os.pathsep)


def ask_required(label: str, default_value: str | None = None) -> str:
    while True:
        suffix = f" [{default_value}]" if default_value else ""
        answer = input(f"{label}{suffix}: ").strip()
        value = answer or default_value or ""
        if value:
            return value
        print("该项不能为空，请重新输入。")


def secret_prompt_suffix(secret: str | None = None) -> str:
    return " [saved]" if secret else " [not set]"


async def main() -> None:
    settings = await load_effective_settings()
    current_env = settings.get("env") or {}
    print("tinycoder installer")
    print(f"配置会写入 {TINYCODER_SETTINGS_PATH}")
    print("配置保存在独立目录中，不会影响其它本地工具配置。")
    print("")
    model = ask_required("Model name", str(settings.get("model") or current_env.get("ANTHROPIC_MODEL") or ""))
    base_url = ask_required("ANTHROPIC_BASE_URL", str(current_env.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"))
    saved_token = str(current_env.get("ANTHROPIC_AUTH_TOKEN") or "")
    token_input = input(f"ANTHROPIC_AUTH_TOKEN{secret_prompt_suffix(saved_token)}: ").strip()
    auth_token = token_input or saved_token
    if not auth_token:
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN 不能为空。")
    await save_tinycoder_settings({"model": model, "env": {"ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_AUTH_TOKEN": auth_token, "ANTHROPIC_MODEL": model}})
    home = Path.home()
    target_bin_dir = Path(os.environ.get("TINYCODER_BIN_DIR") or home / ".local" / "bin").resolve()
    launcher_path = target_bin_dir / "tinycoder"
    repo_root = Path(__file__).resolve().parents[1]
    launcher_script = "\n".join(["#!/usr/bin/env bash", "set -euo pipefail", f'exec "{repo_root / "bin" / "tinycoder"}" "$@"', ""])
    target_bin_dir.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text(launcher_script, encoding="utf-8")
    launcher_path.chmod(0o755)
    print("")
    print("安装完成。")
    print(f"配置文件: {TINYCODER_SETTINGS_PATH}")
    print(f"启动命令: {launcher_path}")
    if not has_path_entry(str(target_bin_dir)):
        print("")
        print(f"你的 PATH 里还没有 {target_bin_dir}")
        print("可以把下面这行加入 ~/.bashrc 或 ~/.zshrc:")
        print(f'export PATH="{target_bin_dir}:$PATH"')
    else:
        print("")
        print("现在你可以在任意终端输入 `tinycoder` 启动。")


def main_sync() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
