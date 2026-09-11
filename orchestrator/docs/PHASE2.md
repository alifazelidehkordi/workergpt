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
python -m orchestrator login --profile default
```

A visible Chromium window opens. Log in to ChatGPT, return to the terminal, and press Enter. The login is stored under `~/.orchestrator/profiles/default` unless `--profile-dir` is supplied.

## Run a real pipeline

```bash
python -m orchestrator --real --profile default pipeline kintsugi --question "Your research question"
```

After the profile has been authenticated, headless mode is available with `--headless`.

## Browser/runtime behavior

The runtime uses a persistent Chromium profile, supports visible/headless launch, file upload, activity-aware response timeout, file download capture, and rate-limit detection. Real mode never silently substitutes mock output.

If a ChatGPT usage/rate limit is detected, Orchestrator writes a local JSON checkpoint containing the agent, context, and file inputs. Retry it later with:

```bash
python -m orchestrator --real --profile default resume kintsugi
```

## Status

- 2.1 Patchright runtime scaffold: implemented
- 2.2 Persistent login profile + `login` command: implemented
- 2.3 Download handling + activity-aware timeout: implemented
- 2.4 Headless/non-headless switch: implemented
- 2.5 Real pipeline integration: implemented; live ChatGPT verification still requires a logged-in browser profile on the execution machine
- 2.6 Rate-limit stop + local checkpoint + `resume`: implemented

## Verification

GitHub Actions runs package installation, unit/regression tests, and `compileall`. The live browser path is intentionally not executed in CI because it requires a real authenticated ChatGPT profile.
