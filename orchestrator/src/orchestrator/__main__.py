"""Command-line interface for WorkerGPT Orchestrator."""

from __future__ import annotations

import argparse
from pathlib import Path

from orchestrator.core.orchestrator import Orchestrator
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
        else:
            parser.print_help()
    finally:
        orch.close()


if __name__ == "__main__":
    main()
