import json
import sys
from pathlib import Path

import pytest

from orchestrator.core.models import AgentOutput
from orchestrator.agents.research_section import ResearchSectionAgent, ResearchSectionCriticAgent
from orchestrator.research_workflow import ResearchWorkflow, SECTIONS, _parse_final_audit, _parse_section_review, _sha256


def _topic(topic_id: str = "KSR-1", title: str = "آزمون") -> str:
    return f"""---
research_id: {topic_id}
title: {title}
status: pending
updated: 2026-01-01
---
# {title}

**در انتظار پژوهش**

## ۱. آیا چنین مفهومی در تحقیقات علمی وجود دارد؟

- [ ] TODO
"""


def _section_markdown(section_id: str) -> str:
    spec = next(item for item in SECTIONS if item.id == section_id)
    extra = ""
    if section_id == "04_evidence":
        extra = "\n| منبع | نتیجه |\n|---|---|\n| مطالعه | محدود |\n"
    if section_id == "06_practice":
        extra += "\n## اصول عملی قابل استفاده در سیستم کینتسوگی\n\n- اصل آزمون‌پذیر\n"
    return f"{spec.title}\n\n[1] " + ("شاهد محدود و نیازمند احتیاط پژوهش روش معتبر " * 90) + f"\n\n[1] https://doi.org/10.1000/{section_id}\n{extra}"


def _section_review(section_id: str, verdict: str = "pass") -> str:
    payload = {
        "verdict": verdict,
        "issues": [] if verdict == "pass" else [{
            "id": "source-1",
            "severity": "major",
            "location": "منابع",
            "problem": "منبع تأیید نشده",
            "required_change": "منبع معتبر جایگزین شود",
        }],
        "source_checks": [],
    }
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def _audit(verdict: str = "pass", repairs: dict | None = None) -> str:
    issues = []
    if verdict == "revise":
        section_ids = list((repairs or {"04_evidence": []}).keys())
        issues = [{
            "section_ids": section_ids,
            "severity": "major",
            "problem": "بخش نیازمند اصلاح است",
            "required_change": next(iter((repairs or {"x": ["اصلاح"]}).values()))[0],
        }]
    payload = {
        "verdict": verdict,
        "issues": issues,
        "summary": "ممیزی کامل",
    }
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


class FakeOrchestrator:
    def __init__(self, root: Path, section_verdict: str = "pass", audits: list[str] | None = None):
        self.projects_dir = root / "projects"
        self.section_verdict = section_verdict
        self.audits = list(audits or [_audit()])
        self.section_verdicts = []
        self.calls = []

    def run_agent(self, project_id, agent_name, context):
        self.calls.append((agent_name, context))
        if agent_name == "research_topic_planner":
            plan = {item.id: {"questions": ["پرسش"], "search_queries": ["query"], "exclude": ["وبلاگ"]} for item in SECTIONS}
            content = "```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```"
        elif agent_name == "research_section":
            section = next(item.id for item in SECTIONS if item.title == context["section_title"])
            content = f"```markdown\n{_section_markdown(section)}\n```"
        elif agent_name == "research_section_critic":
            section = next(item.id for item in SECTIONS if item.title == context["section_title"])
            verdict = self.section_verdicts.pop(0) if self.section_verdicts else self.section_verdict
            content = _section_review(section, verdict)
        else:
            content = self.audits.pop(0)
        return AgentOutput(agent_name=agent_name, success=True, content=content)


@pytest.fixture
def workflow_env(tmp_path):
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "state.json").write_text("{}", encoding="utf-8")
    vault = tmp_path / "vault"
    topics = vault / "موضوعات"
    topics.mkdir(parents=True)
    topic_path = topics / "one.md"
    topic_path.write_text(_topic(), encoding="utf-8")
    fake = FakeOrchestrator(tmp_path)
    workflow = ResearchWorkflow(fake, "demo")
    workflow.initialize(vault, expected_topics=None)
    return workflow, fake, topic_path


