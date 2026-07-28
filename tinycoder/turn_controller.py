from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal


GuardAction = Literal["continue", "recover", "stop"]


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _optional_positive_int(value: Any, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _optional_positive_float(value: Any, default: float | None) -> float | None:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _budget_value(
    values: dict[str, Any],
    key: str,
    env_name: str,
    default: Any,
) -> Any:
    if key in values:
        return values[key]
    return os.environ.get(env_name, default)


@dataclass(frozen=True)
class TurnBudget:
    max_model_steps: int = 24
    max_tool_calls: int = 40
    max_wall_seconds: int = 600
    tool_error_recovery_threshold: int = 4
    max_consecutive_tool_errors: int = 6
    max_same_action_repeats: int = 2
    max_same_result_repeats: int = 2
    no_progress_recovery_threshold: int = 4
    max_no_progress_steps: int = 6
    max_empty_responses: int = 2
    max_thinking_retries: int = 3
    max_tokens: int | None = 1_000_000
    max_cost_usd: float | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None

    @classmethod
    def from_args(cls, args: dict[str, Any]) -> "TurnBudget":
        values = args.get("turnBudget")
        values = values if isinstance(values, dict) else {}
        defaults = cls()
        int_specs = [
            ("max_model_steps", "maxModelSteps", "TINYCODER_MAX_MODEL_STEPS"),
            ("max_tool_calls", "maxToolCalls", "TINYCODER_MAX_TOOL_CALLS"),
            ("max_wall_seconds", "maxWallSeconds", "TINYCODER_MAX_WALL_SECONDS"),
            ("tool_error_recovery_threshold", "toolErrorRecoveryThreshold", "TINYCODER_TOOL_ERROR_RECOVERY_THRESHOLD"),
            ("max_consecutive_tool_errors", "maxConsecutiveToolErrors", "TINYCODER_MAX_CONSECUTIVE_TOOL_ERRORS"),
            ("max_same_action_repeats", "maxSameActionRepeats", "TINYCODER_MAX_SAME_ACTION_REPEATS"),
            ("max_same_result_repeats", "maxSameResultRepeats", "TINYCODER_MAX_SAME_RESULT_REPEATS"),
            ("no_progress_recovery_threshold", "noProgressRecoveryThreshold", "TINYCODER_NO_PROGRESS_RECOVERY_THRESHOLD"),
            ("max_no_progress_steps", "maxNoProgressSteps", "TINYCODER_MAX_NO_PROGRESS_STEPS"),
            ("max_empty_responses", "maxEmptyResponses", "TINYCODER_MAX_EMPTY_RESPONSES"),
            ("max_thinking_retries", "maxThinkingRetries", "TINYCODER_MAX_THINKING_RETRIES"),
        ]
        optional_int_specs = [
            ("max_tokens", "maxTokens", "TINYCODER_MAX_TURN_TOKENS"),
        ]
        optional_float_specs = [
            ("max_cost_usd", "maxCostUsd", "TINYCODER_MAX_TURN_COST_USD"),
            ("input_cost_per_million", "inputCostPerMillion", "TINYCODER_INPUT_COST_PER_MILLION"),
            ("output_cost_per_million", "outputCostPerMillion", "TINYCODER_OUTPUT_COST_PER_MILLION"),
        ]
        parsed: dict[str, Any] = {}
        for field_name, key, env_name in int_specs:
            default = getattr(defaults, field_name)
            raw = args.get("maxSteps") if field_name == "max_model_steps" and args.get("maxSteps") is not None else _budget_value(values, key, env_name, default)
            parsed[field_name] = _positive_int(raw, default)
        for field_name, key, env_name in optional_int_specs:
            default = getattr(defaults, field_name)
            parsed[field_name] = _optional_positive_int(_budget_value(values, key, env_name, default), default)
        for field_name, key, env_name in optional_float_specs:
            default = getattr(defaults, field_name)
            parsed[field_name] = _optional_positive_float(_budget_value(values, key, env_name, default), default)
        return cls(**parsed)


@dataclass(frozen=True)
class TurnStopReason:
    code: str
    summary: str
    detail: str
    recoverable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "summary": self.summary,
            "detail": self.detail,
            "recoverable": self.recoverable,
        }


