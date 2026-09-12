"""P2 regression tests for execution/task outcome separation."""

from orchestrator.core.evaluation import EvaluationStatus, TaskEvaluator
from orchestrator.core.models import AgentOutput, ExecuteResult
from orchestrator.core.orchestrator import Orchestrator


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


def test_orchestrator_records_execution_and_task_outcomes_separately(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p2-separation", "P2 Separation")
    orchestrator.executor.execute = lambda _request: ExecuteResult(
        success=True,
        text_response="A plausible research answer without an explicit evaluator contract.",
    )

    output = orchestrator.run_agent(
        "p2-separation",
        "researcher",
        {"research_question": "What is the answer?"},
    )

    assert output.success is True
    assert output.metadata["execution_success"] is True
    assert output.metadata["agent_output_success"] is True
    assert output.metadata["task_evaluation"]["status"] == "unassessed"
    assert output.metadata["task_evaluation"]["task_success"] is None
    assert output.metadata["task_evaluation"]["reason"] == "no_evaluation_contract"


def test_failed_task_contract_does_not_masquerade_as_execution_failure(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p2-contract", "P2 Contract")
    orchestrator.executor.execute = lambda _request: ExecuteResult(
        success=True,
        text_response="This transport call completed successfully.",
    )

    output = orchestrator.run_agent(
        "p2-contract",
        "researcher",
        {
            "research_question": "Return an answer with a citation.",
            "_evaluation_contract": {"required_terms": ["citation"]},
        },
    )

    # P2.1 records the distinction but intentionally does not gate the workflow yet.
    assert output.success is True
    assert output.metadata["execution_success"] is True
    assert output.metadata["agent_output_success"] is True
    assert output.metadata["task_evaluation"]["status"] == "failed"
    assert output.metadata["task_evaluation"]["task_success"] is False
    assert output.metadata["task_evaluation"]["reason"] == "evaluation_contract_failed"


def test_executor_failure_records_failed_execution_and_task_outcome(tmp_path):
    orchestrator = Orchestrator(tmp_path, use_mock_executor=True)
    orchestrator.create_project("p2-exec-fail", "P2 Execution Failure")
    orchestrator.executor.execute = lambda _request: ExecuteResult(
        success=False,
        error="connection reset by peer",
    )

    output = orchestrator.run_agent(
        "p2-exec-fail",
        "researcher",
        {"research_question": "question"},
    )

    assert output.success is False
    assert output.metadata["execution_success"] is False
    assert output.metadata["agent_output_success"] is False
    assert output.metadata["task_evaluation"]["status"] == "failed"
    assert output.metadata["task_evaluation"]["task_success"] is False
    assert output.metadata["task_evaluation"]["reason"] == "execution_failed"