def test_complete_run_preserves_reviews_and_commits_after_audit(workflow_env):
    workflow, fake, topic_path = workflow_env

    result = workflow.run_topic("۱")

    assert result["status"] == "complete"
    assert "status: complete" in topic_path.read_text(encoding="utf-8")
    state = workflow.load()
    topic = state["topics"]["KSR-1"]
    assert topic["commit"]["status"] == "committed"
    assert topic["source_hash"] == _sha256(topic_path.read_text(encoding="utf-8"))
    assert len(list((workflow.artifacts_dir / "KSR-1").glob("*/critic_review.json"))) == 6
    assert [name for name, _ in fake.calls].count("research_file_critic") == 1
    assert [name for name, _ in fake.calls].count("research_topic_planner") == 1
    web_calls = [context for name, context in fake.calls if name in {"research_section", "research_section_critic", "research_file_critic"}]
    assert web_calls and all(context["_web_search"] is True for context in web_calls)
    section_calls = [context for name, context in fake.calls if name in {"research_section", "research_section_critic"}]
    assert all("topic_context" not in context for context in section_calls)


def test_unresolved_section_is_not_approved(workflow_env):
    workflow, fake, topic_path = workflow_env
    fake.section_verdict = "revise"
    before = topic_path.read_text(encoding="utf-8")

    result = workflow.run_topic("KSR-1", max_section_revisions=0)

    assert result["status"] == "needs_review"
    assert topic_path.read_text(encoding="utf-8") == before
    section_dir = workflow.artifacts_dir / "KSR-1" / "01_construct"
    assert (section_dir / "critic_review.json").exists()
    assert not (section_dir / "approved.md").exists()


def test_existing_needs_review_attempt_resumes_with_fresh_research(workflow_env):
    workflow, fake, _ = workflow_env
    fake.section_verdict = "revise"
    first = workflow.run_topic("KSR-1", max_section_revisions=0)
    assert first["status"] == "needs_review"

    fake.section_verdict = "pass"
    resumed = workflow.run_topic("KSR-1", max_section_revisions=1)

    assert resumed["status"] == "complete"
    revision_dir = workflow.artifacts_dir / "KSR-1" / "01_construct" / "revisions" / "section-attempt-1"
    assert (revision_dir / "critic_response.txt").exists()
    assert (revision_dir / "draft.md").exists()


def test_final_revise_triggers_one_targeted_research_round(workflow_env):
    workflow, fake, _ = workflow_env
    fake.audits = [_audit("revise", {"04_evidence": ["جدول را اصلاح کن"]}), _audit()]

    result = workflow.run_topic("KSR-1", max_revisions=1)

    assert result["status"] == "complete"
    research_calls = [context for name, context in fake.calls if name == "research_section"]
    assert len(research_calls) == 7
    assert research_calls[-1]["review_feedback"][0]["required_change"] == "جدول را اصلاح کن"
    assert (workflow.artifacts_dir / "KSR-1" / "final_audit_attempt-1.json").exists()
    assert (workflow.artifacts_dir / "KSR-1" / "final_audit_attempt-2.json").exists()


def test_section_revise_runs_fresh_research_and_critic(workflow_env):
    workflow, fake, _ = workflow_env
    fake.section_verdicts = ["revise", "pass"]

    result = workflow.run_topic("KSR-1", max_section_revisions=1)

    assert result["status"] == "complete"
    research_calls = [context for name, context in fake.calls if name == "research_section"]
    assert len(research_calls) == 7
    assert research_calls[1]["review_feedback"][0]["required_change"] == "منبع معتبر جایگزین شود"
    revision_dir = workflow.artifacts_dir / "KSR-1" / "01_construct" / "revisions" / "section-attempt-1"
    assert (revision_dir / "draft.md").exists()
    assert (revision_dir / "critic_review.json").exists()
    history = workflow.load()["topics"]["KSR-1"]["sections"]["01_construct"]["revision_history"]
    assert history[0]["hashes"]["draft.md"]


def test_precritic_ui_error_is_archived_and_researched_again(workflow_env, monkeypatch):
    workflow, fake, _ = workflow_env
    original_run = fake.run_agent
    failed_once = False

    def run_agent(project_id, agent_name, context):
        nonlocal failed_once
        if agent_name == "research_section" and not failed_once:
            failed_once = True
            fake.calls.append((agent_name, context))
            return AgentOutput(agent_name=agent_name, success=True, content="Something went wrong. Try again.")
        return original_run(project_id, agent_name, context)

    monkeypatch.setattr(fake, "run_agent", run_agent)
    result = workflow.run_topic("1", max_section_revisions=1)

    assert result["status"] == "complete"
    assert [name for name, _ in fake.calls].count("research_section") == 7
    revision_dir = workflow.artifacts_dir / "KSR-1" / "01_construct" / "revisions" / "section-attempt-1"
    assert "Something went wrong" in (revision_dir / "research_response.txt").read_text(encoding="utf-8")


