# Orchestrator MVP Usage Guide

## Overview

Orchestrator coordinates research agents through a pipeline:

```
Researcher -> Critic -> Synthesizer -> Checkpoint
```

The current MVP runs with mock execution and focuses on reliable state management.

## Quick Start

```bash
python -m orchestrator --help
```

## Example Project

Create a project for the Kintsugi research workflow:

```bash
orchestrator create kintsugi
```

Run a mock pipeline:

```bash
orchestrator run kintsugi
```

## State

Each project stores execution state including:

- current module
- active agent
- progress
- checkpoints
- latest outputs

## Recovery

If a pipeline fails, later agents are not executed. The last checkpoint remains available for resume functionality planned in Phase 4.
