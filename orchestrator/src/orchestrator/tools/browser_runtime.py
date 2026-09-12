"""Patchright browser runtime used by the real ChatGPT web executor."""

from __future__ import annotations

import os
import time
import fcntl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .response_state import ResponseObservation, ResponseState, ResponseStateMachine

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
WEB_SEARCH_CONTROL_SELECTORS = (
    "button[data-testid='composer-button-search']",
    "button[data-testid='search-button']",
    "button[aria-label='Search the web']",
    "form button[aria-label='Search']",
    "button:has-text('Search the web')",
    "[role='menuitem']:has-text('Search the web')",
)
TOOLS_MENU_SELECTORS = (
    "button[aria-label='Tools']",
    "button:has-text('Tools')",
)
WEB_SEARCH_SELECTED_SELECTORS = (
    "button[data-testid='composer-button-search'][aria-pressed='true']",
    "button[data-testid='composer-button-search'][data-state='on']",
    "button[data-testid='composer-button-search'][data-selected='true']",
    "button[aria-label='Search the web'][aria-pressed='true']",
    "form button[aria-label='Search'][aria-pressed='true']",
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
PROVIDER_ERROR_PHRASES = (
    "something went wrong while generating the response",
    "something went wrong. if this issue persists",
    "something went wrong, please try again",
    "something seems to have gone wrong",
    "there was an error generating a response",
    "an error occurred while generating the response",
    "error generating the response",
    "error in message stream",
    "failed to generate a response",
    "response generation failed",
    "network error",
    "unable to load conversation",
)
CODE_BLOCK_LANGUAGES = {"markdown", "md", "json"}
COMPLETION_STABILITY_SECONDS = 3.0


class BrowserRuntimeError(RuntimeError):
    """Base error for real browser execution."""


class BrowserDependencyError(BrowserRuntimeError):
    """Raised when Patchright is not installed."""


class AuthenticationRequiredError(BrowserRuntimeError):
    """Raised when the stored profile is not logged in to ChatGPT."""


class ProfileInUseError(BrowserRuntimeError):
    """Raised when another browser session owns the persistent profile."""


class ResponseIncompleteTimeoutError(TimeoutError):
    """Raised when a response started but did not reach a stable completed state."""


class ProviderResponseError(BrowserRuntimeError):
    """Raised when ChatGPT reports that response generation failed."""


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


def _wait_for_visible(page: Any, selectors: tuple[str, ...], timeout_seconds: float = 15.0) -> Any | None:
    """Wait for asynchronously rendered ChatGPT controls to become visible."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        locator = _visible(page, selectors)
        if locator is not None:
            return locator
        time.sleep(0.2)
    return _visible(page, selectors)


def _contains_rate_limit(text: str, prompt: str = "") -> bool:
    """Classify provider notices without treating the user's prompt as an error."""
    candidate = text
    if prompt:
        candidate = candidate.replace(prompt, "")
    lowered = candidate.lower()
    return any(phrase in lowered for phrase in RATE_LIMIT_PHRASES)


def _contains_provider_error(text: str, prompt: str = "") -> bool:
    """Identify ChatGPT generation failures without scanning the user's prompt."""
    candidate = text
    if prompt:
        candidate = candidate.replace(prompt, "")
    lowered = candidate.lower()
    return any(phrase in lowered for phrase in PROVIDER_ERROR_PHRASES)


def _restore_fenced_code_blocks(rendered: str, blocks: list[dict[str, str]]) -> str:
    """Restore Markdown/JSON fences stripped by ChatGPT's rendered message DOM."""
    supported = [
        block
        for block in blocks
        if block.get("language", "").lower() in CODE_BLOCK_LANGUAGES and block.get("text") is not None
    ]
    if not supported:
        return rendered.strip()

    def fenced(block: dict[str, str]) -> str:
        language = block["language"].lower()
        raw = block["text"].rstrip("\n")
        return f"```{language}\n{raw}\n```"

    # Agents are asked for exactly one fenced block. In that common case the DOM's
    # remaining text is just the language label and Copy button added by ChatGPT.
    if len(supported) == 1:
        block = supported[0]
        raw = block["text"].strip()
        remainder = rendered.replace(raw, "", 1)
        chrome = {"", block["language"].lower(), "copy", "copy code", "copied!"}
        if all(line.strip().lower() in chrome for line in remainder.splitlines()):
            return fenced(block)

    restored = rendered
    for block in supported:
        raw = block["text"].strip()
        if raw and raw in restored:
            restored = restored.replace(raw, fenced(block), 1)
    return restored.strip()


def _assistant_message_text(message: Any) -> str:
    """Read a rendered assistant message while retaining source code fences."""
    try:
        payload = message.evaluate(
            """element => ({
                rendered: element.innerText || '',
                blocks: Array.from(element.querySelectorAll('pre code')).map(code => {
                    const className = typeof code.className === 'string' ? code.className : '';
                    const classMatch = className.match(/(?:^|\\s)language-([\\w+-]+)/i);
                    const languageNode = code.closest('[data-language]');
                    let language = classMatch ? classMatch[1] : '';
                    if (!language && languageNode) language = languageNode.dataset.language || '';
                    if (!language) {
                        const pre = code.closest('pre');
                        const label = pre && pre.previousElementSibling
                            ? (pre.previousElementSibling.innerText || '').trim().toLowerCase()
                            : '';
                        if (label === 'markdown' || label === 'md' || label === 'json') language = label;
                    }
                    return {language, text: code.textContent || ''};
                })
            })"""
        )
        if isinstance(payload, dict):
            rendered = str(payload.get("rendered", ""))
            blocks = payload.get("blocks", [])
            if isinstance(blocks, list):
                return _restore_fenced_code_blocks(rendered, blocks)
    except Exception:
        pass
    return message.inner_text(timeout=3000).strip()


def _search_control_is_selected(control: Any) -> bool:
    """Read the common observable states used by ChatGPT toggle controls."""
    for attribute, selected_values in (
        ("aria-pressed", {"true"}),
        ("aria-checked", {"true"}),
        ("data-selected", {"true"}),
        ("data-state", {"on", "checked", "active"}),
    ):
        try:
            value = control.get_attribute(attribute)
        except Exception:
            continue
        if value is not None and value.strip().lower() in selected_values:
            return True
    return False


class PatchrightChatGPTSession:
    """Purpose-built persistent Patchright session for ChatGPT."""

    def __init__(self, options: BrowserOptions) -> None:
        self.options = options
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._profile_lock: TextIO | None = None
        self._last_prompt = ""
        self._state = "created"
        self._response_state = ResponseState.WAITING

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
        self._acquire_profile_lock()
        self._state = "starting"
        try:
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
            self._state = "ready"
        except Exception:
            self._state = "failed"
            self.close()
            raise

    def _acquire_profile_lock(self) -> None:
        if self._profile_lock is not None:
            return
        lock_path = self.options.profile_dir / ".orchestrator-browser.lock"
        lock_file = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.seek(0)
            owner = lock_file.read().strip() or "another process"
            lock_file.close()
            raise ProfileInUseError(
                f"ChatGPT profile is already in use ({owner}): {self.options.profile_dir}"
            ) from exc
        # Metadata may remain after a crash, but the kernel lock does not. Owning
        # the advisory lock makes it safe to replace stale ownership information.
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()}\n")
        lock_file.flush()
        self._profile_lock = lock_file

    def _release_profile_lock(self) -> None:
        if self._profile_lock is None:
            return
        try:
            fcntl.flock(self._profile_lock.fileno(), fcntl.LOCK_UN)
        finally:
            self._profile_lock.close()
            self._profile_lock = None

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            self._context = None
            self._page = None
            try:
                if self._playwright is not None:
                    self._playwright.stop()
            finally:
                self._playwright = None
                self._release_profile_lock()
                self._state = "closed"

    @property
    def state(self) -> str:
        """Lifecycle state for diagnostics and recovery controllers."""
        return self._state

    def health(self) -> dict[str, Any]:
        """Return a provider-neutral health snapshot without touching the UI."""
        return {
            "state": self._state,
            "profile": str(self.options.profile_dir),
            "page_open": self._page is not None,
            "authenticated": self._page is not None and self._state in {"ready", "sending", "generating"},
            "response_state": self._response_state.value,
        }

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

    def enable_web_search(self) -> bool:
        """Best-effort enable ChatGPT web search and report observable selection."""
        control = _visible(self.page, WEB_SEARCH_CONTROL_SELECTORS)
        if control is None:
            menu = _visible(self.page, TOOLS_MENU_SELECTORS)
            if menu is not None:
                try:
                    menu.evaluate("(element) => element.click()")
                except Exception:
                    try:
                        menu.click()
                    except Exception:
                        return False
                control = _wait_for_visible(self.page, WEB_SEARCH_CONTROL_SELECTORS, timeout_seconds=2.0)
        if control is None:
            return False
        if not _search_control_is_selected(control):
            try:
                control.evaluate("(element) => element.click()")
            except Exception:
                try:
                    control.click()
                except Exception:
                    return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if _search_control_is_selected(control):
                return True
            if _visible(self.page, WEB_SEARCH_SELECTED_SELECTORS) is not None:
                return True
            time.sleep(0.1)
        return _search_control_is_selected(control) or _visible(
            self.page, WEB_SEARCH_SELECTED_SELECTORS
        ) is not None

    def _assistant_count(self) -> int:
        try:
            return int(self.page.locator(ASSISTANT_MESSAGE_SELECTOR).count())
        except Exception:
            return 0

    def send_message(self, text: str) -> int:
        prompt = text.strip()
        if not prompt:
            raise ValueError("Prompt cannot be empty")
        self._state = "sending"
        editor = _wait_for_visible(
            self.page,
            EDITOR_SELECTORS,
            timeout_seconds=min(60.0, max(15.0, float(self.options.timeout_seconds))),
        )
        if editor is None:
            raise AuthenticationRequiredError("ChatGPT composer is unavailable; login may be required")
        before = self._assistant_count()
        self._last_prompt = prompt
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
        state_machine = ResponseStateMachine(
            required_assistant_count=before_count + 1,
            stable_seconds=COMPLETION_STABILITY_SECONDS,
        )
        self._state = "generating"
        while time.monotonic() < hard_deadline:
            for selector in ("[role='alert']", "[role='dialog']", "[data-testid*='toast']"):
                try:
                    notices = self.page.locator(selector)
                    for index in range(int(notices.count())):
                        notice = notices.nth(index)
                        if not notice.is_visible():
                            continue
                        notice_text = notice.inner_text(timeout=500).strip()
                        if _contains_rate_limit(notice_text, self._last_prompt):
                            self._response_state = ResponseState.RATE_LIMITED
                            return "__ORCHESTRATOR_RATE_LIMIT__\n" + notice_text
                        if _contains_provider_error(notice_text, self._last_prompt):
                            raise ProviderResponseError(notice_text)
                except ProviderResponseError:
                    raise
                except Exception:
                    continue
            count = self._assistant_count()
            if count > before_count:
                seen_response = True
                messages = self.page.locator(ASSISTANT_MESSAGE_SELECTOR)
                try:
                    current = _assistant_message_text(messages.nth(count - 1))
                except Exception:
                    current = ""
                if _contains_provider_error(current, self._last_prompt):
                    raise ProviderResponseError(current)
                generating = _visible(self.page, STOP_BUTTON_SELECTORS) is not None
                observed_state = state_machine.observe(
                    ResponseObservation(
                        assistant_count=count,
                        generating=generating,
                        body_text=current,
                        now=time.monotonic(),
                    )
                )
                self._response_state = observed_state
                if current != last_text:
                    last_text = current
                    stable_since = None
                    activity_deadline = min(hard_deadline, time.monotonic() + 30)
                elif current:
                    if generating:
                        # A generation phase can resume after a tool call or a
                        # transient composer rerender; require a fresh quiet window.
                        stable_since = None
                    else:
                        stable_since = stable_since or time.monotonic()
                        if observed_state is ResponseState.STABLE and time.monotonic() - stable_since >= COMPLETION_STABILITY_SECONDS:
                            if _contains_rate_limit(current, self._last_prompt):
                                self._response_state = ResponseState.RATE_LIMITED
                                return "__ORCHESTRATOR_RATE_LIMIT__\n" + current
                            self._state = "ready"
                            return current
            if time.monotonic() >= activity_deadline and not seen_response:
                raise TimeoutError("ChatGPT did not start responding before the activity timeout")
            time.sleep(0.5)
        if seen_response:
            self._state = "failed"
            raise ResponseIncompleteTimeoutError(
                f"Timed out after {timeout_seconds}s before the ChatGPT response completed; "
                "partial output was discarded"
            )
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
