"""Critic agent."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class CriticAgent(BaseAgent):
    name = "critic"
    description = "Evidence and reasoning critic"

    def build_prompt(self, context: dict[str, Any]) -> str:
        return f"""Act as a strict scientific critic. Identify unsupported claims, weak evidence, bias, practical risks, and concrete corrections. Mark anything that cannot be evaluated.

Project definition:
{context.get('project_definition', '')}

Content to critique:
{context.get('content_to_critique', '')}

Return an overall evidence score, unsupported claims, limitations, biases, risks, corrections, reliable parts, and readiness status."""
