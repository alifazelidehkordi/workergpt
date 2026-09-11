"""Base agent abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from orchestrator.core.models import AgentOutput, ExecuteRequest, ExecuteResult


class BaseAgent(ABC):
    name = "base"
    description = "Base agent"

    @abstractmethod
    def build_prompt(self, context: dict[str, Any]) -> str:
        raise NotImplementedError

    def create_request(self, context: dict[str, Any], files: list[Path] | None = None) -> ExecuteRequest:
        return ExecuteRequest(
            prompt=self.build_prompt(context),
            files=files or [],
            expected_output="text",
            metadata={
                "agent": self.name,
                "project_id": context.get("project_id"),
                "phase": context.get("current_phase"),
                "module": context.get("current_module"),
            },
        )

    def process_result(self, result: ExecuteResult, context: dict[str, Any]) -> AgentOutput:
        if not result.success:
            return AgentOutput(
                agent_name=self.name,
                success=False,
                content=result.error or "Unknown execution error",
                needs_review=True,
                metadata={"limit_hit": result.limit_hit, **result.metadata},
            )
        return AgentOutput(
            agent_name=self.name,
            success=True,
            content=result.text_response or "",
            metadata={
                "execution_time": result.execution_time,
                "downloaded_files": [str(path) for path in result.downloaded_files],
                "limit_hit": result.limit_hit,
                **result.metadata,
            },
        )
