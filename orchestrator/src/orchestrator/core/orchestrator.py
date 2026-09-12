"""Main project Orchestrator controller."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator.agents import (
    CheckpointAgent,
    CriticAgent,
    PlannerAgent,
    ResearchFileCriticAgent,
    ResearchSectionAgent,
    ResearchSectionCriticAgent,
    ResearchSectionRepairAgent,
    ResearchTopicPlannerAgent,
    ResearcherAgent,
    SynthesizerAgent,
)
from orchestrator.core.execution_gate import ExecutionGate
from orchestrator.core.models import AgentOutput, ProjectState
from orchestrator.tools.chatgpt_web import ChatGPTWebExecutor


class Orchestrator:
    def __init__(
        self,
        root_dir: Path,
        use_mock_executor: bool = True,
        executor_options: dict[str, Any] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.projects_dir = self.root_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.executor = ChatGPTWebExecutor(use_mock=use_mock_executor, **(executor_options or {}))
        self.agents = {
            "researcher": ResearcherAgent(),
            "critic": CriticAgent(),
            "synthesizer": SynthesizerAgent(),
            "checkpoint": CheckpointAgent(),
            "planner": PlannerAgent(),
            "research_section": ResearchSectionAgent(),
            "research_section_critic": ResearchSectionCriticAgent(),
            "research_section_repair": ResearchSectionRepairAgent(),
            "research_file_critic": ResearchFileCriticAgent(),
            "research_topic_planner": ResearchTopicPlannerAgent(),
        }

    def close(self) -> None:
        self.executor.close()

    def create_project(self, project_id: str, name: str, description: str = "") -> Path:
        project_path = self.projects_dir / project_id
        if project_path.exists():
            raise FileExistsError(f"Project '{project_id}' already exists")
        for sub in ("prompts", "agents", "modules", "critics", "checkpoints", "inbox", "outbox", "archive"):
            (project_path / sub).mkdir(parents=True)
        created = datetime.now(UTC).strftime("%Y-%m-%d")
        (project_path / "project.toml").write_text(
            f'''[project]\nid = "{project_id}"\nname = "{name}"\ndescription = "{description}"\ncreated = "{created}"\nstatus = "active"\n''',
            encoding="utf-8",
        )
        (project_path / "definition.md").write_text(f"# {name}\n\n{description}\n", encoding="utf-8")
        ProjectState(
            project_id=project_id,
            current_phase="phase_0_definition",
            progress={"phases_completed": [], "modules_completed": [], "pending": ["phase_0_definition"]},
            notes="Project created",
        ).save(project_path / "state.json")
        return project_path

    def load_state(self, project_id: str) -> ProjectState:
        return ProjectState.load(self.projects_dir / project_id / "state.json")

    def save_state(self, project_id: str, state: ProjectState) -> None:
        state.last_updated = datetime.now(UTC).isoformat()
        state.save(self.projects_dir / project_id / "state.json")

    def get_definition(self, project_id: str) -> str:
        path = self.projects_dir / project_id / "definition.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _save_agent_output(self, project_id: str, agent_name: str, output: AgentOutput) -> Path:
        project_path = self.projects_dir / project_id
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        folder = project_path / ("checkpoints" if agent_name == "checkpoint" else "critics" if agent_name == "critic" else "modules")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{agent_name}_{timestamp}.md"
        path.write_text(output.content, encoding="utf-8")
        outbox = project_path / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / "latest.md").write_text(output.content, encoding="utf-8")
        return path

    def _write_resume_checkpoint(
        self,
        project_id: str,
        agent_name: str,
        context: dict[str, Any],
        files: list[Path] | None,
        error: str,
    ) -> Path:
        project_path = self.projects_dir / project_id
        checkpoint_dir = project_path / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        path = checkpoint_dir / f"resume_{timestamp}.json"
        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "project_id": project_id,
            "agent_name": agent_name,
            "context": context,
            "files": [str(Path(item)) for item in (files or [])],
            "error": error,
            "completed": False,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _execution_guard_context(
        self,
        project_id: str,
        state: ProjectState,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return stable semantic input for duplicate detection.

        Volatile values such as timestamps, active_agent, notes, and executor
        metadata are deliberately excluded. Meaningful workflow progress is
        included so the same agent/input can run again after state advances.
        """
        return {
            "project_id": project_id,
            "project_definition": self.get_definition(project_id),
            "current_phase": state.current_phase,
            "current_module": state.current_module,
            "progress": state.progress,
            "input": context or {},
        }

    def run_agent(
        self,
        project_id: str,
        agent_name: str,
        context: dict[str, Any] | None = None,
        files: list[Path] | None = None,
    ) -> AgentOutput:
        if agent_name not in self.agents:
            raise ValueError(f"Unknown agent: {agent_name}")

        state = self.load_state(project_id)
        guard_context = self._execution_guard_context(project_id, state, context)
        gate = ExecutionGate(self.projects_dir / project_id / "checkpoints" / "request_cache.json")
        allowed, gate_reason = gate.allow(agent_name, guard_context, files)
        if not allowed:
            state.active_agent = None
            state.status = "paused"
            state.progress["p0_blocked"] = {
                "agent": agent_name,
                "reason": gate_reason,
                "at": datetime.now(UTC).isoformat(),
            }
            state.notes = f"P0 blocked {agent_name}: {gate_reason}"
            self.save_state(project_id, state)
            return AgentOutput(
                agent_name=agent_name,
                success=False,
                content=f"Execution blocked by P0 safety gate: {gate_reason}",
                needs_review=True,
                metadata={
                    "blocked_by": "p0_execution_gate",
                    "reason": gate_reason,
                },
            )

        state.active_agent = agent_name
        self.save_state(project_id, state)
        full_context = {
            "project_id": project_id,
            "project_definition": self.get_definition(project_id),
            "current_state": json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            "current_phase": state.current_phase,
            "current_module": state.current_module,
            "date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        }
        if context:
            full_context.update(context)
        agent = self.agents[agent_name]
        result = self.executor.execute(agent.create_request(full_context, files=files))
        output = agent.process_result(result, full_context)

        # Rate-limit interruptions are intentionally not cached so resume_last()
        # may replay the same logical request. Other completed attempts are
        # recorded before any future identical action can reach the executor.
        if not result.limit_hit:
            gate.record(
                agent_name,
                guard_context,
                files,
                result="success" if output.success else "failed",
            )

        self._save_agent_output(project_id, agent_name, output)

        state = self.load_state(project_id)
        if result.limit_hit:
            resume_path = self._write_resume_checkpoint(
                project_id,
                agent_name,
                context or {},
                files,
                result.error or "ChatGPT usage/rate limit",
            )
            state.status = "paused_due_to_limit"
            state.last_checkpoint = str(resume_path.relative_to(self.projects_dir / project_id))
            state.progress["resume_pending"] = True
            state.notes = f"Paused during {agent_name}: ChatGPT usage/rate limit"
        elif output.success:
            state.status = "in_progress"
            state.notes = f"Last successful agent: {agent_name}"
        else:
            state.status = "paused"
            state.notes = f"Agent failed: {agent_name}: {output.content[:300]}"
        state.active_agent = None
        self.save_state(project_id, state)
        return output

    def resume_last(self, project_id: str) -> AgentOutput:
        checkpoint_dir = self.projects_dir / project_id / "checkpoints"
        candidates = sorted(checkpoint_dir.glob("resume_*.json"), reverse=True)
        if not candidates:
            raise FileNotFoundError("No resumable rate-limit checkpoint found")
        path = candidates[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("completed"):
            raise RuntimeError("Latest resume checkpoint is already completed")
        output = self.run_agent(
            project_id,
            str(payload["agent_name"]),
            context=dict(payload.get("context") or {}),
            files=[Path(item) for item in payload.get("files", [])],
        )
        if output.success:
            payload["completed"] = True
            payload["completed_at"] = datetime.now(UTC).isoformat()
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            state = self.load_state(project_id)
            state.progress["resume_pending"] = False
            state.status = "in_progress"
            state.notes = f"Resumed successfully from {path.name}"
            self.save_state(project_id, state)
        return output

    def research(self, project_id: str, question: str, goal: str | None = None) -> AgentOutput:
        return self.run_agent(project_id, "researcher", {"research_question": question, "goal": goal or "Rigorous research"})

    def critique(self, project_id: str, content: str) -> AgentOutput:
        return self.run_agent(project_id, "critic", {"content_to_critique": content})

    def synthesize(self, project_id: str, research_report: str, critique: str = "") -> AgentOutput:
        return self.run_agent(project_id, "synthesizer", {"research_report": research_report, "critique": critique})

    def create_checkpoint(self, project_id: str, recent_outputs: str = "") -> AgentOutput:
        output = self.run_agent(project_id, "checkpoint", {"recent_outputs": recent_outputs})
        if output.success:
            state = self.load_state(project_id)
            state.last_checkpoint = datetime.now(UTC).isoformat()
            self.save_state(project_id, state)
        return output

    def plan(self, project_id: str, constraints: str = "") -> AgentOutput:
        return self.run_agent(project_id, "planner", {"constraints": constraints})

    def run_research_pipeline(
        self,
        project_id: str,
        question: str,
        goal: str | None = None,
        run_critic: bool = True,
        run_synthesizer: bool = True,
        run_checkpoint: bool = True,
    ) -> dict[str, AgentOutput]:
        results: dict[str, AgentOutput] = {}
        research = self.research(project_id, question, goal)
        results["researcher"] = research
        if not research.success:
            return results

        critique_text = ""
        if run_critic:
            critique = self.critique(project_id, research.content)
            results["critic"] = critique
            if not critique.success:
                return results
            critique_text = critique.content

        if run_synthesizer:
            synthesis = self.synthesize(project_id, research.content, critique_text)
            results["synthesizer"] = synthesis
            if not synthesis.success:
                return results

        if run_checkpoint:
            recent = "\n\n".join(f"### {name.title()}\n{output.content[:1500]}" for name, output in results.items())
            checkpoint = self.create_checkpoint(project_id, recent)
            results["checkpoint"] = checkpoint
            if not checkpoint.success:
                return results

        state = self.load_state(project_id)
        state.status = "in_progress"
        state.notes = "Research pipeline completed successfully"
        self.save_state(project_id, state)
        return results
