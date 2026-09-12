"""
P0 safety layer for preventing duplicate agent executions.

This module is intentionally isolated so the orchestrator can adopt it without
changing existing workflow behavior immediately.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class GuardDecision:
    allowed: bool
    reason: str
    fingerprint: str


class RequestGuard:
    """Detect repeated agent actions before expensive execution."""

    def __init__(self, cache_file: str | Path):
        self.cache_file = Path(cache_file)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

    def fingerprint(self, agent: str, context: Any, files: Any = None) -> str:
        payload = {
            "agent": agent,
            "context": context,
            "files": files,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def check(self, agent: str, context: Any, files: Any = None) -> GuardDecision:
        fp = self.fingerprint(agent, context, files)
        cache = self._load()

        if fp in cache:
            return GuardDecision(
                allowed=False,
                reason="duplicate_request_detected",
                fingerprint=fp,
            )

        return GuardDecision(
            allowed=True,
            reason="new_request",
            fingerprint=fp,
        )

    def record(self, fingerprint: str, result: str = "executed") -> None:
        cache = self._load()
        cache[fingerprint] = {
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.cache_file.write_text(
            json.dumps(cache, indent=2),
            encoding="utf-8",
        )

    def _load(self) -> dict:
        if not self.cache_file.exists():
            return {}
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
