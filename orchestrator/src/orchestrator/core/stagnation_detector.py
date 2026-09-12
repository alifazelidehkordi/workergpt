"""
P0 safety layer for detecting logical loops where executions continue
without measurable improvement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StagnationDecision:
    should_stop: bool
    reason: str
    delta: float


class StagnationDetector:
    """Detect repeated attempts that do not improve project state."""

    def __init__(self, max_stagnant_attempts: int = 3, min_delta: float = 0.01):
        self.max_stagnant_attempts = max_stagnant_attempts
        self.min_delta = min_delta

    def evaluate(self, previous_score: float, current_score: float, stagnant_count: int) -> StagnationDecision:
        delta = current_score - previous_score

        if delta < self.min_delta:
            stagnant_count += 1
        else:
            stagnant_count = 0

        if stagnant_count >= self.max_stagnant_attempts:
            return StagnationDecision(
                should_stop=True,
                reason="no_measurable_progress",
                delta=delta,
            )

        return StagnationDecision(
            should_stop=False,
            reason="progress_detected_or_retry_allowed",
            delta=delta,
        )

    def compare_artifacts(self, previous: Any, current: Any) -> bool:
        return previous != current
