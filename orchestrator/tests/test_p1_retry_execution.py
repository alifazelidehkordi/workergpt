"""Regression coverage for P1 bounded retry execution and budgets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from orchestrator.core.models import AgentOutput, ExecuteResult
from orchestrator.core.orchestrator import Orchestrator
from orchestrator.core.retry_execution import RetryExecutionController


class FakeOrchestrator:
    def __init__(self, root: Path, outputs: list[AgentOutput]) -> None:
        self.projects_dir = root / "projects"
        self.project_dir = self.projects_dir / "p1"
        self.project_dir.mkdir(parents=True)
        (self.project_dir / "project.toml").write_text(
            '[project]\nid = "p1"\nname = "P1"\n\n[settings]\nmax_retries = 3\n',
            encoding="utf-8",
        )
        self.outputs = list(outputs)
        self.calls = 0

    def run_agent(self, project_id, agent_name, context, files=None):
        self.calls += 1
        return self.outputs.pop(0)


def _failure(action: str = "retry", delay: int = 2, *, tokens: int = 0) -> AgentOutput:
    return AgentOutput(
        agent_name="researcher",
        success=False,
        content="connection reset by peer",
        metadata={
            "execution_time": 1.0,
            "total_tokens": tokens,
            "retry_policy": {
                "action": action,
                "allowed": action in {"retry", "backoff"},
                "reason": "transient_retry_allowed",
                "delay_seconds": delay,
            },
        },
    )


def _success(*, tokens: int = 0) -> AgentOutput:
    return AgentOutput(
        agent_name="researcher",
        success=True,
        content="done",
        metadata={"execution_time": 1.0, "total_tokens": tokens},
    )


def test_controller_retries_retry_action_and_resolves(tmp_path):
    fake = FakeOrchestrator(tmp_path, [_failure(), _success()])
    sleeps: list[float] = []
    controller = RetryExecutionController(fake, "p1", sleeper=sleeps.append)

    output = controller.execute("researcher", {"research_question": "q"})

    assert output.success is True
    assert fake.calls == 2
    assert sleeps == [2]
    assert output.metadata["retry_execution"]["attempts"] == 2
    assert output.metadata["retry_execution"]["status"] == "resolved"


def test_backoff_is_durable_and_does_not_hammer_executor(tmp_path):
    fake = FakeOrchestrator(tmp_path, [_failure("backoff", 60)])
    now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    controller = RetryExecutionController(
        fake,
        "p1",
        sleeper=lambda _seconds: None,
        now_fn=lambda: now,
    )

    first = controller.execute("researcher", {"research_question": "q"})
    second = controller.execute("researcher", {"research_question": "q"})

    assert first.success is False
    assert fake.calls == 1
    assert second.metadata["blocked_by"] == "p1_retry_execution"
    assert second.metadata["reason"] == "backoff_active"
    assert second.metadata["retry_budget"]["retry_after_seconds"] == 60
    assert fake.calls == 1


def test_retry_after_backoff_deadline_is_allowed(tmp_path):
    fake = FakeOrchestrator(tmp_path, [_failure("backoff", 60), _success()])
    clock = [datetime(2026, 9, 12, 12, 0, tzinfo=UTC)]
    controller = RetryExecutionController(
        fake,
        "p1",
        sleeper=lambda _seconds: None,
        now_fn=lambda: clock[0],
    )

    first = controller.execute("researcher", {"research_question": "q"})
    assert first.success is False
    clock[0] += timedelta(seconds=61)
    second = controller.execute("researcher", {"research_question": "q"})

    assert second.success is True
    assert fake.calls == 2


def test_attempt_budget_caps_retry_loop(tmp_path):
    fake = FakeOrchestrator(tmp_path, [_failure(), _failure(), _failure(), _failure()])
    (fake.project_dir / "project.toml").write_text(
        '[project]\nid = "p1"\nname = "P1"\n\n[settings]\nmax_retries = 2\n',
        encoding="utf-8",
    )
    controller = RetryExecutionController(fake, "p1", sleeper=lambda _seconds: None)

    output = controller.execute("researcher", {"research_question": "q"})

    assert fake.calls == 3
    assert output.success is False
    assert output.metadata["retry_execution"]["stop_reason"] == "retry_attempt_budget_exhausted"
    assert output.metadata["retry_execution"]["max_attempts"] == 3


def test_token_budget_stops_before_another_external_call(tmp_path):
    fake = FakeOrchestrator(tmp_path, [_failure(tokens=100), _success()])
    (fake.project_dir / "project.toml").write_text(
        '[project]\nid = "p1"\nname = "P1"\n\n[settings]\nmax_retries = 3\nmax_retry_tokens = 100\n',
        encoding="utf-8",
    )
    controller = RetryExecutionController(fake, "p1", sleeper=lambda _seconds: None)

    output = controller.execute("researcher", {"research_question": "q"})

    assert fake.calls == 1
    assert output.metadata["retry_execution"]["stop_reason"] == "retry_token_budget_exhausted"
    assert output.metadata["retry_execution"]["total_tokens"] == 100


def test_real_orchestrator_controlled_retry_recovers_from_network_failure(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p1-real", "P1 Controlled Retry")
    original_execute = orchestrator.executor.execute
    calls = 0

    def network_once(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ExecuteResult(success=False, error="connection reset by peer")
        return original_execute(request)

    orchestrator.executor.execute = network_once
    output = orchestrator.run_agent_with_retries(
        "p1-real",
        "researcher",
        {"research_question": "bounded retry"},
        sleeper=lambda _seconds: None,
    )

    assert output.success is True
    assert calls == 2
    assert output.metadata["retry_execution"]["attempts"] == 2


def test_real_orchestrator_stops_at_retry_budget(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    project = orchestrator.create_project("p1-budget", "P1 Budget")
    (project / "project.toml").write_text(
        '[project]\nid = "p1-budget"\nname = "P1 Budget"\n\n[settings]\nmax_retries = 2\n',
        encoding="utf-8",
    )
    calls = 0

    def always_network(_request):
        nonlocal calls
        calls += 1
        return ExecuteResult(success=False, error="connection reset by peer")

    orchestrator.executor.execute = always_network
    output = orchestrator.run_agent_with_retries(
        "p1-budget",
        "researcher",
        {"research_question": "never succeeds"},
        sleeper=lambda _seconds: None,
    )

    assert calls == 3
    assert output.success is False
    assert output.metadata["retry_execution"]["stop_reason"] == "retry_attempt_budget_exhausted"
