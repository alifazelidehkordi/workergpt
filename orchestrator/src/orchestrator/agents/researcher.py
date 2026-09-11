"""Researcher agent."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import BaseAgent


class ResearcherAgent(BaseAgent):
    name = "researcher"
    description = "Deep evidence-oriented research"

    def build_prompt(self, context: dict[str, Any]) -> str:
        return f"""You are a rigorous research agent. Use reliable evidence, state uncertainty, and do not invent sources.

Project definition:
{context.get('project_definition', '')}

Current state:
{context.get('current_state', '')}

Goal:
{context.get('goal', 'Rigorous research')}

Research question:
{context.get('research_question', '')}

Return: executive summary, key concepts, important evidence, mechanisms, limitations, knowledge gaps, practical implications, references, confidence score, and a short research checkpoint."""
