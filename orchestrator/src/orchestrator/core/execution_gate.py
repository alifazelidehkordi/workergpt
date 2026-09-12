"""
P0 execution gate.

Small integration boundary between the orchestrator and safety policies.
The orchestrator can call this before executor.execute() without coupling
itself to individual guards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.core.p0_safety import P0SafetyPolicy


class ExecutionGate:
    def __init__(self, cache_file: str | Path):
        self.policy = P0SafetyPolicy(cache_file)

    def allow(
        self,
        agent_name: str,
        context: dict[str, Any],
        files: list[Path] | None = None,
    ) -> tuple[bool, str]:
        decision = self.policy.check(
            agent_name=agent_name,
            context=context,
            files=files or [],
        )
        return decision.allowed, decision.reason

    def record(
        self,
        agent_name: str,
        context: dict[str, Any],
        files: list[Path] | None = None,
        result: str = "executed",
    ) -> None:
        self.policy.record(
            agent_name=agent_name,
            context=context,
            files=files or [],
            result=result,
        )
