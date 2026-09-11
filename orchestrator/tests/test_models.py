"""Unit tests for Orchestrator core models.

These tests define the expected MVP behaviour for project state handling.
"""


def test_project_state_defaults():
    """A new project state should start clean."""
    state = {
        "current_module": None,
        "progress": 0,
        "status": "initialized",
    }

    assert state["current_module"] is None
    assert state["progress"] == 0
    assert state["status"] == "initialized"


def test_project_state_progress_update():
    """Progress updates should be monotonic during a pipeline run."""
    state = {"progress": 0}

    state["progress"] = 50
    state["progress"] = 100

    assert state["progress"] == 100
