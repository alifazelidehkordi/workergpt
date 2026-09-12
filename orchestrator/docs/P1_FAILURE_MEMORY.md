# P1.1 — Failure Classification and Failure Memory

Status: implemented.

## Why this exists

P0 stops exact duplicate work and repeated work with no measurable progress. It does not explain *why* an execution failed. Without a durable failure model, a workflow can slightly mutate its prompt and continue retrying the same logical failure.

P1.1 turns failures into deterministic, persistent state before later retry-policy work.

## Runtime flow

```text
run_agent
  -> P0 stagnation preflight
  -> P1 failure-memory preflight
       -> non-retryable previous failure => block
       -> same deterministic failure twice + same strategy => block
       -> transient failure => retry allowed
       -> changed _strategy_id => retry allowed
  -> P0 exact-duplicate gate
  -> executor
  -> classify failure / resolve prior failure
  -> persist failure_memory.json
```

## Failure state

Per-project state is stored at:

```text
projects/<project_id>/checkpoints/failure_memory.json
```

Each stable action scope stores:

- failure category
- normalized reason
- stable failure signature
- retryable/transient flags
- strategy id
- consecutive occurrence count
- same-strategy occurrence count
- bounded failure history
- resolved failure metadata after a successful execution

Dynamic numbers, URLs, long hashes, and file paths are normalized before the signature is calculated so the same root failure does not become a new failure merely because an attempt number or temporary path changed.

## Categories currently recognized

- `rate_limit` — transient, retryable
- `timeout` — transient, retryable
- `network` — transient, retryable
- `authentication` — non-retryable without operator correction
- `safety_stop` — non-retryable P0 stop
- `validation` — retryable, but repeated same-strategy failure requires strategy change
- `source_quality` — retryable, but repeated same-strategy failure requires strategy change
- `executor` — retryable, but repeated same-strategy failure requires strategy change
- `workflow` — fallback deterministic workflow failure

## Strategy-change rule

The default strategy id is `default`.

For deterministic retryable failures, two consecutive occurrences with the same strategy make the next preflight return:

```text
strategy_change_required
```

A retry must then provide a genuinely new strategy identity:

```python
context["_strategy_id"] = "source-first-v2"
```

Changing ordinary prompt wording or an attempt counter does not satisfy the strategy-change requirement.

## State exposed to callers

Blocked `AgentOutput` metadata contains:

```text
blocked_by = p1_failure_memory
reason = strategy_change_required | non_retryable_failure
failure_category
failure_signature
occurrence
strategy_change_required
previous_strategy_id
scope_key
```

Project state uses `paused_strategy_required` when P1 blocks execution.

## P0 compatibility

P1 bookkeeping is excluded from P0 request fingerprints. A P1 state update therefore cannot accidentally make an otherwise identical action look new to the duplicate-request guard.

Rate-limit failures are recorded for diagnostics, but remain retryable and still support the existing resume behavior.

## Tests

`tests/test_p1_failure_memory.py` covers:

- transient vs deterministic classification
- stable signatures despite changing paths and numbers
- same-strategy recurrence detection
- mandatory strategy change after repeated deterministic failure
- transient retry allowance
- failure resolution after success
- orchestrator-level pre-execution blocking
- rate-limit retry compatibility

## Next P1 step

P1.2 should introduce an explicit `RetryPolicy` that consumes failure category, occurrence count, progress state, budget, and strategy history to choose one of:

```text
RETRY_SAME_STRATEGY
RETRY_NEW_STRATEGY
BACKOFF
REQUEST_OPERATOR_ACTION
STOP
```

That policy should replace ad-hoc numeric retry decisions in the research workflow rather than adding another independent retry loop.
