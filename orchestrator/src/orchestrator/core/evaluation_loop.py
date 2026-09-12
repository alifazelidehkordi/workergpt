"""Closed evaluation loop controller.

Keeps task evaluation, repair, and re-evaluation bounded.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class EvaluationLoopAction(str, Enum):
    CONTINUE = "continue"
    REPAIR = "repair"
    STOP = "stop"


@dataclass(frozen=True)
class EvaluationLoopDecision:
    action: EvaluationLoopAction
    reason: str
    round_number: int


class EvaluationLoopController:
    def __init__(self, max_repair_rounds: int = 3):
        self.max_repair_rounds = max_repair_rounds

    def decide(self, evaluation: dict[str, Any], repair_round: int) -> EvaluationLoopDecision:
        if evaluation.get("status") == "passed" and evaluation.get("task_success") is True:
            return EvaluationLoopDecision(EvaluationLoopAction.CONTINUE, "evaluation_passed", repair_round)

        if repair_round >= self.max_repair_rounds:
            return EvaluationLoopDecision(EvaluationLoopAction.STOP, "repair_budget_exhausted", repair_round)

        return EvaluationLoopDecision(EvaluationLoopAction.REPAIR, "evaluation_failed_requires_repair", repair_round)
