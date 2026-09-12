"""P1 retry execution budget controls.

Prevents a valid retry policy from becoming an unlimited execution loop.
The budget is deterministic and does not require an LLM call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import json


@dataclass(frozen=True)
class RetryBudgetDecision:
    allowed: bool
    reason: str
    attempts: int
    token_estimate: int
    cost_units: int


class RetryBudget:
    def __init__(
        self,
        state_file: str | Path,
        *,
        max_attempts: int = 5,
        max_token_estimate: int = 50000,
        max_cost_units: int = 20,
    ) -> None:
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        self.max_token_estimate = max_token_estimate
        self.max_cost_units = max_cost_units

    def check(self, scope_key: str) -> RetryBudgetDecision:
        state = self._load()
        item = state.get(scope_key, {})
        attempts = int(item.get("attempts", 0))
        tokens = int(item.get("token_estimate", 0))
        cost = int(item.get("cost_units", 0))

        if attempts >= self.max_attempts:
            return RetryBudgetDecision(False, "retry_attempt_budget_exceeded", attempts, tokens, cost)
        if tokens >= self.max_token_estimate:
            return RetryBudgetDecision(False, "retry_token_budget_exceeded", attempts, tokens, cost)
        if cost >= self.max_cost_units:
            return RetryBudgetDecision(False, "retry_cost_budget_exceeded", attempts, tokens, cost)
        return RetryBudgetDecision(True, "retry_budget_available", attempts, tokens, cost)

    def record(self, scope_key: str, *, token_estimate: int = 0, cost_units: int = 1) -> None:
        state = self._load()
        item = state.setdefault(scope_key, {})
        item["attempts"] = int(item.get("attempts", 0)) + 1
        item["token_estimate"] = int(item.get("token_estimate", 0)) + token_estimate
        item["cost_units"] = int(item.get("cost_units", 0)) + cost_units
        item["updated_at"] = datetime.now(UTC).isoformat()
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def reset(self, scope_key: str) -> None:
        state = self._load()
        state.pop(scope_key, None)
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
