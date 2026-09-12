"""P0 primitives for stopping retries that make no measurable progress."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StagnationDecision:
    should_stop: bool
    reason: str
    delta: float
    stagnant_count: int


class StagnationDetector:
    """Calculate whether consecutive attempts have become stagnant."""

    def __init__(self, max_stagnant_attempts: int = 3, min_delta: float = 0.01):
        if max_stagnant_attempts < 1:
            raise ValueError("max_stagnant_attempts must be >= 1")
        self.max_stagnant_attempts = max_stagnant_attempts
        self.min_delta = min_delta

    def _decision(self, stagnant_count: int, delta: float) -> StagnationDecision:
        should_stop = stagnant_count >= self.max_stagnant_attempts
        return StagnationDecision(
            should_stop=should_stop,
            reason=(
                "no_measurable_progress"
                if should_stop
                else "progress_detected_or_retry_allowed"
            ),
            delta=delta,
            stagnant_count=stagnant_count,
        )

    def evaluate(
        self,
        previous_score: float,
        current_score: float,
        stagnant_count: int,
    ) -> StagnationDecision:
        """Evaluate numerical quality progress while carrying the count forward."""
        delta = current_score - previous_score
        next_count = stagnant_count + 1 if delta < self.min_delta else 0
        return self._decision(next_count, delta)

    def evaluate_signal(
        self,
        measurable_progress: bool,
        stagnant_count: int,
    ) -> StagnationDecision:
        """Evaluate a precomputed progress signal from artifact/issue metrics."""
        next_count = 0 if measurable_progress else stagnant_count + 1
        return self._decision(next_count, 1.0 if measurable_progress else 0.0)

    def compare_artifacts(self, previous: Any, current: Any) -> bool:
        return previous != current
