"""Mock pipeline tests for Researcher -> Critic -> Synthesizer flow."""


def run_mock_pipeline(research_ok=True, critic_ok=True, synth_ok=True):
    executed = []

    if not research_ok:
        return executed
    executed.append("researcher")

    if not critic_ok:
        return executed
    executed.append("critic")

    if not synth_ok:
        return executed
    executed.append("synthesizer")

    executed.append("checkpoint")
    return executed


def test_full_mock_pipeline_execution():
    result = run_mock_pipeline()

    assert result == [
        "researcher",
        "critic",
        "synthesizer",
        "checkpoint",
    ]


def test_research_failure_stops_pipeline():
    result = run_mock_pipeline(research_ok=False)

    assert result == []
    assert "critic" not in result
    assert "synthesizer" not in result


def test_critic_failure_stops_synthesis():
    result = run_mock_pipeline(critic_ok=False)

    assert result == ["researcher"]
    assert "synthesizer" not in result
