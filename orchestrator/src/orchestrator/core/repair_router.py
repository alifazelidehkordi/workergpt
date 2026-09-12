"""Routes failed task evaluations to targeted repair actions.

P2 keeps repair decisions separate from retry decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RepairAction(str, Enum):
    REPAIR = "repair"
    RETRY = "retry"
    ESCALATE = "escalate"
    STOP = "stop"


@dataclass(frozen=True)
class RepairDecision:
    action: RepairAction
    reason: str
    target_agent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "target_agent": self.target_agent,
        }


class EvaluationRepairRouter:
    """Maps evaluation failures to the smallest useful repair step."""

    def route(self, evaluation: dict[str, Any]) -> RepairDecision:
        if not evaluation:
            return RepairDecision(RepairAction.STOP, "missing_evaluation")

        issues = evaluation.get("issues") or []
        issue_text = " ".join(str(item) for item in issues).lower()

        if "source" in issue_text or "citation" in issue_text:
            return RepairDecision(
                RepairAction.REPAIR,
                "source_quality_issue",
                "research_section_repair",
            )

        if "format" in issue_text or "json" in issue_text:
            return RepairDecision(
                RepairAction.REPAIR,
                "format_validation_issue",
                "repair_agent",
            )

        return RepairDecision(
            RepairAction.REPAIR,
            "generic_task_quality_issue",
            "repair_agent",
        )
