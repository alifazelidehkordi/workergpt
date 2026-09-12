"""
P0 safety layer for preventing duplicate agent executions.

The fingerprint is based on semantic agent input and file content identity so
rewriting a file at the same path is treated as new work while resubmitting an
unchanged file is blocked.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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
            "files": self._normalize_files(files),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
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
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _normalize_files(self, files: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in files or []:
            path = Path(item)
            entry: dict[str, Any] = {"path": str(path)}
            if path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                entry["sha256"] = digest.hexdigest()
                entry["size"] = path.stat().st_size
            else:
                entry["missing"] = True
            normalized.append(entry)
        return normalized

    def _load(self) -> dict[str, Any]:
        if not self.cache_file.exists():
            return {}
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            # Cache corruption must never crash the workflow. Treat it as empty
            # and allow the request; higher layers can still log the incident.
            return {}
