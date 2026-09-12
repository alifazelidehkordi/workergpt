"""Core data models for the Project Orchestrator."""

from __future__ import annotations

import json
import fcntl
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ExecuteRequest:
    prompt: str
    files: list[Path] = field(default_factory=list)
    system_message: str | None = None
    expected_output: str = "text"
    timeout_seconds: int = 300
    profile: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecuteResult:
    success: bool
    text_response: str | None = None
    downloaded_files: list[Path] = field(default_factory=list)
    error: str | None = None
    limit_hit: bool = False
    raw_logs: str | None = None
    execution_time: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentOutput:
    agent_name: str
    success: bool
    content: str
    structured_data: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    needs_review: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectState:
    project_id: str
    current_phase: str | None = None
    current_module: str | None = None
    status: str = "in_progress"
    last_updated: str = field(default_factory=utc_now_iso)
    progress: dict[str, Any] = field(default_factory=lambda: {
        "phases_completed": [],
        "modules_completed": [],
        "pending": [],
    })
    last_checkpoint: str | None = None
    active_agent: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectState":
        return cls(**data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> "ProjectState":
        lock_path = path.with_suffix(path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class ProjectConfig:
    id: str
    name: str
    description: str = ""
    created: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%d"))
    status: str = "active"
    default_model_preference: str = "balanced"
    max_retries: int = 3
    auto_checkpoint: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
