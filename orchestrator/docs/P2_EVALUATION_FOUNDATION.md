# P2.1 — Independent Task Evaluation Foundation

P2.1 separates transport/execution success from task success without changing autonomous workflow behavior yet.

## Problem addressed

Before P2.1, `AgentOutput.success` was overloaded. A successful browser/LLM execution could be treated as a successful task even when the returned content had not been independently evaluated against task requirements.

P2.1 introduces three distinct signals on every real `Orchestrator.run_agent()` result:

- `metadata.execution_success`: whether the external executor call itself succeeded.
- `metadata.agent_output_success`: whether the agent accepted/parsed the executor response.
- `metadata.task_evaluation`: an independent task-outcome assessment.

This means a transport call can succeed while the task evaluation fails or remains unknown.

## Evaluation contract

`orchestrator.core.evaluation.TaskEvaluator` evaluates an output only when an explicit `_evaluation_contract` is supplied in the agent context.

Supported deterministic checks include:

- non-empty output;
- minimum character count;
- required terms;
- forbidden terms;
- JSON format validation;
- required top-level JSON keys;
- optional case-sensitive matching.

Example:

```python
{
    "_evaluation_contract": {
        "min_chars": 100,
        "required_terms": ["citation", "source"],
        "forbidden_terms": ["Something went wrong"],
    }
}
```

## Conservative semantics

A successful execution with no explicit contract is **not** automatically declared task-successful.

Instead:

```json
{
  "status": "unassessed",
  "task_success": null,
  "reason": "no_evaluation_contract"
}
```

This is intentional. P2 must not replace the old false-positive success signal with a new fabricated semantic score.

An explicit contract can produce:

- `passed` / `task_success=true`
- `failed` / `task_success=false`
- `unassessed` / `task_success=null` when the contract itself is invalid or insufficient

Executor failure and agent-processing failure are recorded separately as failed outcomes.

## Current architecture

```text
External Executor
       |
       v
 ExecuteResult
       |
       +--> execution_success
       |
       v
 Agent.process_result()
       |
       +--> agent_output_success
       |
       v
 TaskEvaluator
       |
       +--> task_evaluation
       |
       v
 Existing P0/P1 state + workflow behavior
```

P2.1 is observational by design. It records the independent outcome but does **not** yet rewrite `AgentOutput.success` or stop a workflow solely because `task_evaluation` failed.

That behavior belongs to the next P2 step: the evaluation gate.

## Why the gate is separate

Changing evaluation and workflow control in one patch would make it difficult to distinguish:

- evaluator defects;
- contract defects;
- retry-policy defects;
- workflow-regression defects.

P2.1 therefore establishes a tested outcome contract first. P2.2 can then gate autonomous continuation using a stable signal.

## Regression coverage

The P2.1 tests verify:

- successful execution without a contract remains `unassessed`;
- execution failure is distinct from agent-output failure;
- explicit text contracts pass only when all checks pass;
- failed task contracts do not masquerade as executor failures;
- JSON contracts validate required keys;
- invalid contracts never become false successes;
- `Orchestrator.run_agent()` records all three outcome layers;
- a task contract may fail while the existing `AgentOutput.success` remains true during this foundation step.

At completion of P2.1 the full repository suite passes with 102 tests and package compilation succeeds.

## Next step

P2.2 will introduce an evaluation gate that decides whether autonomous execution may continue based on `task_evaluation`, while preserving P0/P1 ownership of duplicate/stagnation safety and retry mechanics.
