"""Persistent P1 failure classification and recurrence memory.

P0 answers "is this work duplicated or stagnant?". P1 needs a different
question: "what failed, has this exact failure happened before, and is another
retry with the same strategy justified?"

This module stays deterministic and local. Failure classification does not spend
LLM tokens and its persisted state survives process restarts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_SCOPE_FIELDS = (
    "project_id",
    "current_phase",
    "current_module",
    "topic_id",
    "topic_title",
    "section_id",
    "section_title",
    "research_question",
    "workflow_stage",
)


@dataclass(frozen=True)
class FailureClassification:
    category: str
    retryable: bool
    transient: bool
    strategy_change_on_repeat: bool
    normalized_reason: str
    signature: str


@dataclass(frozen=True)
class RetryDecision:
    allowed: bool
    reason: str
    scope_key: str
    category: str | None = None
    signature: str | None = None
    occurrence: int = 0
    strategy_change_required: bool = False
    previous_strategy_id: str | None = None


class FailureClassifier:
    """Classify failures using stable deterministic signals."""

    def classify(
        self,
        error: str | None,
        *,
        limit_hit: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> FailureClassification:
        metadata = metadata or {}
        raw = (error or "unknown failure").strip()
        normalized = self._normalize(raw)
        lowered = normalized.casefold()

        if limit_hit or self._contains(
            lowered,
            "rate limit",
            "usage limit",
            "reached the limit",
            "too many requests",
        ):
            category, retryable, transient, strategy_change = (
                "rate_limit",
                True,
                True,
                False,
            )
        elif self._contains(lowered, "timed out", "timeout", "time out"):
            category, retryable, transient, strategy_change = (
                "timeout",
                True,
                True,
                False,
            )
        elif self._contains(
            lowered,
            "network error",
            "connection error",
            "connection reset",
            "connection refused",
            "temporarily unavailable",
            "service unavailable",
        ):
            category, retryable, transient, strategy_change = (
                "network",
                True,
                True,
                False,
            )
        elif self._contains(
            lowered,
            "not authenticated",
            "authentication",
            "login required",
            "session expired",
            "unauthorized",
        ):
            category, retryable, transient, strategy_change = (
                "authentication",
                False,
                False,
                False,
            )
        elif self._contains(
            lowered,
            "duplicate_request_detected",
            "no_measurable_progress",
            "p0 safety",
            "p0 stagnation",
        ):
            category, retryable, transient, strategy_change = (
                "safety_stop",
                False,
                False,
                False,
            )
        elif self._contains(
            lowered,
            "json",
            "schema",
            "parse",
            "unexpected fields",
            "must contain",
            "must be",
            "missing required",
            "invalid ",
            "validation",
            "verdict",
            "patch",
            "heading",
            "fewer than",
            "more than",
            "no traceable source",
        ):
            category, retryable, transient, strategy_change = (
                "validation",
                True,
                False,
                True,
            )
        elif self._contains(
            lowered,
            "source",
            "doi",
            "pmid",
            "unverifiable",
            "mismatch",
            "citation",
            "fabricat",
        ):
            category, retryable, transient, strategy_change = (
                "source_quality",
                True,
                False,
                True,
            )
        elif metadata.get("provider") or metadata.get("mock") is not None:
            category, retryable, transient, strategy_change = (
                "executor",
                True,
                False,
                True,
            )
        else:
            category, retryable, transient, strategy_change = (
                "workflow",
                True,
                False,
                True,
            )

        signature_material = f"{category}:{normalized}"
        signature = hashlib.sha256(signature_material.encode("utf-8")).hexdigest()
        return FailureClassification(
            category=category,
            retryable=retryable,
            transient=transient,
            strategy_change_on_repeat=strategy_change,
            normalized_reason=normalized,
            signature=signature,
        )

    @staticmethod
    def _contains(value: str, *needles: str) -> bool:
        return any(needle in value for needle in needles)

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = value.casefold().strip()
        normalized = re.sub(r"https?://\S+", "<url>", normalized)
        normalized = re.sub(r"\b[0-9a-f]{12,}\b", "<hex>", normalized)
        normalized = re.sub(r"(?<!\w)\d+(?:\.\d+)?(?!\w)", "<n>", normalized)
        normalized = re.sub(r"(?:[a-zA-Z]:)?[/\\][^\s'\"]+", "<path>", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized[:1000]


class FailureMemory:
    """Persist active failures and stop blind same-strategy retries."""

    def __init__(
        self,
        state_file: str | Path,
        *,
        same_strategy_threshold: int = 2,
        classifier: FailureClassifier | None = None,
    ) -> None:
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.same_strategy_threshold = same_strategy_threshold
        self.classifier = classifier or FailureClassifier()

    def scope(self, agent_name: str, context: dict[str, Any]) -> dict[str, Any]:
        scope: dict[str, Any] = {"agent": agent_name}
        for field in _SCOPE_FIELDS:
            value = context.get(field)
            if value not in (None, "", [], {}):
                scope[field] = value
        return scope

    def scope_key(self, agent_name: str, context: dict[str, Any]) -> str:
        encoded = json.dumps(
            self.scope(agent_name, context),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def strategy_id(context: dict[str, Any]) -> str:
        value = context.get("_strategy_id")
        return str(value).strip() if value not in (None, "") else "default"

    def check_retry(self, agent_name: str, context: dict[str, Any]) -> RetryDecision:
        key = self.scope_key(agent_name, context)
        entry = self._load().get("scopes", {}).get(key, {})
        active = entry.get("active_failure")
        if not isinstance(active, dict):
            return RetryDecision(True, "no_active_failure", key)

        classification = dict(active.get("classification") or {})
        category = classification.get("category")
        signature = classification.get("signature")
        retryable = bool(classification.get("retryable", True))
        transient = bool(classification.get("transient", False))
        require_change = bool(classification.get("strategy_change_on_repeat", False))
        occurrence = int(active.get("same_strategy_count", 0))
        previous_strategy = str(active.get("strategy_id") or "default")
        current_strategy = self.strategy_id(context)

        if not retryable:
            return RetryDecision(
                False,
                "non_retryable_failure",
                key,
                category=category,
                signature=signature,
                occurrence=occurrence,
                previous_strategy_id=previous_strategy,
            )

        if transient:
            return RetryDecision(
                True,
                "transient_retry_allowed",
                key,
                category=category,
                signature=signature,
                occurrence=occurrence,
                previous_strategy_id=previous_strategy,
            )

        if current_strategy != previous_strategy:
            return RetryDecision(
                True,
                "strategy_changed",
                key,
                category=category,
                signature=signature,
                occurrence=occurrence,
                previous_strategy_id=previous_strategy,
            )

        if require_change and occurrence >= self.same_strategy_threshold:
            return RetryDecision(
                False,
                "strategy_change_required",
                key,
                category=category,
                signature=signature,
                occurrence=occurrence,
                strategy_change_required=True,
                previous_strategy_id=previous_strategy,
            )

        return RetryDecision(
            True,
            "retry_allowed",
            key,
            category=category,
            signature=signature,
            occurrence=occurrence,
            previous_strategy_id=previous_strategy,
        )

    def record_failure(
        self,
        agent_name: str,
        context: dict[str, Any],
        error: str | None,
        *,
        limit_hit: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[FailureClassification, dict[str, Any]]:
        store = self._load()
        scopes = store.setdefault("scopes", {})
        key = self.scope_key(agent_name, context)
        entry = scopes.setdefault(
            key,
            {
                "agent": agent_name,
                "scope": self.scope(agent_name, context),
                "history": [],
            },
        )
        classification = self.classifier.classify(
            error,
            limit_hit=limit_hit,
            metadata=metadata,
        )
        strategy = self.strategy_id(context)
        previous = entry.get("active_failure")

        same_signature = (
            isinstance(previous, dict)
            and previous.get("classification", {}).get("signature") == classification.signature
        )
        same_strategy = same_signature and previous.get("strategy_id", "default") == strategy
        consecutive_count = int(previous.get("consecutive_count", 0)) + 1 if same_signature else 1
        same_strategy_count = int(previous.get("same_strategy_count", 0)) + 1 if same_strategy else 1

        now = datetime.now(UTC).isoformat()
        active = {
            "classification": asdict(classification),
            "strategy_id": strategy,
            "consecutive_count": consecutive_count,
            "same_strategy_count": same_strategy_count,
            "error": error or "unknown failure",
            "first_seen_at": (
                previous.get("first_seen_at", now) if same_signature and isinstance(previous, dict) else now
            ),
            "last_seen_at": now,
        }
        entry["active_failure"] = active
        entry["updated_at"] = now
        entry.setdefault("history", []).append(
            {
                "at": now,
                "classification": asdict(classification),
                "strategy_id": strategy,
                "consecutive_count": consecutive_count,
                "same_strategy_count": same_strategy_count,
                "error": error or "unknown failure",
            }
        )
        entry["history"] = entry["history"][-50:]
        self._save(store)
        return classification, active

    def resolve(self, agent_name: str, context: dict[str, Any]) -> None:
        store = self._load()
        key = self.scope_key(agent_name, context)
        entry = store.get("scopes", {}).get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get("active_failure"), dict):
            return
        entry["last_resolved_failure"] = entry.pop("active_failure")
        entry["resolved_at"] = datetime.now(UTC).isoformat()
        entry["updated_at"] = entry["resolved_at"]
        self._save(store)

    def _load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"version": 1, "scopes": {}}
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "scopes": {}}
        if not isinstance(value, dict):
            return {"version": 1, "scopes": {}}
        value.setdefault("version", 1)
        value.setdefault("scopes", {})
        return value

    def _save(self, value: dict[str, Any]) -> None:
        temporary = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.state_file)
