"""P0 execution gate.

Small integration boundary between the orchestrator and safety policies.
The orchestrator calls this before executor.execute() so expensive agent
requests must pass duplicate-execution protection first.
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
        decision = self.policy.allow_request(
            agent=agent_name,
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
        # Recompute the deterministic fingerprint from the same semantic input
        # used by allow(). This intentionally does not depend on transient
        # timestamps or executor metadata.
        decision = self.policy.allow_request(
            agent=agent_name,
            context=context,
            files=files or [],
        )
        self.policy.record_request(decision.fingerprint, result=result)