@dataclass(frozen=True)
class GuardDecision:
    action: GuardAction = "continue"
    reason: TurnStopReason | None = None


class TurnController:
    def __init__(
        self,
        budget: TurnBudget | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget = budget or TurnBudget()
        self.clock = clock
        self.started_at = clock()
        self.model_steps = 0
        self.tool_calls = 0
        self.total_tokens = 0
        self.total_cost_usd = 0.0
        self.consecutive_tool_errors = 0
        self.no_progress_steps = 0
        self.last_action_fingerprint: str | None = None
        self.same_action_count = 0
        self.last_result_fingerprint: str | None = None
        self.same_result_count = 0
        self.reflected_actions: set[str] = set()
        self.reflected_results: set[str] = set()
        self.error_recovery_issued = False
        self.no_progress_recovery_issued = False

    def _reason(self, code: str, summary: str, detail: str) -> TurnStopReason:
        return TurnStopReason(code=code, summary=summary, detail=detail)

    def _elapsed_seconds(self) -> float:
        return max(0.0, self.clock() - self.started_at)

    def remaining_seconds(self) -> float:
        return max(0.0, self.budget.max_wall_seconds - self._elapsed_seconds())

    def wall_timeout_reason(self) -> TurnStopReason:
        elapsed = self._elapsed_seconds()
        return self._reason(
            "max_wall_seconds",
            "回合总耗时已达到安全上限",
            f"elapsedSeconds={round(elapsed, 3)}, limit={self.budget.max_wall_seconds}",
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "modelSteps": self.model_steps,
            "toolCalls": self.tool_calls,
            "elapsedSeconds": round(self._elapsed_seconds(), 3),
            "totalTokens": self.total_tokens,
            "totalCostUsd": round(self.total_cost_usd, 8),
            "consecutiveToolErrors": self.consecutive_tool_errors,
            "noProgressSteps": self.no_progress_steps,
            "sameActionCount": self.same_action_count,
            "sameResultCount": self.same_result_count,
        }

    def check_limits(self) -> TurnStopReason | None:
        elapsed = self._elapsed_seconds()
        if elapsed >= self.budget.max_wall_seconds:
            return self.wall_timeout_reason()
        if self.budget.max_tokens is not None and self.total_tokens >= self.budget.max_tokens:
            return self._reason(
                "max_tokens",
                "回合累计 Token 已达到预算上限",
                f"totalTokens={self.total_tokens}, limit={self.budget.max_tokens}",
            )
        if (
            self.budget.max_cost_usd is not None
            and self.total_cost_usd >= self.budget.max_cost_usd
        ):
            return self._reason(
                "max_cost_usd",
                "回合累计费用已达到预算上限",
                f"totalCostUsd={self.total_cost_usd:.8f}, limit={self.budget.max_cost_usd:.8f}",
            )
        return None

    def before_model_step(self) -> TurnStopReason | None:
        stopped = self.check_limits()
        if stopped:
            return stopped
        if self.model_steps >= self.budget.max_model_steps:
            return self._reason(
                "max_model_steps",
                "模型调用步数已达到安全上限",
                f"modelSteps={self.model_steps}, limit={self.budget.max_model_steps}",
            )
        self.model_steps += 1
        return None

    def before_tool_call(self, tool_name: str, input_value: Any) -> GuardDecision:
        stopped = self.check_limits()
        if stopped:
            return GuardDecision("stop", stopped)
        if self.tool_calls >= self.budget.max_tool_calls:
            return GuardDecision(
                "stop",
                self._reason(
                    "max_tool_calls",
                    "工具调用次数已达到安全上限",
                    f"toolCalls={self.tool_calls}, limit={self.budget.max_tool_calls}",
                ),
            )
        self.tool_calls += 1
        fingerprint = action_fingerprint(tool_name, input_value)
        if fingerprint == self.last_action_fingerprint:
            self.same_action_count += 1
        else:
            self.last_action_fingerprint = fingerprint
            self.same_action_count = 1
        if self.same_action_count <= self.budget.max_same_action_repeats:
            return GuardDecision()
        reason = self._reason(
            "repeated_tool_call",
            "检测到连续重复的工具调用",
            (
                f"tool={tool_name}, repeats={self.same_action_count}, "
                f"allowed={self.budget.max_same_action_repeats}"
            ),
        )
        if fingerprint in self.reflected_actions:
            return GuardDecision("stop", reason)
        self.reflected_actions.add(fingerprint)
        self.no_progress_steps += 1
        return GuardDecision("recover", reason)

    def _no_progress_decision(self) -> GuardDecision:
        if self.no_progress_steps >= self.budget.max_no_progress_steps:
            return GuardDecision(
                "stop",
                self._reason(
                    "no_progress",
                    "连续步骤没有产生可观察的新进展",
                    (
                        f"noProgressSteps={self.no_progress_steps}, "
                        f"limit={self.budget.max_no_progress_steps}"
                    ),
                ),
            )
        if (
            self.no_progress_steps >= self.budget.no_progress_recovery_threshold
            and not self.no_progress_recovery_issued
        ):
            self.no_progress_recovery_issued = True
            return GuardDecision(
                "recover",
                self._reason(
                    "no_progress",
                    "当前方案连续多步没有产生新进展",
                    (
                        f"noProgressSteps={self.no_progress_steps}, "
                        f"recoveryThreshold={self.budget.no_progress_recovery_threshold}"
                    ),
                ),
            )
        return GuardDecision()

    def record_progress(self) -> GuardDecision:
        self.no_progress_steps += 1
        return self._no_progress_decision()

    def record_tool_result(
        self,
        tool_name: str,
        ok: bool,
        output: str,
        *,
        file_changed: bool | None = None,
    ) -> GuardDecision:
        fingerprint = result_fingerprint(tool_name, ok, output)
        if fingerprint == self.last_result_fingerprint:
            self.same_result_count += 1
        else:
            self.last_result_fingerprint = fingerprint
            self.same_result_count = 1

        if not ok:
            self.consecutive_tool_errors += 1
            self.no_progress_steps += 1
            if self.consecutive_tool_errors >= self.budget.max_consecutive_tool_errors:
                return GuardDecision(
                    "stop",
                    self._reason(
                        "consecutive_tool_errors",
                        "连续工具错误已达到安全上限",
                        (
                            f"consecutiveToolErrors={self.consecutive_tool_errors}, "
                            f"limit={self.budget.max_consecutive_tool_errors}"
                        ),
                    ),
                )
            if (
                self.consecutive_tool_errors
                >= self.budget.tool_error_recovery_threshold
                and not self.error_recovery_issued
            ):
                self.error_recovery_issued = True
                return GuardDecision(
                    "recover",
                    self._reason(
                        "consecutive_tool_errors",
                        "连续工具错误过多，需要更换方案",
                        (
                            f"consecutiveToolErrors={self.consecutive_tool_errors}, "
                            f"recoveryThreshold={self.budget.tool_error_recovery_threshold}"
                        ),
                    ),
                )
            return self._no_progress_decision()

        self.consecutive_tool_errors = 0
        self.error_recovery_issued = False
        if self.same_result_count > self.budget.max_same_result_repeats:
            self.no_progress_steps += 1
            reason = self._reason(
                "repeated_tool_result",
                "工具连续返回相同结果",
                (
                    f"tool={tool_name}, repeats={self.same_result_count}, "
                    f"allowed={self.budget.max_same_result_repeats}"
                ),
            )
            if fingerprint in self.reflected_results:
                return GuardDecision("stop", reason)
            self.reflected_results.add(fingerprint)
            return GuardDecision("recover", reason)

        if self.same_result_count > 1 or file_changed is False:
            self.no_progress_steps += 1
            return self._no_progress_decision()

        self.no_progress_steps = 0
        self.no_progress_recovery_issued = False
        return GuardDecision()

    def record_model_usage(self, usage: dict[str, Any] | None) -> TurnStopReason | None:
        if not isinstance(usage, dict):
            return self.check_limits()
        input_tokens = _non_negative_int(usage.get("inputTokens"))
        output_tokens = _non_negative_int(usage.get("outputTokens"))
        total_tokens = _non_negative_int(usage.get("totalTokens"))
        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens
        self.total_tokens += total_tokens

        has_explicit_cost = usage.get("costUsd") is not None
        explicit_cost = _non_negative_float(usage.get("costUsd"))
        can_price_input = input_tokens == 0 or self.budget.input_cost_per_million is not None
        can_price_output = output_tokens == 0 or self.budget.output_cost_per_million is not None
        if (
            self.budget.max_cost_usd is not None
            and not has_explicit_cost
            and not (can_price_input and can_price_output)
        ):
            return TurnStopReason(
                code="cost_accounting_unavailable",
                summary="已配置费用上限，但当前模型用量无法换算为费用",
                detail=(
                    "Provider usage did not include costUsd and the configured "
                    "input/output per-million token prices are incomplete"
                ),
                recoverable=False,
            )
        if has_explicit_cost:
            self.total_cost_usd += explicit_cost
        else:
            if self.budget.input_cost_per_million is not None:
                self.total_cost_usd += (
                    input_tokens * self.budget.input_cost_per_million / 1_000_000
                )
            if self.budget.output_cost_per_million is not None:
                self.total_cost_usd += (
                    output_tokens * self.budget.output_cost_per_million / 1_000_000
                )
        return self.check_limits()


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _non_negative_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def action_fingerprint(tool_name: str, input_value: Any) -> str:
    normalized = json.dumps(
        input_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(f"{tool_name}\0{normalized}".encode("utf-8")).hexdigest()


def result_fingerprint(tool_name: str, ok: bool, output: str) -> str:
    digest = hashlib.sha256(str(output).encode("utf-8", "replace")).hexdigest()
    return f"{tool_name}:{int(ok)}:{digest}"


def format_recovery_prompt(reason: TurnStopReason) -> str:
    return (
        "[Agent loop guard recovery]\n"
        f"Reason code: {reason.code}\n"
        f"Observed: {reason.detail}\n"
        "The current approach is not making safe progress. Re-evaluate the evidence, "
        "choose a materially different tool or argument set, and do not repeat the "
        "blocked action. If no safe alternative exists, return an explicit <final> "
        "answer explaining the blocker."
    )


def format_stop_message(
    reason: TurnStopReason,
    snapshot: dict[str, Any],
) -> str:
    return "\n".join(
        [
            "当前回合已由 Agent Loop 安全控制器停止。",
            "",
            f"- code: {reason.code}",
            f"- 原因: {reason.summary}",
            f"- 详情: {reason.detail}",
            (
                "- 统计: "
                f"modelSteps={snapshot.get('modelSteps', 0)}, "
                f"toolCalls={snapshot.get('toolCalls', 0)}, "
                f"elapsedSeconds={snapshot.get('elapsedSeconds', 0)}, "
                f"totalTokens={snapshot.get('totalTokens', 0)}, "
                f"totalCostUsd={snapshot.get('totalCostUsd', 0)}, "
                f"consecutiveToolErrors={snapshot.get('consecutiveToolErrors', 0)}, "
                f"noProgressSteps={snapshot.get('noProgressSteps', 0)}"
            ),
            "",
            "可以在确认目标、修正工具参数或提高对应预算后继续。",
        ]
    )
