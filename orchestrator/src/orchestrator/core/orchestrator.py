"""Main project Orchestrator controller."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator.agents import CheckpointAgent, CriticAgent, PlannerAgent, ResearcherAgent, SynthesizerAgent
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
        self.executor = ChatGPTWebExecutor(
            use_mock=use_mock_executor,
            **(executor_options or {}),
        )
        self.agents = {
            "researcher": ResearcherAgent(),
            "critic": CriticAgent(),
            "synthesizer": SynthesizerAgent(),
            "checkpoint": CheckpointAgent(),
            "planner": PlannerAgent(),
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
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        if agent_name == "checkpoint":
            folder = project_path / "checkpoints"
        elif agent_name == "critic":
            folder = project_path / "critics"
        else:
            folder = project_path / "modules"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{agent_name}_{timestamp}.md"
        path.write_text(output.content, encoding="utf-8")
        outbox = project_path / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / "latest.md").write_text(output.content, encoding="utf-8")
        return path

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
        self._save_agent_output(project_id, agent_name, output)

        state = self.load_state(project_id)
        if result.limit_hit:
            state.status = "paused_due_to_limit"
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
            recent = "\n\n".join(
                f"### {name.title()}\n{output.content[:1500]}" for name, output in results.items()
            )
            checkpoint = self.create_checkpoint(project_id, recent)
            results["checkpoint"] = checkpoint
            if not checkpoint.success:
                return results

        state = self.load_state(project_id)
        state.status = "in_progress"
        state.notes = "Research pipeline completed successfully"
        self.save_state(project_id, state)
        return results
