"""Integration helper for P3 runtime decisions.

Keeps orchestration code independent from routing details.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class DecisionExecution:
    action: str
    selected_agent: str | None
    reason: str
    metadata: dict[str, Any]


def apply_runtime_decision(runtime_decision: Any) -> DecisionExecution:
    """Convert a runtime decision into workflow metadata.

    The orchestrator can attach this result to AgentOutput/state without
    embedding routing rules.
    """
    return DecisionExecution(
        action=getattr(runtime_decision, "action", "stop"),
        selected_agent=getattr(runtime_decision, "agent", None),
        reason=getattr(runtime_decision, "reason", "unknown"),
        metadata={
            "decision_action": getattr(runtime_decision, "action", "stop"),
            "decision_agent": getattr(runtime_decision, "agent", None),
            "decision_reason": getattr(runtime_decision, "reason", "unknown"),
        },
    )
