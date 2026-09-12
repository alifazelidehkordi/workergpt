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
from orchestrator.core.failure_memory import FailureMemory, RetryDecision
from orchestrator.core.models import AgentOutput, ProjectState
from orchestrator.core.progress_guard import PersistentProgressGuard, ProgressDecision
from orchestrator.core.retry_policy import RetryAction, RetryPolicy, RetryPolicyDecision
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
        self.retry_policy = RetryPolicy()
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
        for sub in (
            "prompts",
            "agents",
            "modules",
            "critics",
            "checkpoints",
            "inbox",
            "outbox",
            "archive",
        ):
            (project_path / sub).mkdir(parents=True)
        created = datetime.now(UTC).strftime("%Y-%m-%d")
        (project_path / "project.toml").write_text(
            f'''[project]\nid = "{project_id}"\nname = "{name}"\ndescription = "{description}"\ncreated = "{created}"\nstatus = "active"\n''',
            encoding="utf-8",
        )
        (project_path / "definition.md").write_text(
            f"# {name}\n\n{description}\n",
            encoding="utf-8",
        )
        ProjectState(
            project_id=project_id,
            current_phase="phase_0_definition",
            progress={
                "phases_completed": [],
                "modules_completed": [],
                "pending": ["phase_0_definition"],
            },
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

    def _save_agent_output(
        self,
        project_id: str,
        agent_name: str,
        output: AgentOutput,
    ) -> Path:
        project_path = self.projects_dir / project_id
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        folder = project_path / (
            "checkpoints"
            if agent_name == "checkpoint"
            else "critics"
            if agent_name == "critic"
            else "modules"
        )
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
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def _execution_guard_context(
        self,
        project_id: str,
        state: ProjectState,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return stable semantic input for exact duplicate detection."""
        semantic_progress = {
            key: value
            for key, value in state.progress.items()
            if not key.startswith("p0_")
            and not key.startswith("p1_")
            and key != "resume_pending"
        }
        return {
            "project_id": project_id,
            "project_definition": self.get_definition(project_id),
            "current_phase": state.current_phase,
            "current_module": state.current_module,
            "progress": semantic_progress,
            "input": context or {},
        }

    @staticmethod
    def _progress_guard_context(
        project_id: str,
        state: ProjectState,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return a stable action scope for logical-loop detection."""
        return {
            **(context or {}),
            "project_id": project_id,
            "current_phase": state.current_phase,
            "current_module": state.current_module,
        }

    @staticmethod
    def _failure_context(
        project_id: str,
        state: ProjectState,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return stable P1 failure scope while preserving explicit strategy id."""
        value = {
            **(context or {}),
            "project_id": project_id,
            "current_phase": state.current_phase,
            "current_module": state.current_module,
        }
        if value.get("_workflow_stage") and not value.get("workflow_stage"):
            value["workflow_stage"] = value["_workflow_stage"]
        return value

    def _block_for_stagnation(
        self,
        project_id: str,
        state: ProjectState,
        agent_name: str,
        decision: ProgressDecision,
        *,
        last_output: str | None = None,
    ) -> AgentOutput:
        state.active_agent = None
        state.status = "paused_no_progress"
        state.progress["p0_stop"] = {
            "agent": agent_name,
            "reason": "no_measurable_progress",
            "stagnant_count": decision.stagnant_count,
            "scope_key": decision.key,
            "last_output": last_output,
            "at": datetime.now(UTC).isoformat(),
        }
        state.notes = (
            f"P0 stopped {agent_name}: no measurable progress after "
            f"{decision.stagnant_count} stagnant attempts"
        )
        self.save_state(project_id, state)
        return AgentOutput(
            agent_name=agent_name,
            success=False,
            content="Execution stopped by P0 stagnation guard: no_measurable_progress",
            needs_review=True,
            metadata={
                "blocked_by": "p0_stagnation_guard",
                "reason": "no_measurable_progress",
                "stagnant_count": decision.stagnant_count,
                "scope_key": decision.key,
                "last_output": last_output,
            },
        )

    def _block_for_retry_policy(
        self,
        project_id: str,
        state: ProjectState,
        agent_name: str,
        memory_decision: RetryDecision,
        policy_decision: RetryPolicyDecision,
    ) -> AgentOutput:
        if policy_decision.action == RetryAction.CHANGE_STRATEGY:
            status = "paused_strategy_required"
            instruction = "Change _strategy_id before retrying."
        elif policy_decision.action == RetryAction.OPERATOR_ACTION:
            status = "paused_operator_action"
            instruction = "Operator action is required before retrying."
        else:
            status = "paused_terminal"
            instruction = "Do not retry this failure automatically."

        state.active_agent = None
        state.status = status
        state.progress["p1_stop"] = {
            "agent": agent_name,
            "reason": policy_decision.reason,
            "action": policy_decision.action.value,
            "category": memory_decision.category,
            "signature": memory_decision.signature,
            "occurrence": memory_decision.occurrence,
            "scope_key": memory_decision.scope_key,
            "strategy_change_required": policy_decision.strategy_change_required,
            "operator_action_required": policy_decision.operator_action_required,
            "previous_strategy_id": memory_decision.previous_strategy_id,
            "at": datetime.now(UTC).isoformat(),
        }
        state.notes = (
            f"P1 blocked {agent_name}: {policy_decision.action.value}; "
            f"reason={policy_decision.reason}; failure={memory_decision.category}"
        )
        self.save_state(project_id, state)
        return AgentOutput(
            agent_name=agent_name,
            success=False,
            content=(
                "Execution blocked by P1 retry policy: "
                f"{policy_decision.reason}. {instruction}"
            ),
            needs_review=True,
            metadata={
                "blocked_by": "p1_retry_policy",
                "reason": policy_decision.reason,
                "retry_policy": policy_decision.to_dict(),
                "failure_category": memory_decision.category,
                "failure_signature": memory_decision.signature,
                "occurrence": memory_decision.occurrence,
                "previous_strategy_id": memory_decision.previous_strategy_id,
                "scope_key": memory_decision.scope_key,
            },
        )

    def _apply_failure_policy_state(
        self,
        state: ProjectState,
        agent_name: str,
        raw_output: AgentOutput,
        policy_decision: RetryPolicyDecision,
    ) -> None:
        state.progress["last_failure"] = raw_output.metadata.get("failure", {})
        state.progress["p1_retry_policy"] = policy_decision.to_dict()

        if policy_decision.action == RetryAction.BACKOFF:
            state.status = "paused_backoff"
            state.notes = (
                f"P1 backoff for {agent_name}: wait {policy_decision.delay_seconds}s "
                f"before retry; reason={policy_decision.reason}"
            )
        elif policy_decision.action == RetryAction.RETRY:
            state.status = "paused_retryable"
            state.notes = (
                f"P1 retry allowed for {agent_name}: {policy_decision.reason}"
            )
        elif policy_decision.action == RetryAction.CHANGE_STRATEGY:
            state.status = "paused_strategy_required"
            state.notes = (
                f"P1 requires strategy change for {agent_name}: "
                f"{policy_decision.reason}"
            )
        elif policy_decision.action == RetryAction.OPERATOR_ACTION:
            state.status = "paused_operator_action"
            state.notes = (
                f"P1 requires operator action for {agent_name}: "
                f"{policy_decision.reason}"
            )
        else:
            state.status = "paused_terminal"
            state.notes = f"P1 stopped {agent_name}: {policy_decision.reason}"

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
        checkpoint_dir = self.projects_dir / project_id / "checkpoints"
        progress_context = self._progress_guard_context(project_id, state, context)
        progress_guard = PersistentProgressGuard(checkpoint_dir / "progress_state.json")

        prior_progress = progress_guard.check(agent_name, progress_context)
        if prior_progress.should_stop:
            return self._block_for_stagnation(
                project_id,
                state,
                agent_name,
                prior_progress,
            )

        failure_context = self._failure_context(project_id, state, context)
        failure_memory = FailureMemory(checkpoint_dir / "failure_memory.json")
        memory_decision = failure_memory.check_retry(agent_name, failure_context)
        preflight_policy = self.retry_policy.before_retry(memory_decision)
        if not preflight_policy.allowed:
            return self._block_for_retry_policy(
                project_id,
                state,
                agent_name,
                memory_decision,
                preflight_policy,
            )

        guard_context = self._execution_guard_context(project_id, state, context)
        gate = ExecutionGate(checkpoint_dir / "request_cache.json")
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
        state.progress["p1_preflight"] = preflight_policy.to_dict()
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
        raw_output = agent.process_result(result, full_context)

        progress_decision: ProgressDecision | None = None
        if not result.limit_hit:
            progress_decision = progress_guard.record_result(
                agent_name,
                progress_context,
                raw_output,
            )

        failure_policy: RetryPolicyDecision | None = None
        if raw_output.success:
            # Exact successful work is idempotent and belongs in P0's duplicate cache.
            # Failed work is deliberately not cached: P1 owns bounded retry decisions.
            gate.record(agent_name, guard_context, files, result="success")
            failure_memory.resolve(agent_name, failure_context)
            raw_output.metadata["p1_failure_state"] = "resolved"
            raw_output.metadata["retry_policy"] = {
                "action": "none",
                "allowed": True,
                "reason": "success",
            }
        else:
            classification, active_failure = failure_memory.record_failure(
                agent_name,
                failure_context,
                result.error or raw_output.content,
                limit_hit=result.limit_hit,
                metadata=result.metadata,
            )
            failure_policy = self.retry_policy.after_failure(
                classification,
                same_strategy_count=active_failure["same_strategy_count"],
            )
            raw_output.metadata["failure"] = {
                "category": classification.category,
                "signature": classification.signature,
                "retryable": classification.retryable,
                "transient": classification.transient,
                "strategy_change_on_repeat": classification.strategy_change_on_repeat,
                "same_strategy_count": active_failure["same_strategy_count"],
                "consecutive_count": active_failure["consecutive_count"],
                "strategy_id": active_failure["strategy_id"],
            }
            raw_output.metadata["retry_policy"] = failure_policy.to_dict()

        saved_path = self._save_agent_output(project_id, agent_name, raw_output)
        saved_relative = str(saved_path.relative_to(self.projects_dir / project_id))

        if progress_decision is not None and progress_decision.should_stop:
            state = self.load_state(project_id)
            return self._block_for_stagnation(
                project_id,
                state,
                agent_name,
                progress_decision,
                last_output=saved_relative,
            )

        state = self.load_state(project_id)
        if result.limit_hit:
            resume_path = self._write_resume_checkpoint(
                project_id,
                agent_name,
                context or {},
                files,
                result.error or "ChatGPT usage/rate limit",
            )
            state.last_checkpoint = str(
                resume_path.relative_to(self.projects_dir / project_id)
            )
            state.progress["resume_pending"] = True
            if failure_policy is not None:
                self._apply_failure_policy_state(
                    state,
                    agent_name,
                    raw_output,
                    failure_policy,
                )
            state.notes += f"; resume checkpoint={resume_path.name}"
        elif raw_output.success:
            state.status = "in_progress"
            state.notes = f"Last successful agent: {agent_name}"
            state.progress.pop("p1_stop", None)
            state.progress.pop("p1_retry_policy", None)
            state.progress.pop("last_failure", None)
            if progress_decision and progress_decision.measurable_progress:
                state.progress.pop("p0_stop", None)
        else:
            if failure_policy is None:
                raise RuntimeError("Failed execution did not produce a retry policy decision")
            self._apply_failure_policy_state(
                state,
                agent_name,
                raw_output,
                failure_policy,
            )

        state.active_agent = None
        self.save_state(project_id, state)
        return raw_output

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
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            state = self.load_state(project_id)
            state.progress["resume_pending"] = False
            state.status = "in_progress"
            state.notes = f"Resumed successfully from {path.name}"
            self.save_state(project_id, state)
        return output

    def research(
        self,
        project_id: str,
        question: str,
        goal: str | None = None,
    ) -> AgentOutput:
        return self.run_agent(
            project_id,
            "researcher",
            {
                "research_question": question,
                "goal": goal or "Rigorous research",
            },
        )

    def critique(self, project_id: str, content: str) -> AgentOutput:
        return self.run_agent(
            project_id,
            "critic",
            {"content_to_critique": content},
        )

    def synthesize(
        self,
        project_id: str,
        research_report: str,
        critique: str = "",
    ) -> AgentOutput:
        return self.run_agent(
            project_id,
            "synthesizer",
            {
                "research_report": research_report,
                "critique": critique,
            },
        )

    def create_checkpoint(
        self,
        project_id: str,
        recent_outputs: str = "",
    ) -> AgentOutput:
        output = self.run_agent(
            project_id,
            "checkpoint",
            {"recent_outputs": recent_outputs},
        )
        if output.success:
            state = self.load_state(project_id)
            state.last_checkpoint = datetime.now(UTC).isoformat()
            self.save_state(project_id, state)
        return output

    def plan(self, project_id: str, constraints: str = "") -> AgentOutput:
        return self.run_agent(
            project_id,
            "planner",
            {"constraints": constraints},
        )

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
            synthesis = self.synthesize(
                project_id,
                research.content,
                critique_text,
            )
            results["synthesizer"] = synthesis
            if not synthesis.success:
                return results

        if run_checkpoint:
            recent = "\n\n".join(
                f"### {name.title()}\n{output.content[:1500]}"
                for name, output in results.items()
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
