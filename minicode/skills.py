from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Literal

SkillSource = Literal["project", "user", "compat_project", "compat_user"]
SkillScope = Literal["user", "project"]


def extract_description(markdown: str) -> str:
    normalized = markdown.replace("\r\n", "\n")
    paragraphs = [block.strip() for block in normalized.split("\n\n") if block.strip()]
    for block in paragraphs:
        if block.startswith("#"):
            continue
        for part in block.split("\n"):
            line = part.strip()
            if line and not line.startswith("#"):
                return line.replace("`", "")
    return "No description provided."


def get_skill_roots(cwd: str) -> list[dict[str, str]]:
    home = str(Path.home())
    return [
        {"root": str(Path(cwd) / ".mini-code" / "skills"), "source": "project"},
        {"root": str(Path(home) / ".mini-code" / "skills"), "source": "user"},
        {"root": str(Path(cwd) / ".claude" / "skills"), "source": "compat_project"},
        {"root": str(Path(home) / ".claude" / "skills"), "source": "compat_user"},
    ]


def get_managed_skill_root(scope: SkillScope, cwd: str) -> str:
    return str((Path(cwd) if scope == "project" else Path.home()) / ".mini-code" / "skills")


def _list_skill_dirs(root: dict[str, str]) -> list[dict[str, Any]]:
    root_path = Path(root["root"])
    try:
        entries = sorted(root_path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    results: list[dict[str, Any]] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        skill_path = entry / "SKILL.md"
        try:
            content = skill_path.read_text(encoding="utf-8")
        except OSError:
            continue
        results.append({
            "name": entry.name,
            "description": extract_description(content),
            "path": str(skill_path),
            "source": root["source"],
            "content": content,
        })
    return results


async def discover_skills(cwd: str) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for root in get_skill_roots(cwd):
        for skill in _list_skill_dirs(root):
            by_name.setdefault(skill["name"], skill)
    return [{k: skill[k] for k in ["name", "description", "path", "source"]} for skill in by_name.values()]


async def load_skill(cwd: str, name: str) -> dict[str, Any] | None:
    normalized_name = name.strip()
    if not normalized_name:
        return None
    for root in get_skill_roots(cwd):
        skill_path = Path(root["root"]) / normalized_name / "SKILL.md"
        try:
            content = skill_path.read_text(encoding="utf-8")
        except OSError:
            continue
        return {
            "name": normalized_name,
            "description": extract_description(content),
            "path": str(skill_path),
            "source": root["source"],
            "content": content,
        }
    return None


async def install_skill(args: dict[str, Any]) -> dict[str, str]:
    cwd = str(args["cwd"])
    source_path = str(args["sourcePath"])
    scope: SkillScope = args.get("scope") or "user"
    stat_path = (Path(cwd) / source_path).resolve()
    content: str
    inferred_name: str
    if stat_path.is_dir():
        skill_file = stat_path / "SKILL.md"
        if not skill_file.is_file():
            raise RuntimeError(f"No SKILL.md found in {stat_path}")
        content = skill_file.read_text(encoding="utf-8")
        inferred_name = stat_path.name
    else:
        file_path = stat_path if stat_path.name == "SKILL.md" else stat_path / "SKILL.md"
        content = file_path.read_text(encoding="utf-8")
        inferred_name = file_path.parent.name
    skill_name = str(args.get("name") or inferred_name).strip()
    if not skill_name:
        raise RuntimeError("Skill name cannot be empty.")
    target_root = Path(get_managed_skill_root(scope, cwd))
    target_dir = target_root / skill_name
    target_path = target_dir / "SKILL.md"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return {"name": skill_name, "targetPath": str(target_path)}


async def remove_managed_skill(args: dict[str, Any]) -> dict[str, Any]:
    cwd = str(args["cwd"])
    scope: SkillScope = args.get("scope") or "user"
    target_path = Path(get_managed_skill_root(scope, cwd)) / str(args["name"])
    if not target_path.exists():
        return {"removed": False, "targetPath": str(target_path)}
    shutil.rmtree(target_path)
    return {"removed": True, "targetPath": str(target_path)}


extractDescription = extract_description
getSkillRoots = get_skill_roots
getManagedSkillRoot = get_managed_skill_root
discoverSkills = discover_skills
loadSkill = load_skill
installSkill = install_skill
removeManagedSkill = remove_managed_skill
