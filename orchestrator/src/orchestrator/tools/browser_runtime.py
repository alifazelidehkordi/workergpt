"""Patchright browser runtime used by the real ChatGPT web executor."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHATGPT_URL = "https://chatgpt.com/?temporary-chat=true"
EDITOR_SELECTORS = (
    "#prompt-textarea",
    "div[contenteditable='true'][data-placeholder]",
    "div[contenteditable='true'][role='textbox']",
    "textarea",
)
SEND_BUTTON_SELECTORS = (
    "button[data-testid='send-button']",
    "button[data-testid='composer-submit-button']",
    "button[aria-label*='Send']",
    "button[aria-label*='ارسال']",
)
STOP_BUTTON_SELECTORS = (
    "button[data-testid='stop-button']",
    "button[aria-label*='Stop']",
)
DOWNLOAD_SELECTORS = (
    "a[download]",
    "a[href*='files.oaiusercontent.com']",
    "a:has-text('Download')",
    "button:has-text('Download')",
)
ASSISTANT_MESSAGE_SELECTOR = "[data-message-author-role='assistant']"
FILE_INPUT_SELECTOR = "input[type='file']"
RATE_LIMIT_PHRASES = (
    "too many requests",
    "you've reached our limit",
    "you have reached the limit",
    "you've reached the limit",
    "rate limit",
    "usage limit",
    "try again later",
    "come back later",
)


class BrowserRuntimeError(RuntimeError):
    """Base error for real browser execution."""


class BrowserDependencyError(BrowserRuntimeError):
    """Raised when Patchright is not installed."""


class AuthenticationRequiredError(BrowserRuntimeError):
    """Raised when the stored profile is not logged in to ChatGPT."""


@dataclass(frozen=True)
class BrowserOptions:
    profile_dir: Path
    download_dir: Path
    headless: bool = False
    timeout_seconds: int = 300
    url: str = CHATGPT_URL

    @classmethod
    def defaults(
        cls,
        *,
        profile: str = "default",
        profile_dir: Path | None = None,
        download_dir: Path | None = None,
        headless: bool = False,
        timeout_seconds: int = 300,
    ) -> "BrowserOptions":
        base = Path(os.environ.get("ORCHESTRATOR_HOME", "~/.orchestrator")).expanduser()
        resolved_profile = profile_dir or base / "profiles" / profile
        resolved_downloads = download_dir or base / "downloads"
        return cls(
            profile_dir=Path(resolved_profile).expanduser().resolve(),
            download_dir=Path(resolved_downloads).expanduser().resolve(),
            headless=headless,
            timeout_seconds=timeout_seconds,
        )


def _load_patchright() -> Any:
    try:
        from patchright import sync_api
    except ImportError as exc:
        raise BrowserDependencyError(
            "Patchright is not installed. Run `pip install -e .` and then "
            "`patchright install chromium`."
        ) from exc
    return sync_api


def _visible(page: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=500):
                return locator
        except Exception:
            continue
    return None


class PatchrightChatGPTSession:
    """Purpose-built persistent Patchright session for ChatGPT."""

    def __init__(self, options: BrowserOptions) -> None:
        self.options = options
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None

    @property
    def page(self) -> Any:
        if self._page is None:
            raise BrowserRuntimeError("Browser session is not open")
        return self._page

    def open(self) -> None:
        if self._context is not None:
            return
        self.options.profile_dir.mkdir(parents=True, exist_ok=True)
        self.options.download_dir.mkdir(parents=True, exist_ok=True)
        sync_api = _load_patchright()
        self._playwright = sync_api.sync_playwright().start()
        kwargs: dict[str, Any] = {
            "user_data_dir": str(self.options.profile_dir),
            "headless": self.options.headless,
            "accept_downloads": True,
            "downloads_path": str(self.options.download_dir),
            "no_viewport": True,
            "ignore_default_args": ["--password-store=basic", "--use-mock-keychain"],
            "args": [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
                "--window-size=1400,950",
            ],
        }
        chrome_binary = os.environ.get("CHATGPT_CHROME_BINARY", "").strip()
        if chrome_binary:
            kwargs["executable_path"] = chrome_binary
        try:
            self._context = self._playwright.chromium.launch_persistent_context(**kwargs)
        except Exception:
            kwargs.pop("executable_path", None)
            self._context = self._playwright.chromium.launch_persistent_context(**kwargs)
        self._context.set_default_timeout(self.options.timeout_seconds * 1000)
        pages = list(self._context.pages)
        self._page = pages[0] if pages else self._context.new_page()
        self._page.goto(self.options.url, wait_until="domcontentloaded")

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            self._context = None
            self._page = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None

    def is_authenticated(self) -> bool:
        self.open()
        if _visible(self.page, EDITOR_SELECTORS) is not None:
            return True
        body = self.page.locator("body").inner_text(timeout=3000).lower()
        return not any(token in body for token in ("log in", "sign up", "ورود", "ثبت نام"))

    def assert_authenticated(self) -> None:
        if not self.is_authenticated():
            raise AuthenticationRequiredError(
                f"ChatGPT login is required for profile: {self.options.profile_dir}. "
                "Run `python -m orchestrator login --profile <name>` first."
            )

    def start_new_chat(self) -> None:
        self.page.goto(CHATGPT_URL, wait_until="domcontentloaded")
        self.assert_authenticated()

    def upload(self, path: Path) -> None:
        file_path = Path(path).expanduser().resolve()
        if not file_path.is_file():
            raise FileNotFoundError(file_path)
        locator = self.page.locator(FILE_INPUT_SELECTOR).first
        if not locator.count():
            raise BrowserRuntimeError("ChatGPT file input is not available")
        locator.set_input_files(str(file_path))
        time.sleep(1.0)

    def _assistant_count(self) -> int:
        try:
            return int(self.page.locator(ASSISTANT_MESSAGE_SELECTOR).count())
        except Exception:
            return 0

    def send_message(self, text: str) -> int:
        prompt = text.strip()
        if not prompt:
            raise ValueError("Prompt cannot be empty")
        editor = _visible(self.page, EDITOR_SELECTORS)
        if editor is None:
            raise AuthenticationRequiredError("ChatGPT composer is unavailable; login may be required")
        before = self._assistant_count()
        try:
            editor.fill(prompt)
        except Exception:
            editor.click()
            self.page.keyboard.insert_text(prompt)
        button = _visible(self.page, SEND_BUTTON_SELECTORS)
        if button is not None:
            try:
                button.evaluate("(element) => element.click()")
            except Exception:
                button.click()
        else:
            editor.press("Enter")
        return before

    def wait_for_response(self, before_count: int, timeout_seconds: int) -> str:
        started = time.monotonic()
        hard_deadline = started + timeout_seconds
        activity_deadline = min(hard_deadline, started + min(60, max(20, timeout_seconds // 2)))
        seen_response = False
        stable_since: float | None = None
        last_text = ""
        while time.monotonic() < hard_deadline:
            body = self.page.locator("body").inner_text(timeout=3000)
            if any(phrase in body.lower() for phrase in RATE_LIMIT_PHRASES):
                return "__ORCHESTRATOR_RATE_LIMIT__\n" + body[-4000:]
            count = self._assistant_count()
            if count > before_count:
                seen_response = True
                messages = self.page.locator(ASSISTANT_MESSAGE_SELECTOR)
                try:
                    current = messages.nth(count - 1).inner_text(timeout=3000).strip()
                except Exception:
                    current = ""
                generating = _visible(self.page, STOP_BUTTON_SELECTORS) is not None
                if current != last_text:
                    last_text = current
                    stable_since = None
                    activity_deadline = min(hard_deadline, time.monotonic() + 30)
                elif current and not generating:
                    stable_since = stable_since or time.monotonic()
                    if time.monotonic() - stable_since >= 1.0:
                        return current
            if time.monotonic() >= activity_deadline and not seen_response:
                raise TimeoutError("ChatGPT did not start responding before the activity timeout")
            time.sleep(0.5)
        if seen_response and last_text:
            return last_text
        raise TimeoutError(f"Timed out waiting for ChatGPT response after {timeout_seconds}s")

    def collect_downloads(self, *, timeout_seconds: int = 20, limit: int = 5) -> list[Path]:
        """Capture visible ChatGPT download controls after a response."""
        saved: list[Path] = []
        seen: set[str] = set()
        for selector in DOWNLOAD_SELECTORS:
            try:
                candidates = self.page.locator(selector)
                count = min(int(candidates.count()), limit)
            except Exception:
                continue
            for index in range(count):
                candidate = candidates.nth(index)
                try:
                    signature = f"{selector}:{candidate.get_attribute('href')}:{candidate.inner_text(timeout=500)}"
                except Exception:
                    signature = f"{selector}:{index}"
                if signature in seen:
                    continue
                seen.add(signature)
                try:
                    with self.page.expect_download(timeout=timeout_seconds * 1000) as info:
                        try:
                            candidate.evaluate("(element) => element.click()")
                        except Exception:
                            candidate.click()
                    download = info.value
                    destination = self.options.download_dir / download.suggested_filename
                    if destination.exists():
                        stem, suffix = destination.stem, destination.suffix
                        destination = destination.with_name(f"{stem}_{int(time.time())}{suffix}")
                    download.save_as(str(destination))
                    saved.append(destination)
                    if len(saved) >= limit:
                        return saved
                except Exception:
                    continue
        return saved


def open_login_session(profile: str = "default", profile_dir: Path | None = None) -> PatchrightChatGPTSession:
    """Open a visible persistent browser session for interactive login."""
    options = BrowserOptions.defaults(profile=profile, profile_dir=profile_dir, headless=False, timeout_seconds=600)
    session = PatchrightChatGPTSession(options)
    session.open()
    return session
