from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tinycoder.agent_loop import run_agent_turn
from tinycoder.tool import ToolDefinition, ToolRegistry


class RepeatingProgressModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        return {"type": "assistant", "content": "still working", "kind": "progress"}


class RepeatingToolModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        return {
            "type": "tool_calls",
            "calls": [
                {
                    "id": f"call-{self.calls}",
                    "toolName": "read_file",
                    "input": {"path": "same.py"},
                }
            ],
            "usage": {"inputTokens": 2, "outputTokens": 1, "totalTokens": 3},
        }


class DifferentFailingToolModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        return {
            "type": "tool_calls",
            "calls": [
                {
                    "id": f"call-{self.calls}",
                    "toolName": "read_file",
                    "input": {"path": f"file-{self.calls}.py"},
                }
            ],
        }


class BatchedToolModel:
    def __init__(self, calls: list[dict[str, Any]], usage: dict[str, Any] | None = None) -> None:
        self.calls = calls
        self.usage = usage
        self.model_calls = 0

    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.model_calls += 1
        return {
            "type": "tool_calls",
            "calls": [
                {**call, "id": f"batch-{self.model_calls}-{index}"}
                for index, call in enumerate(self.calls)
            ],
            "usage": self.usage,
        }


class VaryingToolModel:
    def __init__(
        self,
        *,
        tool_name: str = "read_file",
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.usage = usage
        self.calls = 0

    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        return {
            "type": "tool_calls",
            "calls": [
                {
                    "id": f"vary-{self.calls}",
                    "toolName": self.tool_name,
                    "input": {
                        "path": "same.py",
                        "search": f"search-{self.calls}",
                        "replace": f"replace-{self.calls}",
                    },
                }
            ],
            "usage": self.usage,
        }


class ToolThenCancellationModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {
                "type": "tool_calls",
                "calls": [
                    {
                        "id": "checkpoint-call",
                        "toolName": "read_file",
                        "input": {"path": "checkpoint.py"},
                    }
                ],
            }
        raise asyncio.CancelledError()


class SlowModel:
    async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        await asyncio.sleep(5)
        return {"type": "assistant", "content": "too late"}


def make_tool_registry(*, ok: bool = True, output: str = "result") -> tuple[ToolRegistry, list[dict[str, Any]]]:
    executions: list[dict[str, Any]] = []

    async def run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        executions.append(input_value)
        return {"ok": ok, "output": output}

    tool = ToolDefinition(
        name="read_file",
        description="test reader",
        input_schema={"type": "object"},
        run=run,
    )
    return ToolRegistry([tool]), executions


def make_counting_tool(
    name: str,
    *,
    ok: bool = True,
    output_prefix: str = "result",
    await_user: bool = False,
) -> tuple[ToolDefinition, list[dict[str, Any]]]:
    executions: list[dict[str, Any]] = []

    async def run(input_value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        executions.append(input_value)
        return {
            "ok": ok,
            "output": f"{output_prefix}-{len(executions)}" if output_prefix else "",
            "awaitUser": await_user,
        }

    return (
        ToolDefinition(
            name=name,
            description=f"test {name}",
            input_schema={"type": "object"},
            run=run,
        ),
        executions,
    )


class AgentLoopGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_loop_stops_at_model_step_budget(self) -> None:
        model = RepeatingProgressModel()
        tools, _ = make_tool_registry()
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "work"},
        ]

        result = await run_agent_turn(
            {
                "model": model,
                "tools": tools,
                "messages": messages,
                "cwd": ".",
                "turnBudget": {
                    "maxModelSteps": 3,
                    "maxNoProgressSteps": 99,
                },
            }
        )

        self.assertEqual(model.calls, 3)
        self.assertEqual(result[-1]["role"], "assistant")
        self.assertEqual(result[-1]["stopReason"]["code"], "max_model_steps")
        self.assertIn("modelSteps=3", result[-1]["content"])

    async def test_repeated_tool_call_reflects_once_then_stops_without_reexecution(self) -> None:
        model = RepeatingToolModel()
        tools, executions = make_tool_registry()
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "read"},
        ]

        result = await run_agent_turn(
            {
                "model": model,
                "tools": tools,
                "messages": messages,
                "cwd": ".",
                "turnBudget": {
                    "maxModelSteps": 10,
                    "maxSameActionRepeats": 2,
                    "maxNoProgressSteps": 99,
                },
            }
        )

        self.assertEqual(model.calls, 4)
        self.assertEqual(len(executions), 2)
        self.assertEqual(result[-1]["stopReason"]["code"], "repeated_tool_call")
        self.assertTrue(
            any(
                message.get("role") == "user"
                and "loop guard" in str(message.get("content") or "").lower()
                for message in result
            )
        )

    async def test_consecutive_tool_errors_recover_then_stop(self) -> None:
        model = DifferentFailingToolModel()
        tools, executions = make_tool_registry(ok=False, output="missing")
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "read several files"},
        ]

        result = await run_agent_turn(
            {
                "model": model,
                "tools": tools,
                "messages": messages,
                "cwd": ".",
                "turnBudget": {
                    "maxModelSteps": 10,
                    "toolErrorRecoveryThreshold": 2,
                    "maxConsecutiveToolErrors": 3,
                    "maxNoProgressSteps": 99,
                },
            }
        )

        self.assertEqual(len(executions), 3)
        self.assertEqual(result[-1]["stopReason"]["code"], "consecutive_tool_errors")

    async def test_completed_tool_batch_is_checkpointed_before_cancellation(self) -> None:
        model = ToolThenCancellationModel()
        tools, executions = make_tool_registry()
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "read then continue"},
        ]

        with self.assertRaises(asyncio.CancelledError):
            await run_agent_turn(
                {
                    "model": model,
                    "tools": tools,
                    "messages": messages,
                    "cwd": ".",
                    "turnBudget": {"maxModelSteps": 5},
                }
            )

        self.assertEqual(len(executions), 1)
        self.assertIn("assistant_tool_call", [message.get("role") for message in messages])
        self.assertIn("tool_result", [message.get("role") for message in messages])

    async def test_tool_call_budget_blocks_the_extra_call_in_a_batch(self) -> None:
        model = BatchedToolModel(
            [
                {"toolName": "read_file", "input": {"path": "one.py"}},
                {"toolName": "read_file", "input": {"path": "two.py"}},
                {"toolName": "read_file", "input": {"path": "three.py"}},
            ]
        )
        tools, executions = make_tool_registry()

        result = await run_agent_turn(
            {
                "model": model,
                "tools": tools,
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "read"},
                ],
                "cwd": ".",
                "turnBudget": {
                    "maxModelSteps": 5,
                    "maxToolCalls": 2,
                    "maxNoProgressSteps": 99,
                },
            }
        )

        self.assertEqual(len(executions), 2)
        self.assertEqual(result[-1]["stopReason"]["code"], "max_tool_calls")

    async def test_repeated_success_result_reflects_then_stops(self) -> None:
        model = VaryingToolModel()
        tools, executions = make_tool_registry(output="same result")

        result = await run_agent_turn(
            {
                "model": model,
                "tools": tools,
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "read"},
                ],
                "cwd": ".",
                "turnBudget": {
                    "maxModelSteps": 10,
                    "maxSameActionRepeats": 99,
                    "maxSameResultRepeats": 2,
                    "maxNoProgressSteps": 99,
                },
            }
        )

        self.assertEqual(len(executions), 4)
        self.assertEqual(result[-1]["stopReason"]["code"], "repeated_tool_result")

    async def test_progress_without_evidence_stops_as_no_progress(self) -> None:
        model = RepeatingProgressModel()
        tools, _ = make_tool_registry()

        result = await run_agent_turn(
            {
                "model": model,
                "tools": tools,
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "work"},
                ],
                "cwd": ".",
                "turnBudget": {
                    "maxModelSteps": 10,
                    "noProgressRecoveryThreshold": 2,
                    "maxNoProgressSteps": 3,
                },
            }
        )

        self.assertEqual(model.calls, 3)
        self.assertEqual(result[-1]["stopReason"]["code"], "no_progress")

    async def test_token_budget_stops_before_executing_the_over_budget_action(self) -> None:
        model = VaryingToolModel(
            usage={"inputTokens": 2, "outputTokens": 1, "totalTokens": 3}
        )
        tools, executions = make_tool_registry()

        result = await run_agent_turn(
            {
                "model": model,
                "tools": tools,
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "read"},
                ],
                "cwd": ".",
                "turnBudget": {
                    "maxModelSteps": 10,
                    "maxTokens": 6,
                    "maxSameActionRepeats": 99,
                    "maxSameResultRepeats": 99,
                    "maxNoProgressSteps": 99,
                },
            }
        )

        self.assertEqual(model.calls, 2)
        self.assertEqual(len(executions), 1)
        self.assertEqual(result[-1]["stopReason"]["code"], "max_tokens")

    async def test_cost_budget_stops_a_progress_loop(self) -> None:
        class CostlyProgressModel:
            async def next(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
                return {
                    "type": "assistant",
                    "content": "working",
                    "kind": "progress",
                    "usage": {"totalTokens": 10, "costUsd": 0.6},
                }

        tools, _ = make_tool_registry()
        result = await run_agent_turn(
            {
                "model": CostlyProgressModel(),
                "tools": tools,
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "work"},
                ],
                "cwd": ".",
                "turnBudget": {"maxCostUsd": 0.5},
            }
        )

        self.assertEqual(result[-1]["stopReason"]["code"], "max_cost_usd")

    async def test_successful_edit_without_file_change_counts_as_no_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "same.py").write_text("unchanged", encoding="utf-8")
            model = VaryingToolModel(tool_name="edit_file")
            edit_tool, executions = make_counting_tool("edit_file")
            result = await run_agent_turn(
                {
                    "model": model,
                    "tools": ToolRegistry([edit_tool]),
                    "messages": [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "edit"},
                    ],
                    "cwd": directory,
                    "turnBudget": {
                        "maxModelSteps": 5,
                        "maxSameActionRepeats": 99,
                        "maxSameResultRepeats": 99,
                        "noProgressRecoveryThreshold": 99,
                        "maxNoProgressSteps": 2,
                    },
                }
            )

        self.assertEqual(len(executions), 2)
        self.assertEqual(result[-1]["stopReason"]["code"], "no_progress")

    async def test_ask_user_stops_the_tool_batch_immediately(self) -> None:
        ask_tool, ask_executions = make_counting_tool(
            "ask_user",
            output_prefix="question",
            await_user=True,
        )
        side_effect_tool, side_effect_executions = make_counting_tool("write_file")
        model = BatchedToolModel(
            [
                {"toolName": "ask_user", "input": {"question": "Continue?"}},
                {"toolName": "write_file", "input": {"path": "should-not-run.txt"}},
            ]
        )

        result = await run_agent_turn(
            {
                "model": model,
                "tools": ToolRegistry([ask_tool, side_effect_tool]),
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "ask first"},
                ],
                "cwd": ".",
            }
        )

        self.assertEqual(len(ask_executions), 1)
        self.assertEqual(side_effect_executions, [])
        self.assertEqual(result[-1]["role"], "assistant")
        self.assertIn("question", result[-1]["content"])

    async def test_wall_budget_times_out_an_async_model_request(self) -> None:
        tools, _ = make_tool_registry()
        started = asyncio.get_running_loop().time()

        result = await run_agent_turn(
            {
                "model": SlowModel(),
                "tools": tools,
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "wait"},
                ],
                "cwd": ".",
                "turnBudget": {"maxWallSeconds": 1},
            }
        )

        elapsed = asyncio.get_running_loop().time() - started
        self.assertLess(elapsed, 2)
        self.assertEqual(result[-1]["stopReason"]["code"], "max_wall_seconds")

    async def test_tool_cancellation_records_an_uncertain_checkpoint(self) -> None:
        model = BatchedToolModel(
            [
                {"toolName": "run_command", "input": {"command": "completed-first"}},
                {"toolName": "run_command", "input": {"command": "interrupted-second"}},
            ]
        )
        executions = 0

        async def cancel_tool(
            input_value: dict[str, Any],
            context: dict[str, Any],
        ) -> dict[str, Any]:
            nonlocal executions
            executions += 1
            if executions == 1:
                return {"ok": True, "output": "first completed"}
            raise asyncio.CancelledError()

        tools = ToolRegistry(
            [
                ToolDefinition(
                    name="run_command",
                    description="cancelled tool",
                    input_schema={"type": "object"},
                    run=cancel_tool,
                )
            ]
        )
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "run"},
        ]

        with self.assertRaises(asyncio.CancelledError):
            await run_agent_turn(
                {
                    "model": model,
                    "tools": tools,
                    "messages": messages,
                    "cwd": ".",
                }
            )

        interrupted = [
            message
            for message in messages
            if message.get("role") == "tool_result"
            and message.get("executionUncertain")
        ]
        self.assertEqual(len(interrupted), 1)
        self.assertIn("interrupted", interrupted[0]["content"].lower())
        tool_results = [
            message for message in messages if message.get("role") == "tool_result"
        ]
        self.assertEqual(len(tool_results), 2)
        self.assertEqual(tool_results[0]["content"], "first completed")


if __name__ == "__main__":
    unittest.main()
