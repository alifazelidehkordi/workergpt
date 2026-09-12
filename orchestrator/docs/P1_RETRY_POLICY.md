# P1.2 — Central Retry Policy

P1.2 centralizes retry decisions so callers do not blindly repeat failed agent work.

## Controller actions

Every classified failure maps to exactly one controller action:

- `retry`: a bounded retry is allowed.
- `backoff`: retry is allowed only as a transient/backoff path; the decision includes a delay hint.
- `change_strategy`: the same deterministic failure has repeated and `_strategy_id` must change before another execution.
- `operator_action`: external human/operator repair is required, for example restoring authentication.
- `stop`: the failure is terminal or a safety stop and must not be automatically retried.

## Policy matrix

| Failure class | First action | Repeated action |
| --- | --- | --- |
| rate limit | `backoff` | `backoff` with exponential delay metadata |
| timeout/network | `retry` | `retry` with exponential delay metadata |
| validation/source/executor/workflow | `retry` | `change_strategy` after the same-strategy threshold |
| authentication | `operator_action` | `operator_action` |
| safety/non-retryable | `stop` | `stop` |

The default same-strategy threshold is 2 failed executions. This means one repair retry may be attempted, but a third execution with the same strategy is blocked before the external executor.

## P0/P1 ownership boundary

P0 exact-request cache now records successful executions only. Failed requests are deliberately left to P1 because P1 may explicitly allow a bounded retry. This removes the previous contradiction where Failure Memory allowed a retry but P0 duplicate detection blocked it first.

P0 still blocks duplicate successful work and detects no-progress loops. P1 owns failed-action recurrence and retry strategy.

## Persistent state

Failure recurrence remains stored in:

`projects/<project_id>/checkpoints/failure_memory.json`

The current controller decision is also exposed through `AgentOutput.metadata.retry_policy` and project state under `progress.p1_retry_policy`.

Example failure metadata:

```json
{
  "retry_policy": {
    "action": "change_strategy",
    "allowed": false,
    "reason": "strategy_change_required",
    "category": "validation",
    "occurrence": 2,
    "delay_seconds": 0,
    "strategy_change_required": true,
    "operator_action_required": false,
    "terminal": false
  }
}
```

## Project statuses

P1.2 uses explicit statuses instead of one generic `paused` state:

- `paused_retryable`
- `paused_backoff`
- `paused_strategy_required`
- `paused_operator_action`
- `paused_terminal`

Rate-limit failures still write the durable resume checkpoint. The retry policy metadata records the backoff decision without deleting the existing resume mechanism.

## Important limitation

P1.2 decides what should happen next; it does not run an automatic retry loop or sleep inside `run_agent()`. Automatic bounded retry execution and budget enforcement belong to the next P1 step so the controller never hides additional LLM calls inside one agent invocation.

## Regression coverage

Tests verify:

- deterministic failures get one bounded same-strategy retry and then require strategy change;
- exact failed requests are not incorrectly blocked by the P0 success cache;
- rate limits produce exponential backoff metadata;
- timeout/network failures remain retryable;
- authentication failures require operator action and do not reach the executor again;
- safety/non-retryable failures map to terminal stop;
- a changed `_strategy_id` opens a new repair strategy path.
