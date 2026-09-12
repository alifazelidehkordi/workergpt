# P3 Runtime Integration

P3 now has a runtime decision bridge:

```
Evaluation / Failure State
        |
        v
DecisionEngine
        |
        v
DecisionRuntime
        |
        v
AgentRouter
        |
        v
Selected workflow agent
```

The integration layer converts decisions into workflow metadata so execution paths can record:

- selected action
- selected agent
- decision reason

P3 keeps routing decisions separate from execution implementation.
