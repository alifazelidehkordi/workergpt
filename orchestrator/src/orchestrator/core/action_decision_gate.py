"""P0 action decision gate.

Prevents an agent from executing the same logical action repeatedly when the
system state has not changed. This is intentionally separate from execution
success caching: it protects the decision loop itself.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ActionDecision:
    allowed: bool
    reason: str
    fingerprint: str
    previous_attempts: int = 0


class ActionDecisionGate:
    """State-aware duplicate action protection."""

    def __init__(self, state_file: str | Path, max_same_state_attempts: int = 2):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.max_same_state_attempts = max_same_state_attempts

    def check(self, action: str, context: dict[str, Any]) -> ActionDecision:
        fingerprint = self._fingerprint(action, context)
        store = self._load()
        attempts = int(store.get(fingerprint, {}).get("attempts", 0))

        if attempts >= self.max_same_state_attempts:
            return ActionDecision(
                False,
                "same_action_same_state_threshold_reached",
                fingerprint,
                attempts,
            )

        return ActionDecision(True, "action_not_repeated", fingerprint, attempts)

    def record(self, decision: ActionDecision, result: str) -> None:
        store = self._load()
        item = store.get(decision.fingerprint, {})
        store[decision.fingerprint] = {
            "attempts": int(item.get("attempts", 0)) + 1,
            "last_result": result,
        }
        self._save(store)

    def reset(self, action: str, context: dict[str, Any]) -> None:
        store = self._load()
        store.pop(self._fingerprint(action, context), None)
        self._save(store)

    @staticmethod
    def _fingerprint(action: str, context: dict[str, Any]) -> str:
        payload = {
            "action": action,
            "phase": context.get("current_phase"),
            "module": context.get("current_module"),
            "goal": context.get("goal"),
            "input": context.get("input", context),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, value: dict[str, Any]) -> None:
        tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)
