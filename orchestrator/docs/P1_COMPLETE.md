# P1 — Retry and Loop Control Complete

P1 is complete. The controller now treats a failed execution as a stateful event that must be classified, remembered, evaluated, budgeted, and explicitly authorized before another external LLM/browser call is made.

## Completed layers

### P1.1 — Failure classification and memory

`orchestrator.core.failure_memory` classifies failures deterministically and persists their recurrence in:

`projects/<project_id>/checkpoints/failure_memory.json`

Current failure classes include rate limit, timeout, network, authentication, validation, source quality, executor, workflow, and safety stops. Dynamic numbers, paths, and similar volatile details are normalized before signature generation so the same logical failure is recognized across attempts.

Repeated deterministic failures with the same `_strategy_id` are not allowed to loop indefinitely. After the same-strategy threshold, execution requires a new strategy identifier.

### P1.2 — Central retry policy

`orchestrator.core.retry_policy` maps failure state to exactly one controller action:

- `retry`
- `backoff`
- `change_strategy`
- `operator_action`
- `stop`

Authentication failures require operator action. Safety/non-retryable failures stop. Deterministic repeated failures require strategy change. Transient network/timeout failures can retry with bounded backoff. Rate limits enter durable backoff instead of immediately hammering the executor.

### P1.3 — Bounded retry execution and budgets

`orchestrator.core.retry_execution.RetryExecutionController` is the only autonomous layer that turns a `retry` policy decision into another external call.

Its durable ledger is stored at:

`projects/<project_id>/checkpoints/retry_execution.json`

For each stable action scope it tracks:

- actual external attempts;
- reported token usage when available;
- reported USD cost when available;
- accumulated execution time;
- durable `next_retry_at` backoff deadlines;
- active/resolved retry-cycle state.

P0/P1 preflight blocks do not consume an external-call budget because no external request was sent.

## Retry budgets

Project retry settings live under `[settings]` in `project.toml`.

```toml
[settings]
max_retries = 3
# Optional hard guards when usage metadata is available:
# max_retry_tokens = 50000
# max_retry_cost_usd = 5.0
# max_retry_execution_seconds = 3600
```

`max_retries = 3` means at most four external attempts for one retry cycle: the original attempt plus three retries.

New projects now explicitly write `max_retries = 3` and `auto_checkpoint = true` into `project.toml` instead of relying only on an unused model default.

The call/attempt budget is always enforceable. Token and cost budgets are additionally enforced when the executor/provider reports those usage fields; the controller does not invent provider billing data.

## Backoff semantics

There are two intentionally different transient paths.

### `retry`

For retryable network/timeout-style failures, controlled execution waits for the policy delay and then performs another attempt if the remaining budget allows it.

### `backoff`

For rate-limit-style failures, controlled execution writes `next_retry_at` and returns immediately. It does **not** sleep and repeatedly call the external service. Any later controlled invocation before that deadline is blocked locally with `backoff_active` and does not reach the executor.

This state survives process restarts.

## Autonomous execution boundary

One-shot APIs remain available for callers that explicitly want one attempt:

`run_agent()`

Autonomous flows now use:

`run_agent_with_retries()`

The effective architecture is:

```text
ResearchWorkflow / run_research_pipeline / resume
                    |
                    v
          run_agent_with_retries
                    |
                    v
       RetryExecutionController
          |      |       |
          |      |       +--> durable retry/cost ledger
          |      +----------> backoff deadline
          +-----------------> attempt budget
                    |
                    v
                run_agent
                    |
        +-----------+-----------+
        |                       |
        v                       v
   P0 safety gates        P1 failure/policy gates
        |                       |
        +-----------+-----------+
                    |
                    v
                 executor
```

The durable section-by-section `ResearchWorkflow` routes its real agent calls through controlled execution when the orchestrator exposes it, while preserving a one-shot fallback for test doubles and compatible callers.

The generic research pipeline and durable resume path also use controlled execution.

## Strategy changes do not reset the global retry cycle

`_strategy_id` is intentionally not part of the retry-execution budget scope. It can satisfy Failure Memory's requirement to change strategy, but it cannot create an unlimited fresh call budget simply by renaming the strategy.

## Interaction with P0

P0 and P1 now have separate ownership:

- P0 blocks duplicate successful work and no-progress loops.
- P1 controls failed-work recurrence and retry authorization.
- Failed executions are not inserted into P0's exact-success cache, so a P1-authorized retry is not accidentally blocked as a duplicate.
- P0 stagnation detection still remains a final safety net if retries produce no measurable improvement.

## Regression coverage

P1 regression tests now cover:

- failure normalization and classification;
- persistent failure recurrence;
- required strategy change after repeated deterministic failure;
- transient retry behavior;
- rate-limit backoff decisions;
- authentication/operator-action behavior;
- terminal safety stops;
- real controlled recovery from a network failure;
- durable backoff blocking without another executor call;
- retry after a backoff deadline;
- hard attempt-budget exhaustion;
- token-budget exhaustion;
- integration with the existing research workflow without breaking its FakeOrchestrator test boundary;
- full package compilation.

## Completion criteria

P1 is considered closed because autonomous retry behavior is now bounded, persistent, inspectable, and policy-driven. A failed operation can no longer silently repeat forever: it either succeeds, consumes a finite retry budget, waits behind a durable backoff, requires a strategy change, requires operator action, or stops.

Further semantic decision-making about *which new strategy to invent* belongs to the later decision/evaluator phases rather than P1 retry mechanics.
