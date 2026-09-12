from pathlib import Path

from orchestrator.core.action_decision_gate import ActionDecisionGate


def test_same_action_same_state_is_blocked(tmp_path: Path):
    gate = ActionDecisionGate(tmp_path / "action_state.json", max_same_state_attempts=2)
    context = {
        "current_phase": "phase_1",
        "current_module": "module_a",
        "goal": "complete task",
    }

    first = gate.check("send_file", context)
    assert first.allowed
    gate.record(first, "executed")

    second = gate.check("send_file", context)
    assert second.allowed
    gate.record(second, "executed")

    third = gate.check("send_file", context)
    assert not third.allowed
    assert third.reason == "same_action_same_state_threshold_reached"
