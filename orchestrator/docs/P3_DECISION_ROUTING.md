# P3 Decision Routing Foundation

P3 separates choosing an action from executing an agent.

Current flow:

DecisionEngine -> AgentRouter -> Agent execution

Supported routes:

- continue
- repair
- retry
- stop

The router is intentionally independent from P0 safety and P1 retry controls.

P0 controls execution safety.
P1 controls retry policy.
P2 controls task evaluation.
P3 chooses the next action based on those signals.
