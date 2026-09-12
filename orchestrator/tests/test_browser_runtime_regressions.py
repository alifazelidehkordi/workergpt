"""Focused regressions for the Patchright ChatGPT browser runtime."""

from pathlib import Path

import pytest

import orchestrator.tools.browser_runtime as runtime
from orchestrator.agents.base import BaseAgent
from orchestrator.core.models import ExecuteRequest
from orchestrator.tools.chatgpt_web import RealChatGPTWebExecutor
from orchestrator.tools.response_state import ResponseObservation, ResponseState, ResponseStateMachine


def _options(tmp_path: Path, timeout_seconds: int = 300) -> runtime.BrowserOptions:
    return runtime.BrowserOptions(
        profile_dir=tmp_path / "profile",
        download_dir=tmp_path / "downloads",
        timeout_seconds=timeout_seconds,
    )


def test_rate_limit_detection_removes_the_submitted_prompt():
    prompt = "Explain what a rate limit is and when to try again later."

    assert runtime._contains_rate_limit(prompt, prompt) is False
    assert runtime._contains_rate_limit(prompt + "\nYou've reached the limit.", prompt) is True


def test_response_state_machine_requires_unchanged_quiet_window():
    machine = ResponseStateMachine(required_assistant_count=1, stable_seconds=3.0)
    assert machine.observe(ResponseObservation(1, True, "draft", now=0.0)) is ResponseState.GENERATING
    assert machine.observe(ResponseObservation(1, False, "final", now=1.0)) is ResponseState.WAITING
    assert machine.observe(ResponseObservation(1, False, "final", now=3.9)) is ResponseState.WAITING
    assert machine.observe(ResponseObservation(1, False, "final", now=6.8)) is ResponseState.WAITING
    assert machine.observe(ResponseObservation(1, False, "final", now=7.1)) is ResponseState.STABLE


def test_browser_health_reports_closed_session(tmp_path):
    session = runtime.PatchrightChatGPTSession(_options(tmp_path))
    assert session.state == "created"
    health = session.health()
    assert health["page_open"] is False
    assert health["response_state"] == "waiting"


def test_browser_is_alive_requires_open_page(tmp_path):
    session = runtime.PatchrightChatGPTSession(_options(tmp_path))
    assert session.is_alive() is False


@pytest.mark.parametrize(
    "message",
    [
        "Something went wrong while generating the response. Retry",
        "Something went wrong. If this issue persists please contact our help center.",
        "Hmm...something seems to have gone wrong.",
        "There was an error generating a response",
        "An error occurred while generating the response",
        "Error in message stream",
        "Failed to generate a response",
        "Response generation failed",
        "Network error. Check your connection and try again.",
        "Unable to load conversation",
    ],
)
def test_common_chatgpt_generation_errors_are_detected(message):
    assert runtime._contains_provider_error(message) is True


def test_provider_error_detection_removes_the_submitted_prompt():
    prompt = "Explain the message: Something went wrong while generating the response."

    assert runtime._contains_provider_error(prompt, prompt) is False


@pytest.mark.parametrize("flag", ["_web_search", "web_search"])
def test_base_agent_propagates_web_search_aliases(flag):
    request = _TestAgent().create_request({flag: True})

    assert request.metadata["web_search"] is True


def test_rendered_markdown_code_block_keeps_raw_fence_and_whitespace():
    raw = "# Title\n\n- first\n  - nested\n"

    restored = runtime._restore_fenced_code_blocks(
        f"markdown\nCopy code\n{raw}",
        [{"language": "markdown", "text": raw}],
    )

    assert restored == f"```markdown\n{raw.rstrip()}\n```"


def test_rendered_json_code_block_keeps_raw_fence():
    raw = '{\n  "valid": true,\n  "items": []\n}\n'

    restored = runtime._restore_fenced_code_blocks(
        f"json\nCopy code\n{raw}",
        [{"language": "json", "text": raw}],
    )

    assert restored == f"```json\n{raw.rstrip()}\n```"


