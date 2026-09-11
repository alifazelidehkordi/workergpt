"""ChatGPT Web executor with explicit mock and Patchright-backed real modes."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from orchestrator.core.models import ExecuteRequest, ExecuteResult
from orchestrator.tools.browser_runtime import BrowserOptions, PatchrightChatGPTSession


class MockChatGPTWebExecutor:
    def execute(self, request: ExecuteRequest) -> ExecuteResult:
        start = time.monotonic()
        agent = request.metadata.get("agent", "unknown")
        preview = request.prompt[:140].replace("\n", " ")
        return ExecuteResult(
            success=True,
            text_response=(
                "[Mock Response – ChatGPT Web Executor]\n\n"
                f"Agent: {agent}\nPrompt preview: {preview}...\n\n"
                "This is a simulated response.\n"
            ),
            execution_time=time.monotonic() - start,
            metadata={"mock": True, "agent": agent},
        )


class RealChatGPTWebExecutor:
    """Execute prompts against ChatGPT web using a persistent Patchright profile."""

    def __init__(
        self,
        *,
        profile: str = "default",
        profile_dir: Path | None = None,
        download_dir: Path | None = None,
        headless: bool = False,
    ) -> None:
        self.profile = profile
        self.profile_dir = profile_dir
        self.download_dir = download_dir
        self.headless = headless
        self._session: PatchrightChatGPTSession | None = None

    def _session_for(self, timeout_seconds: int) -> PatchrightChatGPTSession:
        if self._session is None:
            options = BrowserOptions.defaults(
                profile=self.profile,
                profile_dir=self.profile_dir,
                download_dir=self.download_dir,
                headless=self.headless,
                timeout_seconds=timeout_seconds,
            )
            self._session = PatchrightChatGPTSession(options)
            self._session.open()
            self._session.assert_authenticated()
        return self._session

    @staticmethod
    def _is_limit_text(text: str) -> bool:
        lowered = text.lower()
        return text.startswith("__ORCHESTRATOR_RATE_LIMIT__") or any(
            phrase in lowered
            for phrase in (
                "you've reached the limit",
                "you have reached the limit",
                "rate limit",
                "usage limit",
                "too many requests",
                "try again later",
            )
        )

    def execute(self, request: ExecuteRequest) -> ExecuteResult:
        start = time.monotonic()
        try:
            session = self._session_for(request.timeout_seconds)
            session.start_new_chat()
            for file_path in request.files:
                session.upload(file_path)
            before_count = session.send_message(request.prompt)
            text = session.wait_for_response(before_count, request.timeout_seconds)
            limit_hit = self._is_limit_text(text)
            if text.startswith("__ORCHESTRATOR_RATE_LIMIT__"):
                text = text.split("\n", 1)[1] if "\n" in text else text
            return ExecuteResult(
                success=bool(text) and not limit_hit,
                text_response=text or None,
                error="ChatGPT usage/rate limit detected" if limit_hit else None,
                limit_hit=limit_hit,
                execution_time=time.monotonic() - start,
                metadata={
                    "mock": False,
                    "provider": "patchright",
                    "profile": self.profile,
                    "headless": self.headless,
                },
            )
        except Exception as exc:
            return ExecuteResult(
                success=False,
                error=str(exc),
                execution_time=time.monotonic() - start,
                metadata={
                    "mock": False,
                    "provider": "patchright",
                    "error_type": type(exc).__name__,
                },
            )

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None


class ChatGPTWebExecutor:
    """Facade used by the Orchestrator. Real mode never silently falls back to mock."""

    def __init__(self, use_mock: bool = True, **real_kwargs: Any) -> None:
        self.use_mock = use_mock
        self._mock = MockChatGPTWebExecutor()
        self._real = None if use_mock else RealChatGPTWebExecutor(**real_kwargs)

    def execute(self, request: ExecuteRequest) -> ExecuteResult:
        if self.use_mock:
            return self._mock.execute(request)
        assert self._real is not None
        return self._real.execute(request)

    def close(self) -> None:
        if self._real is not None:
            self._real.close()
