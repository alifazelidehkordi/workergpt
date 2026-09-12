"""Evaluation gate for autonomous workflow continuation.

Separates task quality decisions from executor and retry decisions.
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from typing import Any


class EvaluationAction(str, Enum):
    CONTINUE = "continue"
    REPAIR_REQUIRED = "repair_required"
    RETRY_ALLOWED = "retry_allowed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class EvaluationDecision:
    action: EvaluationAction
    reason: str
    evaluation_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "evaluation_status": self.evaluation_status,
        }


class EvaluationGate:
    """Decides whether a workflow may continue after task evaluation."""

    def evaluate(self, task_evaluation: dict[str, Any] | None) -> EvaluationDecision:
        if not task_evaluation:
            return EvaluationDecision(
                EvaluationAction.BLOCKED,
                "missing_task_evaluation",
                "missing",
            )

        status = str(task_evaluation.get("status", "unassessed"))
        task_success = task_evaluation.get("task_success")

        if status == "passed" and task_success is True:
            return EvaluationDecision(
                EvaluationAction.CONTINUE,
                "task_evaluation_passed",
                status,
            )

        if status == "failed" and task_success is False:
            return EvaluationDecision(
                EvaluationAction.REPAIR_REQUIRED,
                "task_evaluation_failed",
                status,
            )

        return EvaluationDecision(
            EvaluationAction.BLOCKED,
            "task_evaluation_unassessed",
            status,
        )
