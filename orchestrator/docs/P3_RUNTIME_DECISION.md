# P3 Runtime Decision Integration

## Purpose

P3 separates deciding what should happen from executing an agent.

Runtime flow:

```
Evaluation / Failure State
        |
        v
Decision Engine
        |
        v
Agent Router
        |
        v
Execution
```

## Rules

- P0 remains responsible for execution safety.
- P1 remains responsible for retry and failure control.
- P2 remains responsible for evaluation correctness.
- P3 decides the next action and target agent.

## Actions

- continue
- repair
- retry
- change_strategy
- stop