def test_send_message_allows_the_composer_to_render_late(tmp_path, monkeypatch):
    session = runtime.PatchrightChatGPTSession(_options(tmp_path))
    editor = _Editor()
    session._page = _Page(_Message(lambda: ""), assistant_count=0)
    observed_timeout = None

    def delayed_editor(page, selectors, timeout_seconds):
        nonlocal observed_timeout
        observed_timeout = timeout_seconds
        return editor

    monkeypatch.setattr(runtime, "_wait_for_visible", delayed_editor)
    monkeypatch.setattr(runtime, "_visible", lambda page, selectors: None)

    assert session.send_message("hello") == 0
    assert observed_timeout == 60.0
    assert editor.filled == "hello"
    assert editor.pressed == "Enter"


def test_web_search_control_is_clicked_and_observed(tmp_path, monkeypatch):
    session = runtime.PatchrightChatGPTSession(_options(tmp_path))
    session._page = _Page(_Message(lambda: ""), assistant_count=0)
    control = _ToggleControl()
    monkeypatch.setattr(runtime, "_visible", lambda page, selectors: control)

    assert session.enable_web_search() is True
    assert control.clicked is True


def test_missing_web_search_control_is_nonfatal(tmp_path, monkeypatch):
    session = runtime.PatchrightChatGPTSession(_options(tmp_path))
    session._page = _Page(_Message(lambda: ""), assistant_count=0)
    monkeypatch.setattr(runtime, "_visible", lambda page, selectors: None)

    assert session.enable_web_search() is False


def test_stable_draft_is_not_returned_during_a_short_generation_gap(tmp_path, monkeypatch):
    clock = _Clock()
    message = _Message(lambda: "draft" if clock.now < 2.0 else "final answer")
    session = runtime.PatchrightChatGPTSession(_options(tmp_path, timeout_seconds=10))
    session._page = _Page(message, assistant_count=1)

    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    # A stop control can briefly disappear while ChatGPT changes generation phases.
    monkeypatch.setattr(runtime, "_visible", lambda page, selectors: None)

    assert session.wait_for_response(0, 10) == "final answer"
    assert clock.now >= 5.0


def test_generation_resuming_restarts_the_completion_stability_window(tmp_path, monkeypatch):
    clock = _Clock()
    message = _Message(lambda: "complete-looking text")
    session = runtime.PatchrightChatGPTSession(_options(tmp_path, timeout_seconds=12))
    session._page = _Page(message, assistant_count=1)

    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        runtime,
        "_visible",
        lambda page, selectors: object() if 1.0 <= clock.now < 3.0 else None,
    )

    assert session.wait_for_response(0, 12) == "complete-looking text"
    assert clock.now >= 6.0


def test_hard_deadline_discards_an_incomplete_response(tmp_path, monkeypatch):
    clock = _Clock()
    message = _Message(lambda: f"partial at {clock.now}")
    session = runtime.PatchrightChatGPTSession(_options(tmp_path, timeout_seconds=2))
    session._page = _Page(message, assistant_count=1)

    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    monkeypatch.setattr(runtime, "_visible", lambda page, selectors: object())

    with pytest.raises(runtime.ResponseIncompleteTimeoutError, match="partial output was discarded"):
        session.wait_for_response(0, 2)


def test_assistant_generation_error_is_raised_instead_of_returned(tmp_path, monkeypatch):
    clock = _Clock()
    error_text = "Something went wrong while generating the response. Retry"
    session = runtime.PatchrightChatGPTSession(_options(tmp_path, timeout_seconds=5))
    session._page = _Page(_Message(lambda: error_text), assistant_count=1)

    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    monkeypatch.setattr(runtime, "_visible", lambda page, selectors: None)

    with pytest.raises(runtime.ProviderResponseError, match="Something went wrong"):
        session.wait_for_response(0, 5)


def test_real_executor_marks_provider_generation_error_as_failure(tmp_path, monkeypatch):
    executor = RealChatGPTWebExecutor(
        profile_dir=tmp_path / "profile",
        download_dir=tmp_path / "downloads",
        headless=True,
    )
    monkeypatch.setattr(executor, "_session_for", lambda timeout: _ProviderErrorSession())

    result = executor.execute(ExecuteRequest(prompt="hello", timeout_seconds=5))

    assert result.success is False
    assert result.text_response is None
    assert result.metadata["error_type"] == "ProviderResponseError"
    assert "Something went wrong" in result.error


