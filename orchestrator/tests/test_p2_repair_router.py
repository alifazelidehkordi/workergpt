from orchestrator.core.repair_router import (
    EvaluationRepairRouter,
    RepairAction,
)


def test_source_failure_routes_to_research_repair():
    decision = EvaluationRepairRouter().route(
        {"issues": ["missing citations"]}
    )
    assert decision.action == RepairAction.REPAIR
    assert decision.target_agent == "research_section_repair"


def test_format_failure_routes_to_repair_agent():
    decision = EvaluationRepairRouter().route(
        {"issues": ["invalid json format"]}
    )
    assert decision.target_agent == "repair_agent"


def test_missing_evaluation_stops():
    decision = EvaluationRepairRouter().route({})
    assert decision.action == RepairAction.STOP
