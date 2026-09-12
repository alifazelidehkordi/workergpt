"""Regression tests for P0 duplicate-execution safety controls."""

from orchestrator.core.models import ExecuteResult
from orchestrator.core.orchestrator import Orchestrator
from orchestrator.core.request_guard import RequestGuard


def test_duplicate_agent_call_is_blocked_before_executor(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p0-duplicate", "P0 Duplicate Guard")

    calls = 0
    original_execute = orchestrator.executor.execute

    def counting_execute(request):
        nonlocal calls
        calls += 1
        return original_execute(request)

    orchestrator.executor.execute = counting_execute

    first = orchestrator.research("p0-duplicate", "same question")
    second = orchestrator.research("p0-duplicate", "same question")
    third = orchestrator.research("p0-duplicate", "same question")

    assert first.success is True
    assert second.success is False
    assert third.success is False
    assert second.metadata["blocked_by"] == "p0_execution_gate"
    assert second.metadata["reason"] == "duplicate_request_detected"
    assert third.metadata["reason"] == "duplicate_request_detected"
    assert calls == 1


def test_changed_semantic_input_is_allowed(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p0-change", "P0 Changed Input")

    calls = 0
    original_execute = orchestrator.executor.execute

    def counting_execute(request):
        nonlocal calls
        calls += 1
        return original_execute(request)

    orchestrator.executor.execute = counting_execute

    first = orchestrator.research("p0-change", "question one")
    second = orchestrator.research("p0-change", "question two")

    assert first.success is True
    assert second.success is True
    assert calls == 2


def test_changed_file_content_is_not_treated_as_duplicate(tmp_path):
    cache = tmp_path / "request_cache.json"
    source = tmp_path / "input.txt"
    guard = RequestGuard(cache)
    context = {"task": "review"}

    source.write_text("version one", encoding="utf-8")
    first = guard.check("researcher", context, [source])
    assert first.allowed is True
    guard.record(first.fingerprint, "success")

    duplicate = guard.check("researcher", context, [source])
    assert duplicate.allowed is False

    source.write_text("version two", encoding="utf-8")
    changed = guard.check("researcher", context, [source])
    assert changed.allowed is True
    assert changed.fingerprint != first.fingerprint


def test_rate_limit_attempt_is_not_cached_as_duplicate(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p0-limit", "P0 Rate Limit Resume")

    calls = 0
    original_execute = orchestrator.executor.execute

    def limit_once(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ExecuteResult(
                success=False,
                error="rate limit",
                limit_hit=True,
            )
        return original_execute(request)

    orchestrator.executor.execute = limit_once

    first = orchestrator.research("p0-limit", "retry after limit")
    second = orchestrator.research("p0-limit", "retry after limit")

    assert first.success is False
    assert first.metadata["limit_hit"] is True
    assert second.success is True
    assert calls == 2
