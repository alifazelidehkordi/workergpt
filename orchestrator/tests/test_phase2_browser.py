"""Phase 2 browser/executor regression tests."""

from orchestrator.core.models import ExecuteRequest
from orchestrator.tools.browser_runtime import BrowserOptions
from orchestrator.tools.chatgpt_web import ChatGPTWebExecutor


def test_profile_defaults_are_isolated_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    alpha = BrowserOptions.defaults(profile="alpha")
    beta = BrowserOptions.defaults(profile="beta")
    assert alpha.profile_dir != beta.profile_dir
    assert alpha.profile_dir.name == "alpha"
    assert beta.profile_dir.name == "beta"


def test_mock_executor_is_explicit():
    result = ChatGPTWebExecutor(use_mock=True).execute(ExecuteRequest(prompt="hello"))
    assert result.success is True
    assert result.metadata["mock"] is True


def test_real_executor_never_silently_falls_back_to_mock(tmp_path):
    executor = ChatGPTWebExecutor(
        use_mock=False,
        profile_dir=tmp_path / "profile",
        download_dir=tmp_path / "downloads",
        headless=True,
    )
    result = executor.execute(ExecuteRequest(prompt="hello", timeout_seconds=1))
    assert result.metadata["mock"] is False
    if not result.success:
        assert result.error
