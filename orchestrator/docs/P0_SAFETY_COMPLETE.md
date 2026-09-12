# P0 Safety Controls — Complete

P0 is the immediate damage-control layer for WorkerGPT. Its job is to prevent expensive repeated actions and logical retry loops before P1 introduces richer retry strategy and failure memory.

## Runtime flow

Every real `Orchestrator.run_agent()` call now passes through two independent controls:

1. **Exact duplicate gate** — blocks an agent request when the semantic context and attached file content are unchanged.
2. **Persistent stagnation guard** — groups attempts by stable task scope (project/phase/module/topic/section/question) and stops the scope after repeated executions produce no measurable progress.

The research workflow already calls `Orchestrator.run_agent()`, so section research, section criticism, targeted repair, final criticism, planning, synthesis, and generic agents inherit these controls without duplicating loop logic inside `research_workflow.py`.

## What counts as progress

P0 intentionally uses deterministic signals rather than another LLM call:

- **Substantive artifact change**: output content changes enough in vocabulary or length to be more than a superficial wording edit.
- **Issue reduction**: the unresolved issue-id set becomes a strict subset of the previous set.
- **Quality-score improvement**: a numeric confidence/quality/score signal improves by the configured minimum delta when such a signal exists.

A changed prompt alone is not progress.

## Stagnation rule

The first completed result establishes a baseline. Each subsequent attempt in the same logical scope that has none of the progress signals above increments `stagnant_count`. At 3 consecutive stagnant attempts:

- the latest raw output is saved for diagnosis;
- project status becomes `paused_no_progress`;
- `state.progress.p0_stop.reason` is `no_measurable_progress`;
- future attempts in that same logical scope are blocked before the external executor is called;
- the stop survives process restarts via `checkpoints/progress_state.json`.

A future P1 strategy can intentionally open a new scope by supplying a different `_strategy_id` after it has diagnosed the failure and changed approach.

## Persistent files

Per project:

- `checkpoints/request_cache.json` — exact completed request fingerprints.
- `checkpoints/progress_state.json` — action-scope progress snapshots, stagnant counts, and stop state.
- `state.json` — human/CLI-visible stop status and reason.

Rate-limit interruptions are excluded from request/progress completion records so `resume` can replay the interrupted request.

## False-positive protections

P0 does **not** stop when:

- file contents changed even if the path is unchanged;
- output changed substantively;
- unresolved issue ids were reduced;
- a quality/confidence score improved;
- a rate-limited request is retried;
- a deliberately new strategy uses a new `_strategy_id`.

## Regression coverage

`tests/test_p0_safety.py` covers:

- identical request blocked before executor;
- changed semantic input allowed;
- changed file content allowed;
- rate-limit retry allowed;
- slightly changed retry requests with identical logical output stopped after the threshold;
- stopped state surviving a new Orchestrator process;
- substantive artifact changes not triggering stagnation;
- issue resolution resetting stagnation;
- quality-score improvement resetting stagnation.

GitHub Actions runs the full pytest suite and package compilation for every `orchestrator/**` push.

## P0 exit criteria

P0 is complete when all of the following hold:

- an identical completed action cannot reach the external executor twice;
- changing retry wording alone cannot bypass loop protection;
- no-progress state survives restart;
- every no-progress stop records a machine-readable reason;
- genuine artifact/issue/score progress resets the stagnation counter;
- rate-limit resume remains functional;
- regression tests and package compilation pass.

These criteria are implemented and covered by CI.

## P1 handoff

P1 should now build on these deterministic safety controls rather than replace them. Its scope is:

- retry policy by failure class;
- failure classification and recurrence tracking;
- failure memory;
- mandatory strategy change after repeated failure;
- retry budgets by action/failure type;
- explicit decision records explaining why another request is justified.
