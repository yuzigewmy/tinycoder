from __future__ import annotations

import unittest

from tinycoder.turn_controller import TurnBudget, TurnController


class MutableClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class TurnControllerTests(unittest.TestCase):
    def test_default_budget_places_hard_limits_on_a_turn(self) -> None:
        budget = TurnBudget()

        self.assertEqual(budget.max_model_steps, 24)
        self.assertEqual(budget.max_tool_calls, 40)
        self.assertEqual(budget.max_wall_seconds, 600)
        self.assertEqual(budget.max_consecutive_tool_errors, 6)
        self.assertEqual(budget.max_same_action_repeats, 2)
        self.assertEqual(budget.max_same_result_repeats, 2)
        self.assertEqual(budget.max_no_progress_steps, 6)
        self.assertEqual(budget.max_tokens, 1_000_000)

    def test_model_step_limit_stops_before_an_extra_request(self) -> None:
        controller = TurnController(TurnBudget(max_model_steps=2))

        self.assertIsNone(controller.before_model_step())
        self.assertIsNone(controller.before_model_step())
        stopped = controller.before_model_step()

        self.assertIsNotNone(stopped)
        self.assertEqual(stopped.code, "max_model_steps")
        self.assertEqual(controller.snapshot()["modelSteps"], 2)

    def test_wall_clock_limit_is_checked_before_more_work(self) -> None:
        clock = MutableClock()
        controller = TurnController(TurnBudget(max_wall_seconds=10), clock=clock)
        clock.value += 11

        stopped = controller.check_limits()

        self.assertIsNotNone(stopped)
        self.assertEqual(stopped.code, "max_wall_seconds")

    def test_remaining_wall_time_never_becomes_negative(self) -> None:
        clock = MutableClock()
        controller = TurnController(TurnBudget(max_wall_seconds=10), clock=clock)

        clock.value += 4
        remaining = controller.remaining_seconds()
        clock.value += 20

        self.assertEqual(remaining, 6)
        self.assertEqual(controller.remaining_seconds(), 0)

    def test_third_identical_action_requests_reflection_then_stops_if_repeated(self) -> None:
        controller = TurnController(TurnBudget(max_same_action_repeats=2))

        self.assertEqual(controller.before_tool_call("read_file", {"path": "a.py"}).action, "continue")
        self.assertEqual(controller.before_tool_call("read_file", {"path": "a.py"}).action, "continue")
        recovery = controller.before_tool_call("read_file", {"path": "a.py"})
        stopped = controller.before_tool_call("read_file", {"path": "a.py"})

        self.assertEqual(recovery.action, "recover")
        self.assertEqual(recovery.reason.code, "repeated_tool_call")
        self.assertEqual(stopped.action, "stop")
        self.assertEqual(stopped.reason.code, "repeated_tool_call")

    def test_repeated_result_requests_reflection_then_stops(self) -> None:
        controller = TurnController(TurnBudget(max_same_result_repeats=2))

        first = controller.record_tool_result("read_file", True, "same output")
        second = controller.record_tool_result("read_file", True, "same output")
        recovery = controller.record_tool_result("read_file", True, "same output")
        stopped = controller.record_tool_result("read_file", True, "same output")

        self.assertEqual(first.action, "continue")
        self.assertEqual(second.action, "continue")
        self.assertEqual(recovery.action, "recover")
        self.assertEqual(recovery.reason.code, "repeated_tool_result")
        self.assertEqual(stopped.action, "stop")

    def test_consecutive_tool_errors_recover_then_stop(self) -> None:
        controller = TurnController(
            TurnBudget(
                tool_error_recovery_threshold=2,
                max_consecutive_tool_errors=3,
            )
        )

        first = controller.record_tool_result("read_file", False, "error one")
        recovery = controller.record_tool_result("read_file", False, "error two")
        stopped = controller.record_tool_result("read_file", False, "error three")

        self.assertEqual(first.action, "continue")
        self.assertEqual(recovery.action, "recover")
        self.assertEqual(recovery.reason.code, "consecutive_tool_errors")
        self.assertEqual(stopped.action, "stop")
        self.assertEqual(stopped.reason.code, "consecutive_tool_errors")

    def test_successful_novel_result_resets_error_and_no_progress_streaks(self) -> None:
        controller = TurnController(TurnBudget())
        controller.record_tool_result("read_file", False, "error")
        controller.record_progress()

        controller.record_tool_result("read_file", True, "new evidence")

        snapshot = controller.snapshot()
        self.assertEqual(snapshot["consecutiveToolErrors"], 0)
        self.assertEqual(snapshot["noProgressSteps"], 0)

    def test_unchanged_file_mutation_counts_as_no_progress(self) -> None:
        controller = TurnController(TurnBudget(max_no_progress_steps=2))

        first = controller.record_tool_result(
            "edit_file",
            True,
            "edit completed",
            file_changed=False,
        )
        stopped = controller.record_tool_result(
            "edit_file",
            True,
            "edit completed again",
            file_changed=False,
        )

        self.assertEqual(first.action, "continue")
        self.assertEqual(stopped.action, "stop")
        self.assertEqual(stopped.reason.code, "no_progress")

    def test_token_budget_accumulates_provider_usage(self) -> None:
        controller = TurnController(TurnBudget(max_tokens=100))

        self.assertIsNone(
            controller.record_model_usage(
                {"inputTokens": 60, "outputTokens": 10, "totalTokens": 70}
            )
        )
        stopped = controller.record_model_usage(
            {"inputTokens": 20, "outputTokens": 20, "totalTokens": 40}
        )

        self.assertIsNotNone(stopped)
        self.assertEqual(stopped.code, "max_tokens")
        self.assertEqual(controller.snapshot()["totalTokens"], 110)

    def test_cost_budget_supports_explicit_or_configured_usage_cost(self) -> None:
        direct = TurnController(TurnBudget(max_cost_usd=0.50))
        direct_stop = direct.record_model_usage({"totalTokens": 10, "costUsd": 0.60})

        calculated = TurnController(
            TurnBudget(
                max_cost_usd=0.50,
                input_cost_per_million=2.0,
                output_cost_per_million=4.0,
            )
        )
        calculated_stop = calculated.record_model_usage(
            {"inputTokens": 200_000, "outputTokens": 50_000}
        )

        self.assertEqual(direct_stop.code, "max_cost_usd")
        self.assertEqual(calculated_stop.code, "max_cost_usd")

    def test_cost_budget_fails_closed_when_usage_cannot_be_priced(self) -> None:
        controller = TurnController(TurnBudget(max_cost_usd=1.0))

        stopped = controller.record_model_usage(
            {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120}
        )

        self.assertIsNotNone(stopped)
        self.assertEqual(stopped.code, "cost_accounting_unavailable")

    def test_budget_can_be_overridden_from_agent_arguments(self) -> None:
        budget = TurnBudget.from_args(
            {
                "maxSteps": 3,
                "turnBudget": {
                    "maxToolCalls": 5,
                    "maxWallSeconds": 12,
                    "maxTokens": 900,
                },
            }
        )

        self.assertEqual(budget.max_model_steps, 3)
        self.assertEqual(budget.max_tool_calls, 5)
        self.assertEqual(budget.max_wall_seconds, 12)
        self.assertEqual(budget.max_tokens, 900)


if __name__ == "__main__":
    unittest.main()
