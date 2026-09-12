"""Regression tests for P0 duplicate and stagnation safety controls."""

from orchestrator.core.models import AgentOutput, ExecuteResult
from orchestrator.core.orchestrator import Orchestrator
from orchestrator.core.progress_guard import PersistentProgressGuard
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


def _repair_context(attempt: int) -> dict:
    return {
        "topic_id": "KSR-TEST",
        "topic_title": "Test topic",
        "section_title": "## Test section",
        "repair_plan": f"retry strategy wording {attempt}",
        "repair_context": "The same bounded paragraph.",
        "review_feedback": [{"id": "issue-1", "problem": "still unresolved"}],
    }


def test_changed_requests_stop_when_outputs_make_no_progress(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p0-stagnant", "P0 Stagnation")

    calls = 0

    def stagnant_execute(request):
        nonlocal calls
        calls += 1
        return ExecuteResult(
            success=True,
            text_response=(
                '{"patches":[{"issue_id":"issue-1","old_text":"same claim",'
                '"new_text":"same replacement"}]}'
            ),
        )

    orchestrator.executor.execute = stagnant_execute

    results = [
        orchestrator.run_agent(
            "p0-stagnant",
            "research_section_repair",
            _repair_context(attempt),
        )
        for attempt in range(1, 5)
    ]

    assert [item.success for item in results] == [True, True, True, False]
    assert results[-1].metadata["blocked_by"] == "p0_stagnation_guard"
    assert results[-1].metadata["reason"] == "no_measurable_progress"
    assert results[-1].metadata["stagnant_count"] == 3
    assert calls == 4

    state = orchestrator.load_state("p0-stagnant")
    assert state.status == "paused_no_progress"
    assert state.progress["p0_stop"]["reason"] == "no_measurable_progress"

    # A later wording change in the same logical section must be blocked before
    # another external request is sent.
    fifth = orchestrator.run_agent(
        "p0-stagnant",
        "research_section_repair",
        _repair_context(5),
    )
    assert fifth.success is False
    assert fifth.metadata["blocked_by"] == "p0_stagnation_guard"
    assert calls == 4

    # The stop is stored on disk, so restarting the orchestrator cannot reset it.
    restarted = Orchestrator(tmp_path, use_mock_executor=True)
    restarted.executor.execute = lambda request: (_ for _ in ()).throw(
        AssertionError("stagnant action reached executor after restart")
    )
    after_restart = restarted.run_agent(
        "p0-stagnant",
        "research_section_repair",
        _repair_context(6),
    )
    assert after_restart.success is False
    assert after_restart.metadata["reason"] == "no_measurable_progress"


def test_substantive_artifact_changes_do_not_trigger_stagnation(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p0-progress", "P0 Real Progress")

    responses = iter(
        [
            "alpha bravo charlie delta evidence",
            "kappa lambda muon neutron theory",
            "saffron violet amber cobalt mechanism",
            "forest glacier canyon desert practice",
        ]
    )
    calls = 0

    def changing_execute(request):
        nonlocal calls
        calls += 1
        return ExecuteResult(success=True, text_response=next(responses))

    orchestrator.executor.execute = changing_execute

    results = [
        orchestrator.run_agent(
            "p0-progress",
            "research_section_repair",
            _repair_context(attempt),
        )
        for attempt in range(1, 5)
    ]

    assert all(item.success for item in results)
    assert calls == 4
    assert orchestrator.load_state("p0-progress").status == "in_progress"


def test_resolved_issues_reset_stagnation_counter(tmp_path):
    guard = PersistentProgressGuard(tmp_path / "progress.json")
    output = AgentOutput(agent_name="repair", success=True, content="same artifact text")
    base_context = {
        "project_id": "p",
        "topic_id": "T1",
        "section_title": "S1",
    }

    first = guard.record_result(
        "repair",
        {
            **base_context,
            "review_feedback": [{"id": "a"}, {"id": "b"}],
        },
        output,
    )
    second = guard.record_result(
        "repair",
        {
            **base_context,
            "review_feedback": [{"id": "a"}],
        },
        output,
    )

    assert first.should_stop is False
    assert second.issues_improved is True
    assert second.measurable_progress is True
    assert second.stagnant_count == 0


def test_quality_score_improvement_counts_as_progress(tmp_path):
    guard = PersistentProgressGuard(tmp_path / "progress.json")
    context = {
        "project_id": "p",
        "topic_id": "T1",
        "section_title": "S1",
    }

    guard.record_result(
        "critic",
        context,
        AgentOutput(agent_name="critic", success=True, content="same", confidence=0.50),
    )
    improved = guard.record_result(
        "critic",
        context,
        AgentOutput(agent_name="critic", success=True, content="same", confidence=0.65),
    )

    assert improved.score_improved is True
    assert improved.measurable_progress is True
    assert improved.stagnant_count == 0
