"""Planner agent."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class PlannerAgent(BaseAgent):
    name = "planner"
    description = "Break a project into phases and modules"

    def build_prompt(self, context: dict[str, Any]) -> str:
        return f"""Plan the project into executable phases and modules. Respect dependencies, define measurable outputs, identify risks, and propose the first concrete actions.

Project definition:
{context.get('project_definition', '')}

Current state:
{context.get('current_state', '')}

Constraints:
{context.get('constraints', '')}

Return: project understanding, phases, modules, dependencies, priorities, risks, and first 1-3 actions."""
