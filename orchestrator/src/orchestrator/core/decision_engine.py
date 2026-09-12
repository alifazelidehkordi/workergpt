"""Strategy decision layer for autonomous workflows.

Keeps planning decisions separate from execution and evaluation.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class DecisionAction(str, Enum):
    CONTINUE = "continue"
    REPAIR = "repair"
    RETRY = "retry"
    CHANGE_STRATEGY = "change_strategy"
    STOP = "stop"


@dataclass(frozen=True)
class Decision:
    action: DecisionAction
    reason: str
    agent: str | None = None


class DecisionEngine:
    def decide(self, *, evaluation: dict[str, Any] | None = None, failure: dict[str, Any] | None = None) -> Decision:
        if failure:
            if failure.get("strategy_change_required"):
                return Decision(DecisionAction.CHANGE_STRATEGY, "repeated_failure_requires_new_strategy")
            return Decision(DecisionAction.RETRY, "retryable_failure")

        status = (evaluation or {}).get("status")
        if status == "passed":
            return Decision(DecisionAction.CONTINUE, "evaluation_passed")
        if status == "failed":
            return Decision(DecisionAction.REPAIR, "evaluation_failed", "repair")
        return Decision(DecisionAction.STOP, "missing_evaluation")