def test_source_change_pauses_without_calling_agents(workflow_env):
    workflow, fake, topic_path = workflow_env
    topic_path.write_text(topic_path.read_text(encoding="utf-8") + "\nexternal edit\n", encoding="utf-8")

    result = workflow.run_topic("1")

    assert result["status"] == "paused"
    assert "Source topic changed" in result["error"]
    assert fake.calls == []


def test_agent_exception_is_checkpointed_for_resume(workflow_env, monkeypatch):
    workflow, fake, _ = workflow_env

    def fail(*args, **kwargs):
        raise RuntimeError("browser crashed")

    monkeypatch.setattr(fake, "run_agent", fail)
    result = workflow.run_topic("1")

    assert result == {"topic": "KSR-1", "status": "paused", "error": "browser crashed"}
    state = workflow.load()
    assert state["status"] == "paused"
    assert state["active"]["stage"] == "planning_exception"


def test_initialize_rejects_duplicate_ids(tmp_path):
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "state.json").write_text("{}", encoding="utf-8")
    topics = tmp_path / "vault" / "موضوعات"
    topics.mkdir(parents=True)
    (topics / "a.md").write_text(_topic("KSR-1"), encoding="utf-8")
    (topics / "b.md").write_text(_topic("۱"), encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate research_id"):
        ResearchWorkflow(FakeOrchestrator(tmp_path), "demo").initialize(tmp_path / "vault", expected_topics=None)


def test_prepared_commit_is_recovered_without_agents(workflow_env):
    workflow, fake, topic_path = workflow_env
    state = workflow.load()
    candidate = topic_path.read_text(encoding="utf-8").replace("status: pending", "status: complete")
    topic_path.write_text(candidate, encoding="utf-8")
    topic = state["topics"]["KSR-1"]
    topic["commit"] = {"status": "prepared", "candidate_hash": _sha256(candidate)}
    workflow.save(state)

    result = workflow.run_topic("1")

    assert result["status"] == "complete"
    assert workflow.load()["topics"]["KSR-1"]["commit"]["status"] == "committed"
    assert fake.calls == []


@pytest.mark.parametrize(
    "parser,payload",
    [
        (_parse_section_review, {"verdict": "pass", "issues": [{"id": "x", "severity": "major", "location": "x", "problem": "x", "required_change": "x"}], "source_checks": []}),
        (_parse_final_audit, {"verdict": "pass", "issues": [{"section_ids": ["unknown"], "severity": "major", "problem": "x", "required_change": "x"}], "summary": "bad"}),
    ],
)
def test_strict_critic_contracts_reject_inconsistent_payloads(parser, payload):
    with pytest.raises(ValueError):
        parser(json.dumps(payload))


def test_workflow_status_cli_outputs_json(workflow_env, monkeypatch, capsys):
    workflow, fake, _ = workflow_env
    import orchestrator.__main__ as cli

    fake.close = lambda: None
    monkeypatch.setattr(cli, "Orchestrator", lambda *args, **kwargs: fake)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "workflow", "status", "demo"])

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ready"
    assert output["summary"] == {"total": 1, "complete": 0, "in_progress": 0, "paused": 0, "pending": 1, "needs_review": 0}


def test_section_prompts_are_scoped_and_enable_web_search_metadata():
    plan = {"questions": ["پرسش دقیق"], "search_queries": ["عبارت جست‌وجو"], "exclude": ["موضوع نامرتبط"]}
    common = {
        "project_id": "demo",
        "topic_id": "KSR-1",
        "topic_title": "عنوان کوتاه",
        "section_title": SECTIONS[0].title,
        "section_plan": plan,
        "_web_search": True,
        "topic_context": "UNRELATED_TEMPLATE_BULK",
    }
    research_request = ResearchSectionAgent().create_request({**common, "section_instructions": "الزام", "review_feedback": []})
    critic_request = ResearchSectionCriticAgent().create_request({**common, "section_draft": _section_markdown("01_construct")})

    for request in (research_request, critic_request):
        assert request.metadata["web_search"] is True
        assert "UNRELATED_TEMPLATE_BULK" not in request.prompt
        assert "پرسش دقیق" in request.prompt
        assert "موضوع نامرتبط" in request.prompt
        assert SECTIONS[0].title in request.prompt
