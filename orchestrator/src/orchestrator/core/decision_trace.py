"""Durable decision trace for P3 autonomous decision debugging."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class DecisionTrace:
    """Append-only record of autonomous decisions."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, decision: dict[str, Any]) -> None:
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            **decision,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
