"""Central P1 retry policy.

FailureMemory remembers what happened. RetryPolicy decides what the controller
is allowed to do next. Keeping these concerns separate makes retry behaviour
explicit, deterministic, testable, and cheap.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from orchestrator.core.failure_memory import FailureClassification, RetryDecision


class RetryAction(str, Enum):
    RETRY = "retry"
    BACKOFF = "backoff"
    CHANGE_STRATEGY = "change_strategy"
    OPERATOR_ACTION = "operator_action"
    STOP = "stop"


@dataclass(frozen=True)
class RetryPolicyDecision:
    action: RetryAction
    allowed: bool
    reason: str
    category: str | None = None
    occurrence: int = 0
    delay_seconds: int = 0
    strategy_change_required: bool = False
    operator_action_required: bool = False
    terminal: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["action"] = self.action.value
        return value


class RetryPolicy:
    """Map classified failures and recurrence state to one controller action."""

    def __init__(
        self,
        *,
        same_strategy_threshold: int = 2,
        transient_base_delay_seconds: int = 2,
        rate_limit_base_delay_seconds: int = 60,
        max_backoff_seconds: int = 900,
    ) -> None:
        self.same_strategy_threshold = same_strategy_threshold
        self.transient_base_delay_seconds = transient_base_delay_seconds
        self.rate_limit_base_delay_seconds = rate_limit_base_delay_seconds
        self.max_backoff_seconds = max_backoff_seconds

    def before_retry(self, memory: RetryDecision) -> RetryPolicyDecision:
        """Decide whether a previously failed scope may execute again."""
        category = memory.category

        if memory.allowed:
            if memory.reason == "transient_retry_allowed":
                if category == "rate_limit":
                    return RetryPolicyDecision(
                        RetryAction.BACKOFF,
                        True,
                        "rate_limit_backoff_required",
                        category=category,
                        occurrence=memory.occurrence,
                        delay_seconds=self._backoff(category, memory.occurrence),
                    )
                return RetryPolicyDecision(
                    RetryAction.RETRY,
                    True,
                    "transient_retry_allowed",
                    category=category,
                    occurrence=memory.occurrence,
                    delay_seconds=self._backoff(category, memory.occurrence),
                )
            if memory.reason == "strategy_changed":
                return RetryPolicyDecision(
                    RetryAction.RETRY,
                    True,
                    "strategy_changed_retry_allowed",
                    category=category,
                    occurrence=memory.occurrence,
                )
            return RetryPolicyDecision(
                RetryAction.RETRY,
                True,
                memory.reason,
                category=category,
                occurrence=memory.occurrence,
            )

        if memory.reason == "strategy_change_required" or memory.strategy_change_required:
            return RetryPolicyDecision(
                RetryAction.CHANGE_STRATEGY,
                False,
                "strategy_change_required",
                category=category,
                occurrence=memory.occurrence,
                strategy_change_required=True,
            )

        if category == "authentication":
            return RetryPolicyDecision(
                RetryAction.OPERATOR_ACTION,
                False,
                "authentication_requires_operator_action",
                category=category,
                occurrence=memory.occurrence,
                operator_action_required=True,
            )

        return RetryPolicyDecision(
            RetryAction.STOP,
            False,
            memory.reason or "non_retryable_failure",
            category=category,
            occurrence=memory.occurrence,
            terminal=True,
        )

    def after_failure(
        self,
        classification: FailureClassification,
        *,
        same_strategy_count: int,
    ) -> RetryPolicyDecision:
        """Return the next action immediately after a failed execution."""
        category = classification.category

        if category == "authentication":
            return RetryPolicyDecision(
                RetryAction.OPERATOR_ACTION,
                False,
                "authentication_requires_operator_action",
                category=category,
                occurrence=same_strategy_count,
                operator_action_required=True,
            )

        if category == "safety_stop" or not classification.retryable:
            return RetryPolicyDecision(
                RetryAction.STOP,
                False,
                "non_retryable_failure",
                category=category,
                occurrence=same_strategy_count,
                terminal=True,
            )

        if classification.transient:
            if category == "rate_limit":
                return RetryPolicyDecision(
                    RetryAction.BACKOFF,
                    True,
                    "rate_limit_backoff_required",
                    category=category,
                    occurrence=same_strategy_count,
                    delay_seconds=self._backoff(category, same_strategy_count),
                )
            return RetryPolicyDecision(
                RetryAction.RETRY,
                True,
                "transient_retry_allowed",
                category=category,
                occurrence=same_strategy_count,
                delay_seconds=self._backoff(category, same_strategy_count),
            )

        if (
            classification.strategy_change_on_repeat
            and same_strategy_count >= self.same_strategy_threshold
        ):
            return RetryPolicyDecision(
                RetryAction.CHANGE_STRATEGY,
                False,
                "strategy_change_required",
                category=category,
                occurrence=same_strategy_count,
                strategy_change_required=True,
            )

        return RetryPolicyDecision(
            RetryAction.RETRY,
            True,
            "bounded_same_strategy_retry_allowed",
            category=category,
            occurrence=same_strategy_count,
        )

    def _backoff(self, category: str | None, occurrence: int) -> int:
        count = max(int(occurrence), 1)
        base = (
            self.rate_limit_base_delay_seconds
            if category == "rate_limit"
            else self.transient_base_delay_seconds
        )
        return min(base * (2 ** (count - 1)), self.max_backoff_seconds)
