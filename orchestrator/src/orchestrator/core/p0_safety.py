"""Unified P0 safety controls for agent execution decisions."""

from __future__ import annotations

from .request_guard import RequestGuard
from .stagnation_detector import StagnationDetector


class P0SafetyPolicy:
    """Combines immediate loop protection mechanisms.

    This is intentionally a small facade so existing orchestration flow can
    adopt safety checks incrementally without a large rewrite.
    """

    def __init__(self, cache_file, max_stagnant_attempts=3):
        self.request_guard = RequestGuard(cache_file)
        self.stagnation_detector = StagnationDetector(max_stagnant_attempts=max_stagnant_attempts)

    def allow_request(self, agent, context, files=None):
        return self.request_guard.check(agent, context, files)

    def record_request(self, fingerprint, result="executed"):
        self.request_guard.record(fingerprint, result)

    def evaluate_progress(self, previous_score, current_score, stagnant_count):
        return self.stagnation_detector.evaluate(
            previous_score,
            current_score,
            stagnant_count,
        )
