"""Agent routing decisions for the P3 decision layer."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RouteAction(str, Enum):
    CONTINUE = "continue"
    REPAIR = "repair"
    RETRY = "retry"
    STOP = "stop"


@dataclass(frozen=True)
class AgentRoute:
    action: RouteAction
    agent: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "agent": self.agent,
            "reason": self.reason,
        }


class AgentRouter:
    """Translate decisions into concrete agent targets."""

    def route(self, decision: str, metadata: dict[str, Any] | None = None) -> AgentRoute:
        metadata = metadata or {}
        if decision in {"repair", "repair_required"}:
            return AgentRoute(RouteAction.REPAIR, "research_section_repair", "evaluation_failed")
        if decision in {"retry", "retry_allowed"}:
            return AgentRoute(RouteAction.RETRY, None, "retry_policy_allowed")
        if decision in {"stop", "blocked"}:
            return AgentRoute(RouteAction.STOP, None, "decision_blocked")
        return AgentRoute(RouteAction.CONTINUE, None, "decision_continue")
