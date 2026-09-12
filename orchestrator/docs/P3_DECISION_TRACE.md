# P3 Decision Trace

P3 decisions are recorded separately from execution output.

Flow:

DecisionEngine -> DecisionRuntime -> AgentRouter -> DecisionTrace

Each event should include:

- action
- selected agent
- reason
- strategy id when available
- evaluation/failure context

The trace exists for debugging autonomous decisions and measuring routing quality.
