"""Command-line interface for WorkerGPT Orchestrator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orchestrator.core.orchestrator import Orchestrator
from orchestrator.research_workflow import ResearchWorkflow
from orchestrator.tools.browser_runtime import open_login_session


def _load_text(value: str) -> str:
    path = Path(value)
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="WorkerGPT Project Orchestrator")
    parser.add_argument("--real", action="store_true", help="Use ChatGPT web through Patchright")
    parser.add_argument("--profile", default="default", help="Persistent browser profile name")
    parser.add_argument("--profile-dir", type=Path, default=None, help="Override persistent browser profile directory")
    parser.add_argument("--download-dir", type=Path, default=None, help="Browser download directory")
    parser.add_argument("--headless", action="store_true", help="Run the real browser headlessly")
    parser.add_argument("--worker-id", default=None, help="Stable label for a parallel workflow worker")
    sub = parser.add_subparsers(dest="command")

    login = sub.add_parser("login", help="Open a persistent browser and log in to ChatGPT")
    login.add_argument("--profile", dest="login_profile", default=None)
    login.add_argument("--profile-dir", dest="login_profile_dir", type=Path, default=None)

    create = sub.add_parser("create", help="Create a project")
    create.add_argument("project_id")
    create.add_argument("--name", required=True)
    create.add_argument("--description", default="")

    research = sub.add_parser("research", help="Run Researcher")
    research.add_argument("project_id")
    research.add_argument("--question", required=True)
    research.add_argument("--goal", default=None)

    critique = sub.add_parser("critique", help="Run Critic")
    critique.add_argument("project_id")
    critique.add_argument("--content", required=True)

    synth = sub.add_parser("synthesize", help="Run Synthesizer")
    synth.add_argument("project_id")
    synth.add_argument("--research", required=True)
    synth.add_argument("--critique", default="")

    checkpoint = sub.add_parser("checkpoint", help="Create checkpoint")
    checkpoint.add_argument("project_id")

    plan = sub.add_parser("plan", help="Run Planner")
    plan.add_argument("project_id")
    plan.add_argument("--constraints", default="")

    pipeline = sub.add_parser("pipeline", help="Run Researcher -> Critic -> Synthesizer -> Checkpoint")
    pipeline.add_argument("project_id")
    pipeline.add_argument("--question", required=True)
    pipeline.add_argument("--goal", default=None)
    pipeline.add_argument("--skip-critic", action="store_true")
    pipeline.add_argument("--skip-synthesizer", action="store_true")
    pipeline.add_argument("--skip-checkpoint", action="store_true")

    resume = sub.add_parser("resume", help="Retry the latest rate-limit checkpoint")
    resume.add_argument("project_id")

    status = sub.add_parser("status", help="Show project status")
    status.add_argument("project_id")

    workflow = sub.add_parser("workflow", help="Manage the durable section-by-section research workflow")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)

    workflow_init = workflow_sub.add_parser("init", help="Index research topics and create durable workflow state")
    workflow_init.add_argument("project_id")
    workflow_init.add_argument("--vault", type=Path, required=True)
    workflow_init.add_argument("--report", type=Path, default=None)
    workflow_init.add_argument("--expected-topics", type=int, default=70, help="Expected unique topic count; use 0 to disable")

    workflow_status = workflow_sub.add_parser("status", help="Show durable research workflow status")
    workflow_status.add_argument("project_id")

    workflow_run = workflow_sub.add_parser("run", help="Run or resume one topic")
    workflow_run.add_argument("project_id")
    workflow_run.add_argument("--topic", help="Topic id (for example KSR-10); defaults to the next incomplete topic")
    workflow_run.add_argument("--max-sections", type=int, default=None, help="Stop after this many newly approved sections")
    workflow_run.add_argument("--max-revisions", type=int, default=1, help="Maximum targeted final-audit repair rounds")
    workflow_run.add_argument("--max-section-revisions", type=int, default=1, help="Maximum fresh researcher retries per section")

    args = parser.parse_args()

    if args.command == "login":
        session = open_login_session(
            profile=args.login_profile or args.profile,
            profile_dir=args.login_profile_dir or args.profile_dir,
        )
        try:
            print(f"Browser profile: {session.options.profile_dir}")
            print("Log in to ChatGPT in the opened browser, then press Enter here.")
            input()
            if not session.is_authenticated():
                raise SystemExit("ChatGPT login could not be verified. Finish login and retry.")
            print("Login verified. Persistent profile is ready.")
        finally:
            session.close()
        return

    root = Path(__file__).resolve().parents[2]
    orch = Orchestrator(
        root,
        use_mock_executor=not args.real,
        executor_options={
            "profile": args.profile,
            "profile_dir": args.profile_dir,
            "download_dir": args.download_dir,
            "headless": args.headless,
        },
    )
    try:
        if args.command == "create":
            print(orch.create_project(args.project_id, args.name, args.description))
        elif args.command == "research":
            print(orch.research(args.project_id, args.question, args.goal).content)
        elif args.command == "critique":
            print(orch.critique(args.project_id, args.content).content)
        elif args.command == "synthesize":
            print(orch.synthesize(args.project_id, _load_text(args.research), _load_text(args.critique) if args.critique else "").content)
        elif args.command == "checkpoint":
            print(orch.create_checkpoint(args.project_id).content)
        elif args.command == "plan":
            print(orch.plan(args.project_id, args.constraints).content)
        elif args.command == "pipeline":
            results = orch.run_research_pipeline(
                args.project_id,
                args.question,
                args.goal,
                run_critic=not args.skip_critic,
                run_synthesizer=not args.skip_synthesizer,
                run_checkpoint=not args.skip_checkpoint,
            )
            for name, output in results.items():
                print(f"{name}: {'OK' if output.success else 'FAILED'}")
        elif args.command == "resume":
            output = orch.resume_last(args.project_id)
            print(f"{output.agent_name}: {'OK' if output.success else 'FAILED'}")
            print(output.content)
        elif args.command == "status":
            state = orch.load_state(args.project_id)
            print(f"Project: {state.project_id}")
            print(f"Status: {state.status}")
            print(f"Phase: {state.current_phase}")
            print(f"Module: {state.current_module}")
            print(f"Updated: {state.last_updated}")
            print(f"Notes: {state.notes}")
        elif args.command == "workflow":
            research_workflow = ResearchWorkflow(orch, args.project_id, worker_id=args.worker_id)
            if args.workflow_command == "init":
                expected = None if args.expected_topics == 0 else args.expected_topics
                state = research_workflow.initialize(args.vault, args.report, expected_topics=expected)
                print(json.dumps({"state_file": str(research_workflow.state_path), "summary": research_workflow.summary(), "status": state["status"]}, ensure_ascii=False, indent=2))
            elif args.workflow_command == "status":
                state = research_workflow.load()
                print(json.dumps({"status": state["status"], "active": state.get("active"), "summary": research_workflow.summary()}, ensure_ascii=False, indent=2))
            elif args.workflow_command == "run":
                topic = args.topic or research_workflow.claim_next_topic()
                if topic is None:
                    print(json.dumps({"status": "complete", "message": "All topics are complete"}, ensure_ascii=False, indent=2))
                else:
                    result = research_workflow.run_topic(
                        topic,
                        max_sections=args.max_sections,
                        max_revisions=args.max_revisions,
                        max_section_revisions=args.max_section_revisions,
                    )
                    print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            parser.print_help()
    finally:
        orch.close()


if __name__ == "__main__":
    main()
