# P2 Evaluation and Self-Correction Layer

## Goal

Separate execution success from task success.

## Components

- TaskEvaluator: measures task quality.
- EvaluationGate: decides continue, repair, or block.
- RepairRouter: selects repair path.
- EvaluationLoopController: bounds repair and re-evaluation cycles.
- EvaluationMemory: stores previous repair outcomes.

## Lifecycle

```
Execute
  -> Evaluate
  -> Continue
  -> or Repair
       -> Re-evaluate
       -> Pass or Stop
```

## Safety

Repair loops are bounded and must not bypass existing P0 safety or P1 retry controls.
