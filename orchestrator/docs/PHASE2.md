# Phase 2 — Real ChatGPT Web Executor

Phase 2 adds an explicit Patchright-backed browser mode alongside the mock executor.

## Install

```bash
cd orchestrator
python -m pip install -e .
patchright install chromium
```

## Create or reuse a logged-in profile

```bash
python -m orchestrator.login --profile default
```

A visible Chromium window opens. Log in to ChatGPT, return to the terminal, and press Enter. The login is stored under `~/.orchestrator/profiles/default` unless `--profile-dir` is supplied.

## Browser runtime

The runtime uses a persistent Chromium profile, supports visible or headless launch, uploads local files through ChatGPT's file input, waits for a stable assistant response, and detects common usage/rate-limit messages.

## Failure behavior

Real browser mode must fail explicitly when the browser, authentication, timeout, or rate-limit path fails. It must not silently substitute mock output.

## Status

- 2.1 Patchright runtime scaffold: implemented
- 2.2 Persistent login profile + login command: implemented
- 2.3 Download handling / smarter timeout: pending
- 2.4 Headless/non-headless primitive: implemented in runtime
- 2.5 End-to-end real pipeline integration/test: pending
- 2.6 Rate-limit detection: implemented; checkpoint/resume integration pending
