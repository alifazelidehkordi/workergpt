"""Persistent P0 progress evaluation for agent retries.

The duplicate guard stops byte-for-byte equivalent work before execution. This
module handles the next failure mode: requests that keep changing slightly while
their outputs, unresolved issues, and quality signals do not materially improve.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator.core.models import AgentOutput
from orchestrator.core.stagnation_detector import StagnationDetector


_SCOPE_FIELDS = (
    "project_id",
    "current_phase",
    "current_module",
    "topic_id",
    "topic_title",
    "section_id",
    "section_title",
    "research_question",
    "_strategy_id",
)
_ISSUE_KEYS = {
    "issues",
    "issue",
    "issue_ids",
    "review_feedback",
    "feedback",
    "critic_issues",
    "unresolved_issues",
    "problems",
}
_SCORE_KEYS = ("quality_score", "score", "confidence")


@dataclass(frozen=True)
class ProgressDecision:
    key: str
    should_stop: bool
    reason: str
    stagnant_count: int
    measurable_progress: bool
    artifact_changed: bool
    substantive_artifact_change: bool
    issues_improved: bool
    score_improved: bool
    score_delta: float | None = None


class PersistentProgressGuard:
    """Persist progress snapshots so stagnation survives process restarts."""

    def __init__(
        self,
        state_file: str | Path,
        *,
        max_stagnant_attempts: int = 3,
        min_score_delta: float = 0.01,
        term_similarity_threshold: float = 0.92,
        length_change_threshold: float = 0.08,
    ) -> None:
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.detector = StagnationDetector(
            max_stagnant_attempts=max_stagnant_attempts,
            min_delta=min_score_delta,
        )
        self.min_score_delta = min_score_delta
        self.term_similarity_threshold = term_similarity_threshold
        self.length_change_threshold = length_change_threshold

    def scope(self, agent_name: str, context: dict[str, Any]) -> dict[str, Any]:
        scope: dict[str, Any] = {"agent": agent_name}
        for field in _SCOPE_FIELDS:
            value = context.get(field)
            if value not in (None, "", [], {}):
                scope[field] = value
        return scope

    def key(self, agent_name: str, context: dict[str, Any]) -> str:
        encoded = json.dumps(
            self.scope(agent_name, context),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def check(self, agent_name: str, context: dict[str, Any]) -> ProgressDecision:
        key = self.key(agent_name, context)
        entry = self._load().get(key, {})
        stopped = bool(entry.get("stopped"))
        stagnant_count = int(entry.get("stagnant_count", 0))
        return ProgressDecision(
            key=key,
            should_stop=stopped,
            reason="no_measurable_progress" if stopped else "retry_allowed",
            stagnant_count=stagnant_count,
            measurable_progress=False,
            artifact_changed=False,
            substantive_artifact_change=False,
            issues_improved=False,
            score_improved=False,
            score_delta=None,
        )

    def record_result(
        self,
        agent_name: str,
        context: dict[str, Any],
        output: AgentOutput,
    ) -> ProgressDecision:
        store = self._load()
        key = self.key(agent_name, context)
        previous_entry = store.get(key, {})
        previous = dict(previous_entry.get("snapshot") or {})
        current = self._snapshot(context, output)

        if not previous:
            stagnant_count = 0
            decision = ProgressDecision(
                key=key,
                should_stop=False,
                reason="baseline_recorded",
                stagnant_count=0,
                measurable_progress=True,
                artifact_changed=True,
                substantive_artifact_change=True,
                issues_improved=False,
                score_improved=False,
                score_delta=None,
            )
        else:
            artifact_changed = current["artifact_hash"] != previous.get("artifact_hash")
            similarity = self._jaccard(
                set(previous.get("content_terms") or []),
                set(current.get("content_terms") or []),
            )
            previous_length = int(previous.get("char_count") or 0)
            current_length = int(current.get("char_count") or 0)
            length_delta = abs(current_length - previous_length) / max(previous_length, 1)
            substantive_artifact_change = artifact_changed and (
                similarity < self.term_similarity_threshold
                or length_delta >= self.length_change_threshold
            )

            previous_issues = set(previous.get("issue_ids") or [])
            current_issues = set(current.get("issue_ids") or [])
            issues_improved = bool(previous_issues) and current_issues < previous_issues

            previous_score = previous.get("quality_score")
            current_score = current.get("quality_score")
            score_delta: float | None = None
            score_improved = False
            if isinstance(previous_score, (int, float)) and isinstance(current_score, (int, float)):
                score_delta = float(current_score) - float(previous_score)
                score_improved = score_delta >= self.min_score_delta

            measurable_progress = (
                substantive_artifact_change or issues_improved or score_improved
            )
            stagnation = self.detector.evaluate_signal(
                measurable_progress,
                int(previous_entry.get("stagnant_count", 0)),
            )
            stagnant_count = stagnation.stagnant_count
            decision = ProgressDecision(
                key=key,
                should_stop=stagnation.should_stop,
                reason=stagnation.reason,
                stagnant_count=stagnant_count,
                measurable_progress=measurable_progress,
                artifact_changed=artifact_changed,
                substantive_artifact_change=substantive_artifact_change,
                issues_improved=issues_improved,
                score_improved=score_improved,
                score_delta=score_delta,
            )

        store[key] = {
            "agent": agent_name,
            "scope": self.scope(agent_name, context),
            "stagnant_count": stagnant_count,
            "stopped": decision.should_stop,
            "stop_reason": decision.reason if decision.should_stop else None,
            "snapshot": current,
            "last_decision": asdict(decision),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._save(store)
        return decision

    def reset(self, agent_name: str, context: dict[str, Any]) -> None:
        store = self._load()
        store.pop(self.key(agent_name, context), None)
        self._save(store)

    def _snapshot(self, context: dict[str, Any], output: AgentOutput) -> dict[str, Any]:
        content = output.content or ""
        parsed = self._parse_json_payload(content)
        issue_ids = self._collect_issue_ids(context)
        issue_ids.update(self._collect_issue_ids(output.structured_data))
        if parsed is not None:
            issue_ids.update(self._collect_issue_ids(parsed))

        quality_score = self._quality_score(output, parsed, context)
        return {
            "artifact_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "char_count": len(content),
            "content_terms": self._content_terms(content),
            "issue_ids": sorted(issue_ids),
            "quality_score": quality_score,
            "success": output.success,
        }

    def _quality_score(
        self,
        output: AgentOutput,
        parsed: dict[str, Any] | None,
        context: dict[str, Any],
    ) -> float | None:
        candidates: list[Any] = [output.confidence]
        for source in (output.structured_data, parsed or {}, context):
            for key in _SCORE_KEYS:
                candidates.append(source.get(key))
        for value in candidates:
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    continue
        return None

    def _collect_issue_ids(self, value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith(("{", "[")):
                try:
                    return self._collect_issue_ids(json.loads(stripped))
                except json.JSONDecodeError:
                    return found
            return found
        if isinstance(value, list):
            for item in value:
                found.update(self._collect_issue_ids(item))
            return found
        if not isinstance(value, dict):
            return found

        issue_id = value.get("issue_id") or value.get("id")
        if isinstance(issue_id, str) and issue_id.strip():
            found.add(issue_id.strip())
        for key, item in value.items():
            if key in _ISSUE_KEYS or isinstance(item, (dict, list)):
                found.update(self._collect_issue_ids(item))
        return found

    @staticmethod
    def _parse_json_payload(content: str) -> dict[str, Any] | None:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _content_terms(content: str) -> list[str]:
        terms = {
            token.casefold()
            for token in re.findall(r"[^\W_]{4,}", content, flags=re.UNICODE)
        }
        return sorted(terms)[:512]

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left and not right:
            return 1.0
        union = left | right
        return len(left & right) / len(union) if union else 1.0

    def _load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, store: dict[str, Any]) -> None:
        temporary = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(store, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.state_file)