@pytest.mark.parametrize("observed", [True, False])
def test_real_executor_records_web_search_observability(tmp_path, monkeypatch, observed):
    executor = RealChatGPTWebExecutor(
        profile_dir=tmp_path / "profile",
        download_dir=tmp_path / "downloads",
        headless=True,
    )
    session = _SuccessfulSession(search_observed=observed)
    monkeypatch.setattr(executor, "_session_for", lambda timeout: session)

    result = executor.execute(
        ExecuteRequest(prompt="research this", timeout_seconds=5, metadata={"_web_search": True})
    )

    assert result.success is True
    assert result.metadata["search_requested"] is True
    assert result.metadata["search_observed"] is observed
    assert session.events == ["start", "search", "send", "wait"]


def test_incomplete_assistant_rate_limit_text_cannot_bypass_completion_gate(tmp_path, monkeypatch):
    clock = _Clock()
    message = _Message(lambda: "You've reached the limit, partial details follow...")
    session = runtime.PatchrightChatGPTSession(_options(tmp_path, timeout_seconds=2))
    session._page = _Page(message, assistant_count=1)

    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    monkeypatch.setattr(runtime, "_visible", lambda page, selectors: object())

    with pytest.raises(runtime.ResponseIncompleteTimeoutError):
        session.wait_for_response(0, 2)


def test_profile_lock_rejects_a_second_live_owner(tmp_path):
    first = runtime.PatchrightChatGPTSession(_options(tmp_path))
    second = runtime.PatchrightChatGPTSession(_options(tmp_path))
    first.options.profile_dir.mkdir(parents=True)

    first._acquire_profile_lock()
    try:
        with pytest.raises(runtime.ProfileInUseError, match="already in use"):
            second._acquire_profile_lock()
    finally:
        first._release_profile_lock()

    second._acquire_profile_lock()
    second._release_profile_lock()


def test_profile_lock_recovers_stale_owner_metadata(tmp_path):
    session = runtime.PatchrightChatGPTSession(_options(tmp_path))
    session.options.profile_dir.mkdir(parents=True)
    lock_path = session.options.profile_dir / ".orchestrator-browser.lock"
    lock_path.write_text("pid=999999999\n", encoding="utf-8")

    session._acquire_profile_lock()
    try:
        assert lock_path.read_text(encoding="utf-8") == f"pid={runtime.os.getpid()}\n"
    finally:
        session._release_profile_lock()


class _Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds


class _Message:
    def __init__(self, text):
        self._text = text

    def evaluate(self, script):
        return {"rendered": self._text(), "blocks": []}

    def inner_text(self, timeout=None):
        return self._text()


class _LocatorList:
    def __init__(self, message=None, count=0):
        self.message = message
        self._count = count

    def count(self):
        return self._count

    def nth(self, index):
        assert self.message is not None
        return self.message


class _Keyboard:
    def insert_text(self, text):
        self.text = text


class _Page:
    def __init__(self, message, assistant_count):
        self.message = message
        self.assistant_count = assistant_count
        self.keyboard = _Keyboard()

    def locator(self, selector):
        if selector == runtime.ASSISTANT_MESSAGE_SELECTOR:
            return _LocatorList(self.message, self.assistant_count)
        return _LocatorList(count=0)


class _Editor:
    def __init__(self):
        self.filled = None
        self.pressed = None

    def fill(self, text):
        self.filled = text

    def press(self, key):
        self.pressed = key


class _ToggleControl:
    def __init__(self):
        self.clicked = False

    def get_attribute(self, name):
        if name == "aria-pressed":
            return "true" if self.clicked else "false"
        return None

    def evaluate(self, script):
        self.clicked = True


class _ProviderErrorSession:
    def start_new_chat(self):
        pass

    def upload(self, path):
        pass

    def send_message(self, prompt):
        return 0

    def wait_for_response(self, before_count, timeout_seconds):
        raise runtime.ProviderResponseError(
            "Something went wrong while generating the response. Retry"
        )


class _SuccessfulSession:
    def __init__(self, search_observed):
        self.search_observed = search_observed
        self.events = []

    def start_new_chat(self):
        self.events.append("start")

    def upload(self, path):
        pass

    def enable_web_search(self):
        self.events.append("search")
        return self.search_observed

    def send_message(self, prompt):
        self.events.append("send")
        return 0

    def wait_for_response(self, before_count, timeout_seconds):
        self.events.append("wait")
        return "answer"


class _TestAgent(BaseAgent):
    def build_prompt(self, context):
        return "prompt"
