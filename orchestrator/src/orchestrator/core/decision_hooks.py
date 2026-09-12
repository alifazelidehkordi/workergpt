"""Decision hooks for workflow integration.

Keeps DecisionEngine routing decisions explicit without mixing them with
execution, retry, or evaluation ownership.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class DecisionHookResult:
    action: str
    route: str | None
    reason: str
    should_continue: bool


def apply_decision_hook(runtime_decision: Any) -> DecisionHookResult:
    """Convert a runtime decision into a workflow-safe continuation signal."""
    action = getattr(runtime_decision, "action", "stop")
    route = getattr(runtime_decision, "agent", None)
    reason = getattr(runtime_decision, "reason", "decision_runtime")

    return DecisionHookResult(
        action=action,
        route=route,
        reason=reason,
        should_continue=action == "continue",
    )
