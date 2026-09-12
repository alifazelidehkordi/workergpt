"""P3 decision flow contract tests.

These tests document the expected runtime contract between decision, routing,
and workflow execution layers.
"""

from orchestrator.core.decision_hooks import DecisionHook


def test_decision_hook_continue_contract():
    result = DecisionHook().apply(
        {"action": "continue", "agent": None, "reason": "evaluation_passed"}
    )

    assert result.should_continue is True
    assert result.action == "continue"


def test_decision_hook_repair_contract():
    result = DecisionHook().apply(
        {
            "action": "repair",
            "agent": "research_section_repair",
            "reason": "evaluation_failed",
        }
    )

    assert result.should_continue is False
    assert result.route == "research_section_repair"
