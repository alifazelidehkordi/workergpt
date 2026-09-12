"""Regression tests for P1 failure classification and retry memory."""

from orchestrator.core.failure_memory import FailureClassifier, FailureMemory
from orchestrator.core.models import ExecuteResult
from orchestrator.core.orchestrator import Orchestrator


def test_classifier_separates_transient_and_deterministic_failures():
    classifier = FailureClassifier()

    rate_limit = classifier.classify("You've reached the usage limit", limit_hit=True)
    validation = classifier.classify("Invalid JSON schema: missing required field")
    auth = classifier.classify("Session expired; login required")

    assert rate_limit.category == "rate_limit"
    assert rate_limit.retryable is True
    assert rate_limit.transient is True
    assert rate_limit.strategy_change_on_repeat is False

    assert validation.category == "validation"
    assert validation.retryable is True
    assert validation.transient is False
    assert validation.strategy_change_on_repeat is True

    assert auth.category == "authentication"
    assert auth.retryable is False


def test_failure_signature_normalizes_dynamic_numbers_and_paths():
    classifier = FailureClassifier()

    first = classifier.classify("Invalid schema on attempt 2 at /tmp/run-123/output.json")
    second = classifier.classify("Invalid schema on attempt 9 at /tmp/run-999/output.json")

    assert first.category == "validation"
    assert second.category == "validation"
    assert first.signature == second.signature


def test_same_failure_twice_requires_strategy_change(tmp_path):
    memory = FailureMemory(tmp_path / "failure_memory.json", same_strategy_threshold=2)
    context = {
        "project_id": "p1",
        "topic_id": "KSR-1",
        "section_id": "01_construct",
    }

    assert memory.check_retry("research_section", context).allowed is True

    memory.record_failure(
        "research_section",
        context,
        "Invalid JSON schema: missing required field",
    )
    after_one = memory.check_retry("research_section", context)
    assert after_one.allowed is True
    assert after_one.occurrence == 1

    memory.record_failure(
        "research_section",
        context,
        "Invalid JSON schema: missing required field",
    )
    blocked = memory.check_retry("research_section", context)
    assert blocked.allowed is False
    assert blocked.reason == "strategy_change_required"
    assert blocked.occurrence == 2
    assert blocked.strategy_change_required is True

    changed = memory.check_retry(
        "research_section",
        {**context, "_strategy_id": "source-first-v2"},
    )
    assert changed.allowed is True
    assert changed.reason == "strategy_changed"


def test_transient_failure_does_not_require_strategy_change(tmp_path):
    memory = FailureMemory(tmp_path / "failure_memory.json", same_strategy_threshold=2)
    context = {"project_id": "p1", "research_question": "q"}

    for _ in range(4):
        memory.record_failure(
            "researcher",
            context,
            "ChatGPT usage/rate limit",
            limit_hit=True,
        )

    decision = memory.check_retry("researcher", context)
    assert decision.allowed is True
    assert decision.reason == "transient_retry_allowed"
    assert decision.occurrence == 4


def test_success_resolves_active_failure(tmp_path):
    memory = FailureMemory(tmp_path / "failure_memory.json", same_strategy_threshold=2)
    context = {"project_id": "p1", "research_question": "q"}

    memory.record_failure("researcher", context, "Invalid schema")
    memory.record_failure("researcher", context, "Invalid schema")
    assert memory.check_retry("researcher", context).allowed is False

    memory.resolve("researcher", context)
    decision = memory.check_retry("researcher", context)
    assert decision.allowed is True
    assert decision.reason == "no_active_failure"


def test_orchestrator_allows_one_exact_failure_retry_then_requires_strategy_change(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p1-gate", "P1 Failure Gate")

    calls = 0

    def invalid_schema(_request):
        nonlocal calls
        calls += 1
        return ExecuteResult(
            success=False,
            error="Invalid JSON schema: missing required field 7",
            metadata={"provider": "test"},
        )

    orchestrator.executor.execute = invalid_schema

    context = {"research_question": "same bounded question"}
    first = orchestrator.run_agent("p1-gate", "researcher", context)
    second = orchestrator.run_agent("p1-gate", "researcher", context)
    third = orchestrator.run_agent("p1-gate", "researcher", context)

    assert first.success is False
    assert second.success is False
    assert first.metadata["failure"]["category"] == "validation"
    assert first.metadata["retry_policy"]["action"] == "retry"
    assert second.metadata["failure"]["same_strategy_count"] == 2
    assert second.metadata["retry_policy"]["action"] == "change_strategy"
    assert third.success is False
    assert third.metadata["blocked_by"] == "p1_retry_policy"
    assert third.metadata["reason"] == "strategy_change_required"
    assert third.metadata["retry_policy"]["action"] == "change_strategy"
    assert calls == 2

    changed_strategy = orchestrator.run_agent(
        "p1-gate",
        "researcher",
        {**context, "_strategy_id": "schema-repair-v2"},
    )
    assert "blocked_by" not in changed_strategy.metadata
    assert changed_strategy.metadata["failure"]["strategy_id"] == "schema-repair-v2"
    assert calls == 3


def test_rate_limit_resume_remains_allowed_with_failure_memory(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p1-limit", "P1 Limit")

    calls = 0
    original_execute = orchestrator.executor.execute

    def limit_once(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ExecuteResult(success=False, error="rate limit", limit_hit=True)
        return original_execute(request)

    orchestrator.executor.execute = limit_once

    first = orchestrator.research("p1-limit", "retry after transient failure")
    second = orchestrator.research("p1-limit", "retry after transient failure")

    assert first.success is False
    assert first.metadata["failure"]["category"] == "rate_limit"
    assert first.metadata["retry_policy"]["action"] == "backoff"
    assert first.metadata["retry_policy"]["delay_seconds"] == 60
    assert second.success is True
    assert calls == 2
