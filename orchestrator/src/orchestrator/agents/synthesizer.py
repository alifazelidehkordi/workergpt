"""Synthesizer agent."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class SynthesizerAgent(BaseAgent):
    name = "synthesizer"
    description = "Turn research and critique into practical principles"

    def build_prompt(self, context: dict[str, Any]) -> str:
        return f"""Synthesize the research and critique into concise, evidence-backed practical principles. Be conservative when evidence conflicts or is weak.

Project definition:
{context.get('project_definition', '')}

Research:
{context.get('research_report', '')}

Critique:
{context.get('critique', '')}

Return: principle, evidence basis, mechanism, implementation, success conditions, failure conditions, measurement, confidence, and a short documentation-ready version."""
