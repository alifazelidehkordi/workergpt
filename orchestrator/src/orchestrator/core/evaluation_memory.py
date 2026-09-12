"""Persistent memory for evaluation outcomes and repair strategies."""

import json
from pathlib import Path
from typing import Any


class EvaluationMemory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def record(self, issue: str, strategy: str, success: bool) -> None:
        data = self._load()
        history = data.setdefault(issue, [])
        history.append({"strategy": strategy, "success": success})
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, issue: str) -> list[dict[str, Any]]:
        return self._load().get(issue, [])
