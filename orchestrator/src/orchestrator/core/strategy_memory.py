"""Persistent strategy outcome memory foundation."""

from pathlib import Path
import json


class StrategyMemory:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def record(self, problem: str, strategy: str, success: bool) -> None:
        data = self.load()
        data.setdefault(problem, []).append({"strategy": strategy, "success": success})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))
