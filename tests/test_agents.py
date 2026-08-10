"""Tests for the TinyCoder multi-agent system."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tinycoder.agents.types import SubAgentConfig, SubAgentResult, AgentTask, RunConfig, VALID_APPROVAL_MODES
from tinycoder.agents.manager import (
    _load_agent_from_file,
    list_agents,
    load_agent,
    resolve_toolset,
)




class TestFrontmatterYaml:
    def test_basic_fields(self):
        text = "name: test-agent\ndescription: A test agent\nmodel: gpt-4\nmaxTurns: 5"
        from tinycoder.agents.manager import _parse_frontmatter
        result = _parse_frontmatter(text)
        assert result["name"] == "test-agent"
        assert result["description"] == "A test agent"
        assert result["model"] == "gpt-4"
        assert result["maxTurns"] == 5

    def test_list_field(self):
        text = "name: test\ndescription: desc\ntools:\n  - read_file\n  - write_file\n  - run_command"
        from tinycoder.agents.manager import _parse_frontmatter
        result = _parse_frontmatter(text)
        assert result["tools"] == ["read_file", "write_file", "run_command"]

    def test_nested_run_config(self):
        text = "name: test\ndescription: desc\nrunConfig:\n  max_turns: 5\n  max_time_minutes: 10"
        from tinycoder.agents.manager import _parse_frontmatter
        result = _parse_frontmatter(text)
        assert result["runConfig"]["max_turns"] == 5
        assert result["runConfig"]["max_time_minutes"] == 10

    def test_approval_mode(self):
        text = "name: test\ndescription: desc\napprovalMode: auto-edit"
        from tinycoder.agents.manager import _parse_frontmatter
        result = _parse_frontmatter(text)
        assert result["approvalMode"] == "auto-edit"

    def test_run_config_dataclass(self):
        rc = RunConfig(max_time_minutes=30, max_turns=20)
        assert rc.max_time_minutes == 30
        assert rc.max_turns == 20

    def test_approval_modes(self):
        assert "default" in VALID_APPROVAL_MODES
        assert "auto-edit" in VALID_APPROVAL_MODES
        assert "plan" in VALID_APPROVAL_MODES
        assert "yolo" in VALID_APPROVAL_MODES
        assert "bubble" in VALID_APPROVAL_MODES


class TestSessionAgents:
    def test_session_agent_takes_priority(self):
        import tempfile, os
        from tinycoder.agents.manager import load_session_agents, list_agents

        session_cfg = SubAgentConfig(
            name="session-only",
            description="runtime injected",
            system_prompt="session prompt",
            level="session",
        )
        load_session_agents([session_cfg])
        agents = list_agents()
        names = {a.name for a in agents}
        assert "session-only" in names
        # Clean up
        load_session_agents([])
class TestAgentFileLoading:
    def test_load_from_markdown_file(self):
        content = """---
name: file-agent
description: Loaded from file
tools: [read_file]
model: inherit
maxTurns: 3
---

You are a file reading agent.
Read files and report their contents.
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = f.name

        try:
            config = _load_agent_from_file(Path(tmp_path), "user")
            assert config is not None
            assert config.name == "file-agent"
            assert config.description == "Loaded from file"
            assert config.tools == ["read_file"]
            assert config.model == "inherit"
            assert config.max_turns == 3
            assert config.system_prompt == "You are a file reading agent.\nRead files and report their contents."
        finally:
            os.unlink(tmp_path)

    def test_no_frontmatter_returns_none(self):
        content = "Just some markdown without frontmatter."
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = f.name

        try:
            config = _load_agent_from_file(Path(tmp_path), "user")
            assert config is None
        finally:
            os.unlink(tmp_path)


class TestAgentListing:
    def test_builtin_agents_are_loaded(self):
        agents = list_agents()
        names = {a.name for a in agents}
        assert "test-engineer" in names
        assert "code-reviewer" in names

    def test_load_agent_by_name(self):
        agent = load_agent("test-engineer")
        assert agent is not None
        assert agent.name == "test-engineer"

    def test_load_nonexistent_agent(self):
        agent = load_agent("nonexistent-agent-xyz")
        assert agent is None


class TestToolsetResolution:
    def test_restrict_to_explicit_tools(self):
        config = SubAgentConfig(
            name="test",
            description="desc",
            tools=["read_file", "write_file"],
            system_prompt="",
        )
        available = ["read_file", "write_file", "run_command", "grep_files", "web_fetch"]
        resolved = resolve_toolset(config, available)
        assert resolved == ["read_file", "write_file"]

    def test_disallowed_tools_removed(self):
        config = SubAgentConfig(
            name="test",
            description="desc",
            tools=["read_file", "run_command", "web_fetch"],
            disallowed_tools=["web_fetch"],
            system_prompt="",
        )
        available = ["read_file", "write_file", "run_command", "web_fetch"]
        resolved = resolve_toolset(config, available)
        assert "web_fetch" not in resolved
        assert "read_file" in resolved

    def test_inherit_all_when_no_tools_specified(self):
        config = SubAgentConfig(
            name="test",
            description="desc",
            system_prompt="",
        )
        available = ["read_file", "write_file", "run_command"]
        resolved = resolve_toolset(config, available)
        assert resolved == available

    def test_unknown_tools_filtered_out(self):
        config = SubAgentConfig(
            name="test",
            description="desc",
            tools=["read_file", "nonexistent_tool"],
            system_prompt="",
        )
        available = ["read_file", "write_file"]
        resolved = resolve_toolset(config, available)
        assert resolved == ["read_file"]


class TestTaskToolSchema:
    def test_task_tool_exists(self):
        from tinycoder.agents.task_tool import task_tool
        assert task_tool.name == "task"
        schema = task_tool.input_schema
        assert "agent_name" in schema["properties"]
        assert "description" in schema["properties"]

    def test_task_tool_validate_rejects_missing_fields(self):
        from tinycoder.agents.task_tool import _validate
        import pytest
        with pytest.raises(ValueError):
            _validate({})
        with pytest.raises(ValueError):
            _validate({"agent_name": "test"})
        with pytest.raises(ValueError):
            _validate({"description": "do something"})

    def test_task_tool_validate_accepts_valid(self):
        from tinycoder.agents.task_tool import _validate
        result = _validate({"agent_name": "test", "description": "do something"})
        assert result["agent_name"] == "test"
        assert result["description"] == "do something"
