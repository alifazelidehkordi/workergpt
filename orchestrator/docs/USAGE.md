# Orchestrator MVP Usage Guide

## Overview

Orchestrator coordinates research agents through a pipeline:

```
Researcher -> Critic -> Synthesizer -> Checkpoint
```

Mock execution is the default. Put `--real` before the command to use the persistent ChatGPT browser profile.

## Quick Start

```bash
python -m orchestrator --help
```

## Example Project

Create a project:

```bash
python -m orchestrator create kintsugi --name "Kintsugi research"
```

Run a mock pipeline:

```bash
python -m orchestrator pipeline kintsugi --question "What does the evidence show?"
```

## Durable dossier workflow

The vault must contain a `موضوعات` directory. Each Markdown topic needs a unique `research_id` in frontmatter. Initialization expects 70 topics by default and refuses a partial or duplicate index:

```bash
python -m orchestrator workflow init kintsugi \
  --vault /absolute/path/to/vault \
  --report /absolute/path/to/progress-report.md
```

For a smaller or test vault, set another count. `--expected-topics 0` disables only the count check; uniqueness is always enforced.

```bash
python -m orchestrator workflow init demo --vault /path/to/vault --expected-topics 3
python -m orchestrator workflow status demo
```

Run one topic with the real browser. Omitting `--topic` selects the first incomplete topic. A later invocation resumes from saved artifacts.

```bash
python -m orchestrator --real --profile kintsugi workflow run kintsugi --topic KSR-10
python -m orchestrator --real --profile kintsugi workflow run kintsugi
```

Useful bounds:

- `--max-sections 1` stops after one newly approved section.
- `--max-revisions 1` permits one targeted repair round after a final `revise` verdict.
- `--max-section-revisions 1` permits one fresh Researcher → Critic retry for each rejected section.
- A topic planner is run once and stored before section work begins. It supplies bounded questions, search queries, and exclusions for all six sections.
- Section critics return audit-only JSON. A `revise` verdict sends structured issues to a fresh researcher call and then a new independent critic; the critic never silently rewrites or approves its own prose.
- Research output is rejected before criticism when it has the wrong heading, is empty/truncated or oversized, lacks numbered citations and real `https` source links, or contains browser UI/internal citation tokens such as `contentReference` or `oaicite`.
- A final audit without actionable section IDs, a failed local validator, or exhausted repair rounds also moves the topic to `needs_review` without replacing the source.

## State

Each project stores execution state including:

- current module
- active agent
- progress
- checkpoints
- latest outputs

## Recovery

If an agent or parser fails, later stages are not executed. The durable workflow records the active stage, plan, content hashes, immutable revision attempts, audit history, and a commit journal. It detects edits to source, plan, or approved artifacts instead of silently overwriting them. Use `resume` for the original pipeline's rate-limit checkpoint, or repeat `workflow run` for a dossier topic.
