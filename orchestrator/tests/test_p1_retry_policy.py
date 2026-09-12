"""Regression tests for centralized P1 retry decisions."""

from orchestrator.core.failure_memory import FailureClassifier, RetryDecision
from orchestrator.core.models import ExecuteResult
from orchestrator.core.orchestrator import Orchestrator
from orchestrator.core.retry_policy import RetryAction, RetryPolicy


def test_validation_retry_escalates_to_strategy_change():
    classifier = FailureClassifier()
    policy = RetryPolicy(same_strategy_threshold=2)
    failure = classifier.classify("Invalid JSON schema: missing required field")

    first = policy.after_failure(failure, same_strategy_count=1)
    second = policy.after_failure(failure, same_strategy_count=2)

    assert first.action == RetryAction.RETRY
    assert first.allowed is True
    assert first.reason == "bounded_same_strategy_retry_allowed"

    assert second.action == RetryAction.CHANGE_STRATEGY
    assert second.allowed is False
    assert second.strategy_change_required is True


def test_rate_limit_uses_exponential_backoff_metadata():
    classifier = FailureClassifier()
    policy = RetryPolicy(rate_limit_base_delay_seconds=60, max_backoff_seconds=900)
    failure = classifier.classify("rate limit", limit_hit=True)

    first = policy.after_failure(failure, same_strategy_count=1)
    second = policy.after_failure(failure, same_strategy_count=2)
    fifth = policy.after_failure(failure, same_strategy_count=5)

    assert first.action == RetryAction.BACKOFF
    assert first.delay_seconds == 60
    assert second.delay_seconds == 120
    assert fifth.delay_seconds == 900


def test_timeout_is_retryable_but_authentication_requires_operator():
    classifier = FailureClassifier()
    policy = RetryPolicy(transient_base_delay_seconds=2)

    timeout = policy.after_failure(
        classifier.classify("request timed out"),
        same_strategy_count=2,
    )
    auth = policy.after_failure(
        classifier.classify("Session expired; login required"),
        same_strategy_count=1,
    )

    assert timeout.action == RetryAction.RETRY
    assert timeout.allowed is True
    assert timeout.delay_seconds == 4

    assert auth.action == RetryAction.OPERATOR_ACTION
    assert auth.allowed is False
    assert auth.operator_action_required is True


def test_preflight_maps_memory_blocks_to_correct_controller_action():
    policy = RetryPolicy()

    strategy = policy.before_retry(
        RetryDecision(
            allowed=False,
            reason="strategy_change_required",
            scope_key="scope",
            category="validation",
            occurrence=2,
            strategy_change_required=True,
        )
    )
    auth = policy.before_retry(
        RetryDecision(
            allowed=False,
            reason="non_retryable_failure",
            scope_key="scope",
            category="authentication",
            occurrence=1,
        )
    )
    safety = policy.before_retry(
        RetryDecision(
            allowed=False,
            reason="non_retryable_failure",
            scope_key="scope",
            category="safety_stop",
            occurrence=1,
        )
    )

    assert strategy.action == RetryAction.CHANGE_STRATEGY
    assert auth.action == RetryAction.OPERATOR_ACTION
    assert safety.action == RetryAction.STOP
    assert safety.terminal is True


def test_authentication_failure_blocks_next_execution_for_operator_action(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p1-auth", "P1 Authentication")

    calls = 0

    def auth_failure(_request):
        nonlocal calls
        calls += 1
        return ExecuteResult(success=False, error="Session expired; login required")

    orchestrator.executor.execute = auth_failure

    first = orchestrator.research("p1-auth", "question")
    second = orchestrator.research("p1-auth", "question")

    assert first.success is False
    assert first.metadata["retry_policy"]["action"] == "operator_action"
    assert first.metadata["retry_policy"]["operator_action_required"] is True

    assert second.success is False
    assert second.metadata["blocked_by"] == "p1_retry_policy"
    assert second.metadata["retry_policy"]["action"] == "operator_action"
    assert calls == 1

    state = orchestrator.load_state("p1-auth")
    assert state.status == "paused_operator_action"


def test_network_failure_can_retry_exact_request_without_p0_duplicate_block(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p1-network", "P1 Network Retry")

    calls = 0
    original_execute = orchestrator.executor.execute

    def network_once(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ExecuteResult(success=False, error="connection reset by peer")
        return original_execute(request)

    orchestrator.executor.execute = network_once

    first = orchestrator.research("p1-network", "same exact request")
    second = orchestrator.research("p1-network", "same exact request")

    assert first.success is False
    assert first.metadata["failure"]["category"] == "network"
    assert first.metadata["retry_policy"]["action"] == "retry"
    assert first.metadata["retry_policy"]["delay_seconds"] == 2
    assert second.success is True
    assert calls == 2
