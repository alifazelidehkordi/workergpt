"""Runtime bridge between decision making and workflow execution.

This layer keeps routing decisions separate from execution. It is intentionally
small so P0/P1/P2 controls remain authoritative.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class RuntimeDecision:
    action: str
    agent: str | None
    reason: str


class DecisionRuntime:
    """Translate evaluation/failure state into executable workflow choices."""

    def __init__(self, decision_engine: Any, agent_router: Any):
        self.decision_engine = decision_engine
        self.agent_router = agent_router

    def decide(self, state: dict[str, Any]) -> RuntimeDecision:
        decision = self.decision_engine.decide(state)
        route = self.agent_router.route(decision)
        return RuntimeDecision(
            action=getattr(decision, "action", "stop"),
            agent=route,
            reason=getattr(decision, "reason", "decision_engine"),
        )
