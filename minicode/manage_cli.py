from __future__ import annotations

from typing import Any

from .config import MINI_CODE_MCP_TOKENS_PATH, get_mcp_config_path, load_scoped_mcp_servers, read_mcp_tokens_file, save_mcp_tokens_file, save_scoped_mcp_servers
from .skills import discover_skills, install_skill, remove_managed_skill


def print_usage() -> None:
    print("""minicode management commands

minicode mcp list [--project]
minicode mcp add <name> [--project] [--protocol <auto|content-length|newline-json|streamable-http>] [--url <endpoint>] [--header KEY=VALUE ...] [--env KEY=VALUE ...] [-- <command> [args...]]
minicode mcp login <name> --token <bearer-token>
minicode mcp logout <name>
minicode mcp remove <name> [--project]

minicode skills list
minicode skills add <path-to-skill-or-dir> [--name <name>] [--project]
minicode skills remove <name> [--project]""")


def parse_scope(args: list[str]) -> dict[str, Any]:
    rest = list(args)
    if "--project" in rest:
        rest.remove("--project")
        return {"scope": "project", "rest": rest}
    return {"scope": "user", "rest": rest}


def take_option(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    index = args.index(name)
    if index + 1 >= len(args):
        raise RuntimeError(f"Missing value for {name}")
    value = args[index + 1]
    del args[index:index + 2]
    return value


def take_repeat_option(args: list[str], name: str) -> list[str]:
    values: list[str] = []
    while name in args:
        index = args.index(name)
        if index + 1 >= len(args):
            raise RuntimeError(f"Missing value for {name}")
        values.append(args[index + 1])
        del args[index:index + 2]
    return values


def parse_env_pairs(values: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for entry in values:
        if "=" not in entry:
            raise RuntimeError(f"Invalid --env value: {entry}")
        key, value = entry.split("=", 1)
        key = key.strip()
        if not key:
            raise RuntimeError(f"Invalid --env value: {entry}")
        env[key] = value
    return env


async def handle_mcp_command(cwd: str, args: list[str]) -> bool:
    if not args:
        print_usage(); return True
    subcommand, rest_args = args[0], args[1:]
    parsed = parse_scope(rest_args)
    scope = parsed["scope"]; rest = parsed["rest"]
    if subcommand == "list":
        servers = await load_scoped_mcp_servers(scope, cwd)
        if not servers:
            print(f"No MCP servers configured in {get_mcp_config_path(scope, cwd)}.")
            return True
        for name, server in servers.items():
            endpoint = str(server.get("url") or "").strip() or f"{server.get('command') or ''} {' '.join(server.get('args') or [])}".strip()
            protocol = f" protocol={server.get('protocol')}" if server.get("protocol") else ""
            print(f"{name}: {endpoint}{protocol}".strip())
        return True
    if subcommand == "add":
        sep = rest.index("--") if "--" in rest else -1
        head = rest[:] if sep == -1 else rest[:sep]
        command_parts = [] if sep == -1 else rest[sep + 1:]
        if not head:
            raise RuntimeError("Missing MCP server name.")
        name = head.pop(0)
        protocol = take_option(head, "--protocol")
        url = (take_option(head, "--url") or "").strip()
        env = parse_env_pairs(take_repeat_option(head, "--env"))
        headers = parse_env_pairs(take_repeat_option(head, "--header"))
        if head:
            raise RuntimeError("Unknown arguments: " + " ".join(head))
        has_url = bool(url); has_command = bool(command_parts)
        if has_url and has_command:
            raise RuntimeError("Cannot set both --url and local command. Choose one.")
        if not has_url and not has_command:
            raise RuntimeError("Missing MCP command or --url.")
        if protocol == "streamable-http" and not has_url:
            raise RuntimeError("Protocol streamable-http requires --url.")
        command = command_parts[0] if command_parts else ""
        command_args = command_parts[1:] if len(command_parts) > 1 else []
        existing = await load_scoped_mcp_servers(scope, cwd)
        existing[name] = {"command": command, "args": command_args if has_command else None, "env": env or None, "url": url or None, "headers": headers or None, "protocol": protocol}
        await save_scoped_mcp_servers(scope, existing, cwd)
        print(f"Added MCP server {name} to {get_mcp_config_path(scope, cwd)}")
        return True
    if subcommand == "remove":
        if not rest:
            raise RuntimeError("Missing MCP server name.")
        name = rest[0]
        existing = await load_scoped_mcp_servers(scope, cwd)
        if name not in existing:
            print(f"MCP server {name} not found in {get_mcp_config_path(scope, cwd)}")
            return True
        del existing[name]
        await save_scoped_mcp_servers(scope, existing, cwd)
        print(f"Removed MCP server {name} from {get_mcp_config_path(scope, cwd)}")
        return True
    if subcommand == "login":
        if not rest:
            raise RuntimeError("Missing MCP server name.")
        name = rest[0]
        token = (take_option(rest, "--token") or "").strip()
        if not token:
            raise RuntimeError("Missing --token value.")
        tokens = await read_mcp_tokens_file()
        tokens[name] = token
        await save_mcp_tokens_file(tokens)
        print(f"Stored MCP token for {name} in {MINI_CODE_MCP_TOKENS_PATH}")
        return True
    if subcommand == "logout":
        if not rest:
            raise RuntimeError("Missing MCP server name.")
        name = rest[0]
        tokens = await read_mcp_tokens_file()
        if name not in tokens:
            print(f"No token found for {name} in {MINI_CODE_MCP_TOKENS_PATH}")
            return True
        del tokens[name]
        await save_mcp_tokens_file(tokens)
        print(f"Removed MCP token for {name} from {MINI_CODE_MCP_TOKENS_PATH}")
        return True
    print_usage(); return True


async def handle_skills_command(cwd: str, args: list[str]) -> bool:
    if not args:
        print_usage(); return True
    subcommand, rest_args = args[0], args[1:]
    parsed = parse_scope(rest_args)
    scope = parsed["scope"]; rest = parsed["rest"]
    if subcommand == "list":
        skills = await discover_skills(cwd)
        if not skills:
            print("No skills discovered.")
            return True
        for skill in skills:
            print(f"{skill.get('name')}: {skill.get('description')} ({skill.get('path')})")
        return True
    if subcommand == "add":
        if not rest:
            raise RuntimeError("Missing skill source path.")
        source_path = rest[0]
        name = take_option(rest, "--name")
        result = await install_skill({"cwd": cwd, "sourcePath": source_path, "name": name, "scope": scope})
        print(f"Installed skill {result['name']} at {result['targetPath']}")
        return True
    if subcommand == "remove":
        if not rest:
            raise RuntimeError("Missing skill name.")
        name = rest[0]
        result = await remove_managed_skill({"cwd": cwd, "name": name, "scope": scope})
        if not result["removed"]:
            print(f"Skill {name} not found at {result['targetPath']}")
            return True
        print(f"Removed skill {name} from {result['targetPath']}")
        return True
    print_usage(); return True


async def maybe_handle_management_command(cwd: str, argv: list[str]) -> bool:
    if not argv:
        return False
    category, rest = argv[0], argv[1:]
    if category == "mcp":
        return await handle_mcp_command(cwd, rest)
    if category == "skills":
        return await handle_skills_command(cwd, rest)
    if category in {"help", "--help", "-h"}:
        print_usage(); return True
    return False


maybeHandleManagementCommand = maybe_handle_management_command
