# P0 Duplicate Request Guard

## Problem

The orchestrator can repeat expensive agent calls when the state has not changed.

## Added

`core/request_guard.py` introduces a lightweight execution gate.

Flow:

```
Agent Request
     |
     v
RequestGuard
     |
     +--> Existing fingerprint -> STOP
     |
     +--> New fingerprint -> Execute
```

## Fingerprint inputs

- agent name
- execution context
- related files metadata

## Next P0 step

Add stagnation detection:

- compare previous and current results
- detect zero improvement
- stop repeated near-duplicate strategies
