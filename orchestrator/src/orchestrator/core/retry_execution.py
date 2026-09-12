"""Durable bounded retry execution for P1.

RetryPolicy decides what should happen after a failure. This controller is the
only layer that is allowed to turn a RETRY decision into another external
agent call. It keeps a persistent per-action ledger, enforces retry/cost
budgets, and makes backoff durable across process restarts.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from orchestrator.core.models import AgentOutput


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
class RetryBudget:
    max_retries: int = 3
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_execution_seconds: float | None = None

    @property
    def max_attempts(self) -> int:
        return 1 + max(self.max_retries, 0)


class RetryExecutionController:
    """Run explicit, bounded retries without hiding an unbounded loop."""

    def __init__(
        self,
        orchestrator: Any,
        project_id: str,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.project_id = project_id
        self.project_dir = Path(orchestrator.projects_dir) / project_id
        self.state_file = self.project_dir / "checkpoints" / "retry_execution.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.sleeper = sleeper
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def execute(
        self,
        agent_name: str,
        context: dict[str, Any] | None = None,
        files: list[Path] | None = None,
    ) -> AgentOutput:
        context = dict(context or {})
        context.setdefault("project_id", self.project_id)
        if context.get("_workflow_stage") and not context.get("workflow_stage"):
            context["workflow_stage"] = context["_workflow_stage"]

        budget = self._load_budget()
        key = self._scope_key(agent_name, context)
        self._start_cycle_if_needed(key, agent_name, context)

        while True:
            gate = self._preflight(key, budget)
            if gate is not None:
                return AgentOutput(
                    agent_name=agent_name,
                    success=False,
                    content=f"Execution blocked by P1 retry budget: {gate['reason']}",
                    needs_review=True,
                    metadata={
                        "blocked_by": "p1_retry_execution",
                        "reason": gate["reason"],
                        "retry_budget": gate,
                    },
                )

            if files is None:
                output = self.orchestrator.run_agent(
                    self.project_id,
                    agent_name,
                    context,
                )
            else:
                output = self.orchestrator.run_agent(
                    self.project_id,
                    agent_name,
                    context,
                    files=files,
                )

            # P0/P1 preflight blocks do not consume an external-call budget.
            if not output.metadata.get("blocked_by"):
                self._record_attempt(key, output)

            if output.success:
                self._resolve_cycle(key)
                output.metadata["retry_execution"] = self._snapshot(key, budget)
                return output

            policy = output.metadata.get("retry_policy")
            if not isinstance(policy, dict):
                output.metadata["retry_execution"] = self._snapshot(key, budget)
                return output

            action = str(policy.get("action") or "")
            allowed = bool(policy.get("allowed"))
            delay = max(int(policy.get("delay_seconds") or 0), 0)

            # Rate-limit style BACKOFF is durable but not auto-retried. A later
            # resume must occur after next_retry_at.
            if action == "backoff":
                self._set_backoff(key, delay)
                output.metadata["retry_execution"] = self._snapshot(key, budget)
                return output

            if action != "retry" or not allowed:
                output.metadata["retry_execution"] = self._snapshot(key, budget)
                return output

            # RETRY is automatic but still delayed and budgeted.
            after_attempt = self._preflight(key, budget, ignore_backoff=True)
            if after_attempt is not None:
                output.metadata["retry_execution"] = self._snapshot(key, budget)
                output.metadata["retry_execution"]["next_action"] = "stop"
                output.metadata["retry_execution"]["stop_reason"] = after_attempt["reason"]
                return output

            self._set_backoff(key, delay)
            if delay:
                self.sleeper(delay)
            self._clear_backoff(key)

    def _load_budget(self) -> RetryBudget:
        path = self.project_dir / "project.toml"
        settings: dict[str, Any] = {}
        if path.exists():
            try:
                parsed = tomllib.loads(path.read_text(encoding="utf-8"))
                settings = dict(parsed.get("settings") or {})
            except (OSError, tomllib.TOMLDecodeError, TypeError):
                settings = {}

        def _optional_int(name: str) -> int | None:
            value = settings.get(name)
            if value in (None, ""):
                return None
            try:
                return max(int(value), 0)
            except (TypeError, ValueError):
                return None

        def _optional_float(name: str) -> float | None:
            value = settings.get(name)
            if value in (None, ""):
                return None
            try:
                return max(float(value), 0.0)
            except (TypeError, ValueError):
                return None

        try:
            max_retries = max(int(settings.get("max_retries", 3)), 0)
        except (TypeError, ValueError):
            max_retries = 3

        return RetryBudget(
            max_retries=max_retries,
            max_tokens=_optional_int("max_retry_tokens"),
            max_cost_usd=_optional_float("max_retry_cost_usd"),
            max_execution_seconds=_optional_float("max_retry_execution_seconds"),
        )

    def _scope(self, agent_name: str, context: dict[str, Any]) -> dict[str, Any]:
        value: dict[str, Any] = {"agent": agent_name}
        for field in _SCOPE_FIELDS:
            item = context.get(field)
            if item not in (None, "", [], {}):
                value[field] = item
        return value

    def _scope_key(self, agent_name: str, context: dict[str, Any]) -> str:
        encoded = json.dumps(
            self._scope(agent_name, context),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _start_cycle_if_needed(
        self,
        key: str,
        agent_name: str,
        context: dict[str, Any],
    ) -> None:
        store = self._load()
        entry = store.setdefault("scopes", {}).get(key)
        if not isinstance(entry, dict) or entry.get("status") == "resolved":
            store["scopes"][key] = {
                "agent": agent_name,
                "scope": self._scope(agent_name, context),
                "status": "active",
                "attempts": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "total_execution_seconds": 0.0,
                "next_retry_at": None,
                "started_at": self.now_fn().isoformat(),
                "updated_at": self.now_fn().isoformat(),
            }
            self._save(store)

    def _preflight(
        self,
        key: str,
        budget: RetryBudget,
        *,
        ignore_backoff: bool = False,
    ) -> dict[str, Any] | None:
        entry = self._load().get("scopes", {}).get(key, {})
        attempts = int(entry.get("attempts", 0))
        total_tokens = int(entry.get("total_tokens", 0))
        total_cost = float(entry.get("total_cost_usd", 0.0))
        total_seconds = float(entry.get("total_execution_seconds", 0.0))

        if attempts >= budget.max_attempts:
            return self._budget_payload(entry, budget, "retry_attempt_budget_exhausted")
        if budget.max_tokens is not None and total_tokens >= budget.max_tokens:
            return self._budget_payload(entry, budget, "retry_token_budget_exhausted")
        if budget.max_cost_usd is not None and total_cost >= budget.max_cost_usd:
            return self._budget_payload(entry, budget, "retry_cost_budget_exhausted")
        if (
            budget.max_execution_seconds is not None
            and total_seconds >= budget.max_execution_seconds
        ):
            return self._budget_payload(entry, budget, "retry_time_budget_exhausted")

        if not ignore_backoff:
            not_before = entry.get("next_retry_at")
            if isinstance(not_before, str) and not_before:
                try:
                    target = datetime.fromisoformat(not_before)
                    now = self.now_fn()
                    if target > now:
                        payload = self._budget_payload(entry, budget, "backoff_active")
                        payload["retry_after_seconds"] = max(
                            int((target - now).total_seconds()),
                            1,
                        )
                        return payload
                except ValueError:
                    pass
        return None

    def _record_attempt(self, key: str, output: AgentOutput) -> None:
        store = self._load()
        entry = store.setdefault("scopes", {}).setdefault(key, {})
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["total_execution_seconds"] = float(
            entry.get("total_execution_seconds", 0.0)
        ) + self._number(output.metadata.get("execution_time"))
        usage = output.metadata.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        total_tokens = output.metadata.get("total_tokens", usage.get("total_tokens"))
        cost_usd = output.metadata.get("cost_usd", usage.get("cost_usd"))
        entry["total_tokens"] = int(entry.get("total_tokens", 0)) + int(
            self._number(total_tokens)
        )
        entry["total_cost_usd"] = float(entry.get("total_cost_usd", 0.0)) + self._number(
            cost_usd
        )
        entry["last_output_success"] = bool(output.success)
        entry["updated_at"] = self.now_fn().isoformat()
        self._save(store)

    def _set_backoff(self, key: str, delay_seconds: int) -> None:
        store = self._load()
        entry = store.setdefault("scopes", {}).setdefault(key, {})
        entry["next_retry_at"] = (
            self.now_fn() + timedelta(seconds=max(delay_seconds, 0))
        ).isoformat()
        entry["updated_at"] = self.now_fn().isoformat()
        self._save(store)

    def _clear_backoff(self, key: str) -> None:
        store = self._load()
        entry = store.setdefault("scopes", {}).setdefault(key, {})
        entry["next_retry_at"] = None
        entry["updated_at"] = self.now_fn().isoformat()
        self._save(store)

    def _resolve_cycle(self, key: str) -> None:
        store = self._load()
        entry = store.setdefault("scopes", {}).get(key)
        if isinstance(entry, dict):
            entry["status"] = "resolved"
            entry["next_retry_at"] = None
            entry["resolved_at"] = self.now_fn().isoformat()
            entry["updated_at"] = entry["resolved_at"]
            self._save(store)

    def _snapshot(self, key: str, budget: RetryBudget) -> dict[str, Any]:
        entry = dict(self._load().get("scopes", {}).get(key, {}))
        return self._budget_payload(entry, budget, "active")

    def _budget_payload(
        self,
        entry: dict[str, Any],
        budget: RetryBudget,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "reason": reason,
            "attempts": int(entry.get("attempts", 0)),
            "max_attempts": budget.max_attempts,
            "max_retries": budget.max_retries,
            "total_tokens": int(entry.get("total_tokens", 0)),
            "max_tokens": budget.max_tokens,
            "total_cost_usd": float(entry.get("total_cost_usd", 0.0)),
            "max_cost_usd": budget.max_cost_usd,
            "total_execution_seconds": float(entry.get("total_execution_seconds", 0.0)),
            "max_execution_seconds": budget.max_execution_seconds,
            "next_retry_at": entry.get("next_retry_at"),
            "status": entry.get("status", "active"),
        }

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _load(self) -> dict[str, Any]:
        lock_path = self.state_file.with_suffix(self.state_file.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
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
        lock_path = self.state_file.with_suffix(self.state_file.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            temporary = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.state_file)
