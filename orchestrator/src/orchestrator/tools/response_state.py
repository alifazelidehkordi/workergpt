"""Deterministic response-observation state used by browser providers.

The web UI can briefly hide the stop button while a tool call or rerender is
in progress.  Keeping this state machine separate makes completion decisions
testable and prevents a transient quiet frame from being treated as final.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ResponseState(str, Enum):
    WAITING = "waiting"
    GENERATING = "generating"
    RATE_LIMITED = "rate_limited"
    STABLE = "stable"


@dataclass(frozen=True)
class ResponseObservation:
    assistant_count: int
    generating: bool
    body_text: str
    rate_limited: bool = False
    now: float = 0.0


class ResponseStateMachine:
    """Track response progress and require a quiet, unchanged window."""

    def __init__(self, *, required_assistant_count: int | None, stable_seconds: float = 3.0) -> None:
        self.required_assistant_count = required_assistant_count
        self.stable_seconds = stable_seconds
        self._last_text = ""
        self._stable_since: float | None = None

    def observe(self, observation: ResponseObservation) -> ResponseState:
        if observation.rate_limited:
            self._stable_since = None
            return ResponseState.RATE_LIMITED
        if (
            self.required_assistant_count is not None
            and observation.assistant_count < self.required_assistant_count
        ):
            self._stable_since = None
            self._last_text = observation.body_text
            return ResponseState.WAITING
        if observation.generating:
            self._stable_since = None
            self._last_text = observation.body_text
            return ResponseState.GENERATING
        if not observation.body_text:
            self._stable_since = None
            return ResponseState.WAITING
        if observation.body_text != self._last_text:
            self._last_text = observation.body_text
            self._stable_since = None
            return ResponseState.WAITING
        self._stable_since = self._stable_since or observation.now
        if observation.now - self._stable_since >= self.stable_seconds:
            return ResponseState.STABLE
        return ResponseState.WAITING
