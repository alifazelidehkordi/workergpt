"""P2 regression tests for execution/task outcome separation."""

from orchestrator.core.evaluation import EvaluationStatus, TaskEvaluator
from orchestrator.core.models import AgentOutput


def _output(content: str = "answer", *, success: bool = True) -> AgentOutput:
    return AgentOutput(agent_name="researcher", success=success, content=content)


def test_successful_execution_without_contract_is_not_assumed_task_success():
    evaluation = TaskEvaluator().evaluate(
        _output("plausible answer"),
        {},
        execution_success=True,
    )

    assert evaluation.status == EvaluationStatus.UNASSESSED
    assert evaluation.task_success is None
    assert evaluation.reason == "no_evaluation_contract"


def test_execution_failure_is_distinct_from_task_evaluation():
    evaluation = TaskEvaluator().evaluate(
        _output("", success=False),
        {},
        execution_success=False,
    )

    assert evaluation.status == EvaluationStatus.FAILED
    assert evaluation.task_success is False
    assert evaluation.reason == "execution_failed"


def test_agent_processing_failure_cannot_be_task_success():
    evaluation = TaskEvaluator().evaluate(
        _output("invalid parser output", success=False),
        {},
        execution_success=True,
    )

    assert evaluation.status == EvaluationStatus.FAILED
    assert evaluation.task_success is False
    assert evaluation.reason == "agent_output_failed"


def test_explicit_text_contract_passes_only_when_all_checks_pass():
    evaluation = TaskEvaluator().evaluate(
        _output("Evidence from SOURCE A is complete."),
        {
            "_evaluation_contract": {
                "min_chars": 20,
                "required_terms": ["source a", "evidence"],
                "forbidden_terms": ["something went wrong"],
            }
        },
        execution_success=True,
    )

    assert evaluation.status == EvaluationStatus.PASSED
    assert evaluation.task_success is True
    assert all(check["passed"] for check in evaluation.checks)


def test_contract_failure_is_reported_without_relabeling_execution():
    evaluation = TaskEvaluator().evaluate(
        _output("short answer"),
        {
            "_evaluation_contract": {
                "min_chars": 50,
                "required_terms": ["citation"],
            }
        },
        execution_success=True,
    )

    assert evaluation.status == EvaluationStatus.FAILED
    assert evaluation.task_success is False
    assert evaluation.reason == "evaluation_contract_failed"
    assert any(not check["passed"] for check in evaluation.checks)


def test_json_contract_validates_format_and_required_keys():
    evaluator = TaskEvaluator()
    passed = evaluator.evaluate(
        _output('{"answer": "ok", "sources": ["a"]}'),
        {
            "_evaluation_contract": {
                "expected_format": "json",
                "required_json_keys": ["answer", "sources"],
            }
        },
        execution_success=True,
    )
    failed = evaluator.evaluate(
        _output('{"answer": "ok"}'),
        {
            "_evaluation_contract": {
                "expected_format": "json",
                "required_json_keys": ["answer", "sources"],
            }
        },
        execution_success=True,
    )

    assert passed.status == EvaluationStatus.PASSED
    assert passed.task_success is True
    assert failed.status == EvaluationStatus.FAILED
    assert failed.task_success is False


def test_invalid_contract_is_unassessed_not_false_success():
    evaluation = TaskEvaluator().evaluate(
        _output("answer"),
        {"_evaluation_contract": {"min_chars": -1}},
        execution_success=True,
    )

    assert evaluation.status == EvaluationStatus.UNASSESSED
    assert evaluation.task_success is None
    assert evaluation.reason == "invalid_evaluation_contract"
