# P2 Evaluation Repair Routing

## Goal

Separate task-quality failures from execution failures.

## Flow

```
Executor
  |
TaskEvaluator
  |
EvaluationGate
  |
RepairRouter
  |
Targeted Repair Agent
```

## Rules

- Evaluation failure is not an executor failure.
- RetryPolicy remains responsible for execution retries.
- RepairRouter selects the smallest corrective action.
- Missing evaluation is blocked.

## Current routes

| Signal | Route |
|---|---|
| source/citation issue | research_section_repair |
| format issue | repair_agent |
| generic quality issue | repair_agent |
