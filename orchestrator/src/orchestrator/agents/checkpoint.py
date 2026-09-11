"""Checkpoint agent."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class CheckpointAgent(BaseAgent):
    name = "checkpoint"
    description = "Create a resumable project checkpoint"

    def build_prompt(self, context: dict[str, Any]) -> str:
        return f"""Create a compact but complete project checkpoint that another agent can use to resume work without hidden context.

Project definition:
{context.get('project_definition', '')}

Current state:
{context.get('current_state', '')}

Recent outputs:
{context.get('recent_outputs', '')}

Return: objective, current phase/module, completed work, decisions, key outputs, open issues, exact next step, continuation notes, and completeness level."""
