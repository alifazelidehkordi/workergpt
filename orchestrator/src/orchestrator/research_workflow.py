"""Durable section-by-section scientific research workflow."""

from __future__ import annotations

import hashlib
import json
import fcntl
import os
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from orchestrator.core.orchestrator import Orchestrator


PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

RESEARCH_POLICY = """فقط مقالهٔ داوری‌شده، مرور نظام‌مند/فراتحلیل، کتاب دانشگاهی و گزارش روش‌مند معتبر استفاده شود. DOI، PMID، PMCID یا صفحهٔ رسمی ناشر ثبت شود. نظریه، همبستگی، علیت و استنباط طراحی جدا شوند. شواهد منفی و محدودیت تعمیم حذف نشوند. هیچ عدد، اندازهٔ اثر یا منبعی ساخته نشود. قانون محصول جایگزین تشخیص یا درمان تخصصی نیست."""

VALIDATION_POLICY = """پرونده باید frontmatter معتبر با status: complete، شش محور اصلی، جدول شواهد، منابع قابل‌ردیابی، شواهد منفی، شرایط مرزی، ایمنی، قانون عملیاتی و اصول کینتسوگی داشته باشد. هیچ TODO، placeholder، چک‌باکس خالی یا ادعای قطعی فراتر از شواهد پذیرفته نیست."""

TOPIC_PLANNER_TIMEOUT_SECONDS = 900
SECTION_RESEARCHER_TIMEOUT_SECONDS = 1800
SECTION_CRITIC_TIMEOUT_SECONDS = 900
FINAL_CRITIC_TIMEOUT_SECONDS = 900


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    instructions: str


SECTIONS = (
    SectionSpec(
        "01_construct",
        "## ۱. آیا چنین مفهومی در تحقیقات علمی وجود دارد؟",
        "با همین تیتر دقیق آغاز کن. سازه‌های نزدیک فارسی/انگلیسی، تعریف عملیاتی، مرز با مفاهیم مشابه، اعتبار سنجه‌ها و اینکه عنوان کینتسوگی سازهٔ مستقل است یا خوشهٔ طراحی را بررسی کن. در پایان حکم کالیبره و منابع این بخش را بیاور.",
    ),
    SectionSpec(
        "02_theories",
        "## ۲. چه نظریه‌هایی آن را حمایت می‌کنند؟",
        "با همین تیتر دقیق آغاز کن. نظریه‌های حامی و رقیب را جداگانه توضیح بده؛ برای هر نظریه پیش‌بینی قابل‌آزمون، دامنهٔ کاربرد، شاهد و محدودیت بنویس. حمایت نظری را با اثبات اثربخشی یکی نکن و منابع این بخش را بیاور.",
    ),
    SectionSpec(
        "03_mechanisms",
        "## ۳. مکانیسم روان‌شناختی پشت آن چیست؟",
        "با همین تیتر دقیق آغاز کن. زنجیرهٔ محرک ← فرایند ← رفتار ← پیامد، میانجی‌ها، تعدیل‌گرها، جهت‌های علی رقیب، پیامد کوتاه/بلندمدت و توضیح‌های جایگزین را پوشش بده. مکانیسم اثبات‌نشده را فرضیه بنام و منابع این بخش را بیاور.",
    ),
    SectionSpec(
        "04_evidence",
        "## ۴. چه مطالعات تجربی وجود دارد؟",
        "با همین تیتر دقیق آغاز کن. سؤال PICO/PECO، راهبرد مرور، مرورهای نظام‌مند و مطالعات کلیدی را گزارش کن. یک جدول Markdown با منبع، طرح، جمعیت/نمونه، مواجهه یا مداخله، مقایسه، پیامد/پیگیری، نتیجه یا اندازهٔ اثر و کیفیت/محدودیت بساز. شواهد همسو، ناهمسو و عدم اثر را جدا کن و منابع قابل‌ردیابی بیاور.",
    ),
    SectionSpec(
        "05_limits",
        "## ۵. محدودیت‌ها و نقدها چیست؟",
        "با همین تیتر دقیق آغاز کن. اعتبار سازه و سنجه، تعمیم‌پذیری، ناهمگونی، سوگیری انتشار، توان و تکرارپذیری، تعارض منافع، شواهد منفی، پیامد ناخواسته، گروه‌های در معرض خطر، منع کاربرد و ادعاهای اغراق‌شده را بررسی کن. منابع این بخش را بیاور.",
    ),
    SectionSpec(
        "06_practice",
        "## ۶. چگونه باید آن را به یک قانون عملی تبدیل کنیم؟",
        "با همین تیتر دقیق آغاز کن. قانون را با هدف رفتاری، نشانه، رفتار مشاهده‌پذیر، دامنه، افزایش/توقف، تعدیل، ایمنی، ثبت، سطح اطمینان و تاریخ بازبینی بنویس. سپس تیتر «## اصول عملی قابل استفاده در سیستم کینتسوگی» و اصول کوتاه با پشتوانه، اطمینان، شرایط و استثناها را اضافه کن. در پایان منابع این بخش را بیاور. آستانهٔ بی‌منبع نساز.",
    ),
)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _frontmatter_value(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*[\"']?([^\n\"']+)", text)
    return match.group(1).strip() if match else None


def _topic_sort_key(topic_id: str) -> tuple[int, int]:
    suffix = topic_id.removeprefix("KSR-").upper()
    numeric = re.match(r"(\d+)", suffix)
    return (int(numeric.group(1)) if numeric else 9999, 1 if suffix.endswith("A") else 0)


def _clean_model_block(text: str) -> str:
    value = text.strip()
    fence = re.search(r"```(?:markdown|md|json)?\s*\n([\s\S]*?)\n```", value, flags=re.IGNORECASE)
    if fence:
        value = fence.group(1).strip()
    # ChatGPT's rendered code-block DOM can occasionally preserve only the
    # language badge as a plain first line (without the backtick fence).
    # It is UI chrome, not dossier content.
    lines = value.splitlines()
    if len(lines) > 1 and lines[0].strip().lower() in {"markdown", "md", "json"}:
        value = "\n".join(lines[1:]).lstrip()
    return value.strip() + "\n"


def _parse_json_output(text: str) -> dict[str, Any]:
    cleaned = _clean_model_block(text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Final critic did not return a JSON object")
    return dict(json.loads(cleaned[start : end + 1]))


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _topic_excerpt(content: str, limit: int = 1200) -> str:
    body = re.sub(r"\A---\s*\n[\s\S]*?\n---\s*\n", "", content, count=1)
    body = re.split(r"(?m)^##\s+[۱1][\.\s]", body, maxsplit=1)[0]
    lines = [
        line for line in body.splitlines()
        if "در انتظار پژوهش" not in line and not re.match(r"^\s*-\s*\[[ xX]\]", line)
    ]
    return "\n".join(lines).strip()[:limit]


def _validate_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return value


def _parse_topic_plan(text: str) -> dict[str, Any]:
    plan = _parse_json_output(text)
    expected = {spec.id for spec in SECTIONS}
    if set(plan) != expected:
        raise ValueError("Topic plan must contain exactly the six known section ids")
    for section_id, item in plan.items():
        if not isinstance(item, dict) or set(item) != {"questions", "search_queries", "exclude"}:
            raise ValueError(f"Invalid plan schema for {section_id}")
        for field in ("questions", "search_queries", "exclude"):
            if not _validate_string_list(item[field], f"{section_id}.{field}"):
                raise ValueError(f"{section_id}.{field} cannot be empty")
    return plan


def _validate_issue(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"Invalid {label} schema")
    if not isinstance(value.get("severity"), str) or value["severity"] not in {"blocking", "major", "minor"}:
        raise ValueError(f"Invalid {label} severity")
    for field in fields - {"section_ids"}:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"{label}.{field} must be a non-empty string")
    return value


def _parse_section_review(text: str) -> dict[str, Any]:
    review = _parse_json_output(text)
    if set(review) != {"verdict", "issues", "source_checks"}:
        raise ValueError("Section critic returned unexpected fields")
    if review.get("verdict") not in {"pass", "revise"}:
        raise ValueError("Section critic verdict must be pass or revise")
    issues = review.get("issues")
    if not isinstance(issues, list):
        raise ValueError("issues must be a list")
    issue_fields = {"id", "severity", "location", "problem", "required_change"}
    for issue in issues:
        _validate_issue(issue, issue_fields, "section issue")
    checks = review.get("source_checks")
    if not isinstance(checks, list):
        raise ValueError("source_checks must be a list")
    check_fields = {"claim", "url", "result", "note"}
    for check in checks:
        if not isinstance(check, dict) or set(check) != check_fields or check.get("result") not in {"verified", "mismatch", "unverifiable"}:
            raise ValueError("Invalid source check schema")
        if not all(isinstance(check[field], str) for field in check_fields):
            raise ValueError("Source check values must be strings")
    material = [item for item in issues if item["severity"] in {"blocking", "major"}]
    if review["verdict"] == "pass" and material:
        raise ValueError("A passing section review cannot contain material issues")
    if review["verdict"] == "revise" and not material:
        raise ValueError("A revise verdict requires a blocking or major issue")
    return review


def _parse_final_audit(text: str) -> dict[str, Any]:
    audit = _parse_json_output(text)
    if set(audit) != {"verdict", "issues", "summary"}:
        raise ValueError("Final critic returned unexpected fields")
    if audit.get("verdict") not in {"pass", "revise"}:
        raise ValueError("Final critic verdict must be pass or revise")
    issues = audit.get("issues")
    if not isinstance(issues, list):
        raise ValueError("Final issues must be a list")
    known = {spec.id for spec in SECTIONS}
    fields = {"section_ids", "severity", "problem", "required_change"}
    for issue in issues:
        _validate_issue(issue, fields, "final issue")
        ids = issue["section_ids"]
        if not isinstance(ids, list) or not ids or not all(isinstance(item, str) and item in known for item in ids):
            raise ValueError("Final issue section_ids must contain known section ids")
    if not isinstance(audit.get("summary"), str) or not audit["summary"].strip():
        raise ValueError("Final critic summary must be a non-empty string")
    material = [item for item in issues if item["severity"] in {"blocking", "major"}]
    if audit["verdict"] == "pass" and material:
        raise ValueError("A passing final audit cannot contain material issues")
    if audit["verdict"] == "revise" and not material:
        raise ValueError("A revise verdict requires a blocking or major issue")
    return audit


def _validate_section_content(spec: SectionSpec, content: str) -> list[str]:
    issues: list[str] = []
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if first_line != spec.title:
        issues.append(f"missing required heading: {spec.title}")
    words = len(content.split())
    if words < 600:
        issues.append("section appears empty or truncated (fewer than 600 words)")
    if words > 900:
        issues.append("section exceeds the bounded length (more than 900 words)")
    if not re.search(r"\[\d+\]", content):
        issues.append("section has no numbered [n] citations")
    if not re.search(r"https://\S+", content, flags=re.IGNORECASE):
        issues.append("section has no traceable source marker")
    for token in ("contentreference", "oaicite", "something went wrong", "network error", "try again"):
        if token in content.lower():
            issues.append(f"forbidden UI/internal token: {token}")
    if spec.id == "04_evidence" and "|" not in content:
        issues.append("evidence section has no Markdown table")
    if spec.id == "06_practice" and "## اصول عملی قابل استفاده در سیستم کینتسوگی" not in content:
        issues.append("practice section has no Kintsugi principles heading")
    return issues


class ResearchWorkflow:
    """Coordinate bounded web research, independent criticism, and safe commits."""

    def __init__(self, orchestrator: Orchestrator, project_id: str, worker_id: str | None = None) -> None:
        self.orchestrator = orchestrator
        self.project_id = project_id
        self.project_dir = orchestrator.projects_dir / project_id
        self.state_path = self.project_dir / "research_workflow.json"
        self.state_lock_path = self.project_dir / ".research_workflow.lock"
        self.artifacts_dir = self.project_dir / "research_artifacts"
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._topic_locks: dict[str, TextIO] = {}
        self._claimed_topic: str | None = None

    def initialize(
        self,
        vault_dir: Path,
        report_file: Path | None = None,
        expected_topics: int | None = 70,
    ) -> dict[str, Any]:
        if not (self.project_dir / "state.json").is_file():
            raise FileNotFoundError(f"Orchestrator project not found: {self.project_id}")
        vault = Path(vault_dir).expanduser().resolve()
        topics_dir = vault / "موضوعات"
        if not topics_dir.is_dir():
            raise FileNotFoundError(f"Topic directory not found: {topics_dir}")
        if self.state_path.exists():
            return self.load()

        topics: dict[str, Any] = {}
        for path in topics_dir.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            topic_id = (_frontmatter_value(text, "research_id") or "").upper()
            if not topic_id:
                continue
            topic_id = self.normalize_topic_id(topic_id)
            if topic_id in topics:
                raise ValueError(f"Duplicate research_id: {topic_id}")
            status = (_frontmatter_value(text, "status") or "pending").lower()
            topics[topic_id] = {
                "path": str(path.relative_to(vault)),
                "title": _frontmatter_value(text, "title") or path.stem,
                "status": "complete" if status == "complete" else "pending",
                "source_hash": _sha256(text),
                "sections": {spec.id: {"status": "pending"} for spec in SECTIONS},
            }

        if expected_topics is not None and len(topics) != expected_topics:
            raise ValueError(f"Expected {expected_topics} unique research topics, found {len(topics)}")

        state = {
            "version": 3,
            "project_id": self.project_id,
            "vault_dir": str(vault),
            "report_file": str(Path(report_file).expanduser().resolve()) if report_file else None,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "active": None,
            "status": "ready",
            "topics": dict(sorted(topics.items(), key=lambda item: _topic_sort_key(item[0]))),
            "history": [],
            "claims": {},
        }
        self.save(state)
        return state

    def load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            raise FileNotFoundError("Research workflow is not initialized")
        self.project_dir.mkdir(parents=True, exist_ok=True)
        with self.state_lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
            return dict(json.loads(self.state_path.read_text(encoding="utf-8")))

    def save(self, state: dict[str, Any]) -> None:
        state["updated_at"] = datetime.now(UTC).isoformat()
        self.project_dir.mkdir(parents=True, exist_ok=True)
        with self.state_lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            value = state
            if self._claimed_topic and self.state_path.exists():
                current = dict(json.loads(self.state_path.read_text(encoding="utf-8")))
                topic_id = self._claimed_topic
                current["topics"][topic_id] = state["topics"][topic_id]
                seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in current.get("history", [])}
                for item in state.get("history", []):
                    marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
                    if marker not in seen:
                        current.setdefault("history", []).append(item)
                        seen.add(marker)
                for key in ("status", "active", "report_file"):
                    if key in state:
                        current[key] = state[key]
                current["updated_at"] = state["updated_at"]
                value = current
            _atomic_write(self.state_path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def _update_claim_record(self, topic_id: str, claimed: bool) -> None:
        """Update diagnostic claim metadata while holding the durable state lock."""
        with self.state_lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            state = dict(json.loads(self.state_path.read_text(encoding="utf-8")))
            claims = state.setdefault("claims", {})
            if claimed:
                claims[topic_id] = {
                    "worker_id": self.worker_id,
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "claimed_at": datetime.now(UTC).isoformat(),
                }
            elif claims.get(topic_id, {}).get("worker_id") == self.worker_id:
                claims.pop(topic_id, None)
            state["updated_at"] = datetime.now(UTC).isoformat()
            _atomic_write(self.state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    def _try_claim_topic(self, topic_id: str) -> bool:
        if topic_id in self._topic_locks:
            return True
        if self._claimed_topic is not None:
            raise RuntimeError(f"Worker already owns topic {self._claimed_topic}")
        claim_dir = self.project_dir / ".research_claims"
        claim_dir.mkdir(parents=True, exist_ok=True)
        lock_file = (claim_dir / f"{topic_id}.lock").open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        self._topic_locks[topic_id] = lock_file
        self._claimed_topic = topic_id
        self._update_claim_record(topic_id, True)
        return True

    def _release_topic(self, topic_id: str) -> None:
        lock_file = self._topic_locks.pop(topic_id, None)
        if lock_file is None:
            return
        try:
            self._update_claim_record(topic_id, False)
        finally:
            self._claimed_topic = None
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def claim_next_topic(self) -> str | None:
        """Atomically reserve the first unfinished topic not owned by another worker."""
        state = self.load()
        for topic_id, topic in state["topics"].items():
            if topic.get("status") != "complete" and self._try_claim_topic(topic_id):
                return topic_id
        return None

    def summary(self) -> dict[str, int]:
        topics = self.load()["topics"].values()
        return {
            "total": len(topics),
            "complete": sum(item["status"] == "complete" for item in topics),
            "in_progress": sum(item["status"] == "in_progress" for item in topics),
            "paused": sum(item["status"] == "paused" for item in topics),
            "pending": sum(item["status"] == "pending" for item in topics),
            "needs_review": sum(item["status"] == "needs_review" for item in topics),
        }

    @staticmethod
    def normalize_topic_id(value: str) -> str:
        cleaned = value.strip().upper().translate(PERSIAN_DIGITS)
        cleaned = cleaned.replace("الف", "A")
        return cleaned if cleaned.startswith("KSR-") else f"KSR-{cleaned}"

    def _artifact_dir(self, topic_id: str, section_id: str | None = None) -> Path:
        base = self.artifacts_dir / topic_id
        return base / section_id if section_id else base

    def _record_failure(self, state: dict[str, Any], topic_id: str, stage: str, error: str) -> None:
        topic = state["topics"][topic_id]
        topic["status"] = "paused"
        state["status"] = "paused"
        state["active"] = {"topic": topic_id, "stage": stage}
        state["history"].append({"at": datetime.now(UTC).isoformat(), "topic": topic_id, "stage": stage, "error": error})
        self.save(state)

    def next_topic(self) -> str | None:
        state = self.load()
        for topic_id, topic in state["topics"].items():
            if topic.get("status") != "complete":
                return topic_id
        return None

    def _recover_commit(self, state: dict[str, Any], topic_id: str, content: str) -> bool:
        topic = state["topics"][topic_id]
        journal = topic.get("commit")
        if not isinstance(journal, dict) or journal.get("status") != "prepared":
            return False
        if _sha256(content) != journal.get("candidate_hash"):
            return False
        journal["status"] = "committed"
        journal["recovered_at"] = datetime.now(UTC).isoformat()
        topic["status"] = "complete"
        topic["source_hash"] = journal["candidate_hash"]
        topic["completed_at"] = datetime.now(UTC).isoformat()
        state["status"] = "ready"
        state["active"] = None
        state["history"].append({"at": datetime.now(UTC).isoformat(), "topic": topic_id, "stage": "commit_recovered"})
        self.save(state)
        self.sync_vault_progress()
        return True

    @staticmethod
    def _repair_section_ids(audit: dict[str, Any]) -> set[str]:
        return {
            section_id
            for issue in audit["issues"]
            if issue["severity"] in {"blocking", "major"}
            for section_id in issue["section_ids"]
        }

    def _archive_section_attempt(
        self,
        state: dict[str, Any],
        topic_id: str,
        section_id: str,
        feedback: list[Any],
        label: str,
    ) -> None:
        section = state["topics"][topic_id]["sections"][section_id]
        attempt = int(section.get("section_revision_attempts", 0)) + 1
        artifact_dir = self._artifact_dir(topic_id, section_id)
        revision_dir = artifact_dir / "revisions" / f"section-attempt-{attempt}"
        hashes: dict[str, str] = {}
        for name in ("research_response.txt", "draft.md", "approved.md", "critic_response.txt", "critic_review.json"):
            source = artifact_dir / name
            if source.exists():
                content = source.read_text(encoding="utf-8")
                _atomic_write(revision_dir / name, content)
                hashes[name] = _sha256(content)
                source.unlink()
        section.setdefault("revision_history", []).append(
            {"attempt": attempt, "at": datetime.now(UTC).isoformat(), "label": label, "feedback": feedback, "hashes": hashes}
        )
        section.update(
            {"status": "pending", "review_feedback": feedback, "section_revision_attempts": attempt}
        )
        for key in ("draft", "draft_hash", "approved", "approved_hash", "critic_review", "unresolved"):
            section.pop(key, None)
        state["history"].append(
            {"at": datetime.now(UTC).isoformat(), "topic": topic_id, "section": section_id, "stage": "section_retry", "attempt": attempt}
        )
        self.save(state)

    def _invalidate_sections(
        self,
        state: dict[str, Any],
        topic_id: str,
        section_ids: set[str],
        audit: dict[str, Any],
        attempt: int,
    ) -> None:
        topic = state["topics"][topic_id]
        for section_id in section_ids:
            section = topic["sections"][section_id]
            artifact_dir = self._artifact_dir(topic_id, section_id)
            revision_dir = artifact_dir / "revisions" / f"final-audit-attempt-{attempt}"
            hashes: dict[str, str] = {}
            for name in ("research_response.txt", "draft.md", "approved.md", "critic_review.json", "critic_response.txt"):
                source = artifact_dir / name
                if source.exists():
                    content = source.read_text(encoding="utf-8")
                    _atomic_write(revision_dir / name, content)
                    hashes[name] = _sha256(content)
                    source.unlink()
            feedback = [issue for issue in audit["issues"] if section_id in issue["section_ids"]]
            section.update({"status": "pending", "review_feedback": feedback, "revision_attempt": attempt})
            section.setdefault("revision_history", []).append(
                {"attempt": attempt, "at": datetime.now(UTC).isoformat(), "label": "final_audit", "feedback": feedback, "hashes": hashes}
            )
            section.pop("approved", None)
            section.pop("approved_hash", None)
        topic["revision_attempts"] = attempt
        state["history"].append(
            {"at": datetime.now(UTC).isoformat(), "topic": topic_id, "stage": "targeted_repair", "sections": sorted(section_ids)}
        )
        self.save(state)

    def run_topic(
        self,
        topic_value: str,
        max_sections: int | None = None,
        max_revisions: int = 1,
        max_section_revisions: int = 1,
    ) -> dict[str, Any]:
        topic_id = self.normalize_topic_id(topic_value)
        acquired_here = topic_id not in self._topic_locks
        if acquired_here and not self._try_claim_topic(topic_id):
            return {"topic": topic_id, "status": "claimed", "error": "Topic is already claimed by another worker"}
        try:
            return self._run_topic_claimed(topic_id, max_sections, max_revisions, max_section_revisions)
        finally:
            self._release_topic(topic_id)

    def _run_topic_claimed(
        self,
        topic_value: str,
        max_sections: int | None = None,
        max_revisions: int = 1,
        max_section_revisions: int = 1,
    ) -> dict[str, Any]:
        if max_sections is not None and max_sections < 1:
            raise ValueError("max_sections must be at least 1")
        if max_revisions < 0:
            raise ValueError("max_revisions cannot be negative")
        if max_section_revisions < 0:
            raise ValueError("max_section_revisions cannot be negative")
        state = self.load()
        topic_id = self.normalize_topic_id(topic_value)
        if topic_id not in state["topics"]:
            raise KeyError(f"Unknown topic: {topic_id}")
        topic = state["topics"][topic_id]
        vault = Path(state["vault_dir"])
        topic_path = vault / topic["path"]
        original = topic_path.read_text(encoding="utf-8")
        if self._recover_commit(state, topic_id, original):
            return {"topic": topic_id, "status": "complete", "message": "Recovered a completed commit"}
        if topic["status"] == "complete":
            self.sync_vault_progress()
            return {"topic": topic_id, "status": "complete", "message": "Topic is already complete"}
        if topic.get("source_hash") and _sha256(original) != topic["source_hash"]:
            self._record_failure(state, topic_id, "source_changed", "The source topic changed after workflow initialization")
            return {"topic": topic_id, "status": "paused", "error": "Source topic changed; reinitialize or reconcile it manually"}

        plan_path = self._artifact_dir(topic_id) / "research_plan.json"
        if plan_path.exists():
            plan_content = plan_path.read_text(encoding="utf-8")
            if topic.get("plan_hash") and _sha256(plan_content) != topic["plan_hash"]:
                self._record_failure(state, topic_id, "plan_changed", "Research plan hash mismatch")
                return {"topic": topic_id, "status": "paused", "error": "Research plan changed outside the workflow"}
            try:
                plan = _parse_topic_plan(plan_content)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self._record_failure(state, topic_id, "plan_parse", str(exc))
                return {"topic": topic_id, "status": "paused", "error": str(exc)}
        else:
            state["active"] = {"topic": topic_id, "stage": "planning"}
            self.save(state)
            try:
                plan_output = self.orchestrator.run_agent(
                    self.project_id,
                    "research_topic_planner",
                    {
                        "research_policy": RESEARCH_POLICY,
                        "topic_id": topic_id,
                        "topic_title": topic["title"],
                        "topic_excerpt": _topic_excerpt(original),
                        "_timeout_seconds": TOPIC_PLANNER_TIMEOUT_SECONDS,
                    },
                )
            except Exception as exc:
                self._record_failure(state, topic_id, "planning_exception", str(exc))
                return {"topic": topic_id, "status": "paused", "error": str(exc)}
            if not plan_output.success:
                self._record_failure(state, topic_id, "planning", plan_output.content)
                return {"topic": topic_id, "status": "paused", "error": plan_output.content}
            _atomic_write(self._artifact_dir(topic_id) / "research_plan_response.txt", plan_output.content)
            try:
                plan = _parse_topic_plan(plan_output.content)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self._record_failure(state, topic_id, "plan_parse", str(exc))
                return {"topic": topic_id, "status": "paused", "error": str(exc)}
            plan_content = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
            _atomic_write(plan_path, plan_content)
            topic["plan"] = str(plan_path.relative_to(self.project_dir))
            topic["plan_hash"] = _sha256(plan_content)
            self.save(state)
        topic["status"] = "in_progress"
        state["status"] = "running"
        self.save(state)
        completed_now = 0

        for spec in SECTIONS:
            section = topic["sections"][spec.id]
            artifact_dir = self._artifact_dir(topic_id, spec.id)
            draft_path = artifact_dir / "draft.md"
            approved_path = artifact_dir / "approved.md"

            if section.get("status") == "needs_review" and not approved_path.exists():
                attempt = int(section.get("section_revision_attempts", 0)) + 1
                if attempt > max_section_revisions:
                    recovered = self._recover_archived_draft(topic_id, spec, section, draft_path)
                    if recovered:
                        topic["status"] = "in_progress"
                        state["status"] = "running"
                        self.save(state)
                    else:
                        topic["status"] = "needs_review"
                        state["status"] = "needs_review"
                        state["active"] = None
                        self.save(state)
                        return {"topic": topic_id, "status": "needs_review", "section": spec.id, "issues": section.get("unresolved", [])}
                else:
                    self._archive_section_attempt(
                        state, topic_id, spec.id, list(section.get("unresolved", [])), "resume_needs_review"
                    )

            if section["status"] == "complete" and approved_path.exists():
                if section.get("approved_hash") and _sha256(approved_path.read_text(encoding="utf-8")) != section["approved_hash"]:
                    self._record_failure(state, topic_id, f"{spec.id}:artifact_changed", "Approved artifact hash mismatch")
                    return {"topic": topic_id, "status": "paused", "error": "Approved artifact changed outside the workflow"}
                continue
            if draft_path.exists() and section.get("draft_hash") and _sha256(draft_path.read_text(encoding="utf-8")) != section["draft_hash"]:
                self._record_failure(state, topic_id, f"{spec.id}:artifact_changed", "Draft artifact hash mismatch")
                return {"topic": topic_id, "status": "paused", "error": "Draft artifact changed outside the workflow"}
            if not draft_path.exists():
                state["active"] = {"topic": topic_id, "section": spec.id, "stage": "research"}
                section["status"] = "researching"
                self.save(state)
                try:
                    output = self.orchestrator.run_agent(
                        self.project_id,
                        "research_section",
                        {
                            "research_policy": RESEARCH_POLICY,
                            "topic_id": topic_id,
                            "topic_title": topic["title"],
                            "section_title": spec.title,
                            "section_instructions": spec.instructions,
                            "section_plan": plan[spec.id],
                            "review_feedback": section.get("review_feedback", []),
                            "_timeout_seconds": SECTION_RESEARCHER_TIMEOUT_SECONDS,
                            "_web_search": True,
                        },
                    )
                except Exception as exc:
                    self._record_failure(state, topic_id, f"{spec.id}:research_exception", str(exc))
                    return {"topic": topic_id, "status": "paused", "error": str(exc)}
                if not output.success:
                    self._record_failure(state, topic_id, f"{spec.id}:research", output.content)
                    return {"topic": topic_id, "status": "paused", "error": output.content}
                _atomic_write(artifact_dir / "research_response.txt", output.content)
                cleaned_draft = _clean_model_block(output.content)
                try:
                    blocked = _parse_json_output(output.content)
                except (TypeError, ValueError, json.JSONDecodeError):
                    blocked = None
                if isinstance(blocked, dict) and blocked.get("status") == "blocked":
                    reason = str(blocked.get("reason") or "Researcher reported a blocked result")
                    self._record_failure(state, topic_id, f"{spec.id}:research_blocked", reason)
                    return {"topic": topic_id, "status": "paused", "error": reason}
                local_section_issues = _validate_section_content(spec, cleaned_draft)
                if local_section_issues:
                    section["status"] = "needs_review"
                    section["unresolved"] = local_section_issues
                    attempt = int(section.get("section_revision_attempts", 0)) + 1
                    if attempt <= max_section_revisions:
                        _atomic_write(draft_path, cleaned_draft)
                        self._archive_section_attempt(state, topic_id, spec.id, local_section_issues, "local_precritic_validation")
                        return self._run_topic_claimed(
                            topic_id,
                            max_sections=max_sections,
                            max_revisions=max_revisions,
                            max_section_revisions=max_section_revisions,
                        )
                    topic["status"] = "needs_review"
                    state["status"] = "needs_review"
                    state["active"] = None
                    self.save(state)
                    return {"topic": topic_id, "status": "needs_review", "section": spec.id, "issues": local_section_issues}
                _atomic_write(draft_path, cleaned_draft)
                section["draft"] = str(draft_path.relative_to(self.project_dir))
                section["draft_hash"] = _sha256(draft_path.read_text(encoding="utf-8"))
                section["status"] = "researched"
                self.save(state)

            if not approved_path.exists():
                state["active"] = {"topic": topic_id, "section": spec.id, "stage": "critic"}
                section["status"] = "criticizing"
                self.save(state)
                try:
                    output = self.orchestrator.run_agent(
                        self.project_id,
                        "research_section_critic",
                        {
                            "topic_title": topic["title"],
                            "topic_id": topic_id,
                            "section_title": spec.title,
                            "section_plan": plan[spec.id],
                            "section_draft": draft_path.read_text(encoding="utf-8"),
                            "_timeout_seconds": SECTION_CRITIC_TIMEOUT_SECONDS,
                            "_web_search": True,
                        },
                    )
                except Exception as exc:
                    self._record_failure(state, topic_id, f"{spec.id}:critic_exception", str(exc))
                    return {"topic": topic_id, "status": "paused", "error": str(exc)}
                if not output.success:
                    self._record_failure(state, topic_id, f"{spec.id}:critic", output.content)
                    return {"topic": topic_id, "status": "paused", "error": output.content}
                review_path = artifact_dir / "critic_review.json"
                _atomic_write(artifact_dir / "critic_response.txt", output.content)
                try:
                    review = _parse_section_review(output.content)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    self._record_failure(state, topic_id, f"{spec.id}:critic_parse", str(exc))
                    return {"topic": topic_id, "status": "paused", "error": str(exc)}
                _atomic_write(review_path, json.dumps(review, ensure_ascii=False, indent=2) + "\n")
                section["critic_review"] = str(review_path.relative_to(self.project_dir))
                if review["verdict"] != "pass":
                    material_issues = [item for item in review["issues"] if item["severity"] in {"blocking", "major"}]
                    section["status"] = "needs_review"
                    section["unresolved"] = material_issues
                    attempt = int(section.get("section_revision_attempts", 0)) + 1
                    if attempt <= max_section_revisions:
                        self._archive_section_attempt(state, topic_id, spec.id, material_issues, "critic_revise")
                        return self._run_topic_claimed(
                            topic_id,
                            max_sections=max_sections,
                            max_revisions=max_revisions,
                            max_section_revisions=max_section_revisions,
                        )
                    topic["status"] = "needs_review"
                    state["status"] = "needs_review"
                    state["active"] = None
                    self.save(state)
                    return {"topic": topic_id, "status": "needs_review", "section": spec.id, "issues": material_issues}
                approved_content = draft_path.read_text(encoding="utf-8")
                _atomic_write(approved_path, approved_content)
                section["approved"] = str(approved_path.relative_to(self.project_dir))
                section["approved_hash"] = _sha256(approved_content)

            section["status"] = "complete"
            section["completed_at"] = datetime.now(UTC).isoformat()
            state["active"] = None
            self.save(state)
            completed_now += 1
            if max_sections is not None and completed_now >= max_sections:
                return {"topic": topic_id, "status": "in_progress", "sections_completed_now": completed_now}

        try:
            candidate = self._assemble(topic_id, original, topic)
        except (OSError, ValueError) as exc:
            self._record_failure(state, topic_id, "assemble", str(exc))
            return {"topic": topic_id, "status": "paused", "error": str(exc)}
        candidate_path = self._artifact_dir(topic_id) / "candidate.md"
        _atomic_write(candidate_path, candidate)

        audit_path = self._artifact_dir(topic_id) / "final_audit.json"
        state["active"] = {"topic": topic_id, "stage": "final_critic"}
        self.save(state)
        try:
            audit_output = self.orchestrator.run_agent(
                self.project_id,
                "research_file_critic",
                {
                    "validation_policy": VALIDATION_POLICY,
                    "assembled_dossier": candidate,
                    "_timeout_seconds": FINAL_CRITIC_TIMEOUT_SECONDS,
                    "_web_search": True,
                },
            )
        except Exception as exc:
            self._record_failure(state, topic_id, "final_critic_exception", str(exc))
            return {"topic": topic_id, "status": "paused", "error": str(exc)}
        if not audit_output.success:
            self._record_failure(state, topic_id, "final_critic", audit_output.content)
            return {"topic": topic_id, "status": "paused", "error": audit_output.content}
        _atomic_write(self._artifact_dir(topic_id) / "final_audit_response.txt", audit_output.content)
        try:
            audit = _parse_final_audit(audit_output.content)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._record_failure(state, topic_id, "final_critic_parse", str(exc))
            return {"topic": topic_id, "status": "paused", "error": str(exc)}
        audit_round = int(topic.get("audit_attempts", 0)) + 1
        audit_content = json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
        _atomic_write(self._artifact_dir(topic_id) / f"final_audit_attempt-{audit_round}.json", audit_content)
        _atomic_write(audit_path, audit_content)
        topic["audit_attempts"] = audit_round

        local_issues = self.validate_candidate(candidate)
        if audit.get("verdict") != "pass" or local_issues:
            repairs = self._repair_section_ids(audit)
            attempt = int(topic.get("revision_attempts", 0)) + 1
            if audit["verdict"] == "revise" and repairs and attempt <= max_revisions:
                self._invalidate_sections(state, topic_id, repairs, audit, attempt)
                return self._run_topic_claimed(
                    topic_id,
                    max_sections=max_sections,
                    max_revisions=max_revisions,
                    max_section_revisions=max_section_revisions,
                )
            topic["status"] = "needs_review"
            topic["audit"] = str(audit_path.relative_to(self.project_dir))
            topic["local_issues"] = local_issues
            state["status"] = "needs_review"
            state["active"] = None
            self.save(state)
            return {"topic": topic_id, "status": "needs_review", "audit": audit, "local_issues": local_issues}

        backup_path = self._artifact_dir(topic_id) / "original.md"
        if not backup_path.exists():
            _atomic_write(backup_path, original)
        topic["commit"] = {
            "status": "prepared",
            "target": str(topic_path),
            "backup": str(backup_path.relative_to(self.project_dir)),
            "original_hash": _sha256(original),
            "candidate_hash": _sha256(candidate),
            "prepared_at": datetime.now(UTC).isoformat(),
        }
        state["active"] = {"topic": topic_id, "stage": "commit"}
        self.save(state)
        _atomic_write(topic_path, candidate)
        topic["status"] = "complete"
        topic["source_hash"] = _sha256(candidate)
        topic["completed_at"] = datetime.now(UTC).isoformat()
        topic["audit"] = str(audit_path.relative_to(self.project_dir))
        topic["commit"]["status"] = "committed"
        topic["commit"]["committed_at"] = datetime.now(UTC).isoformat()
        state["status"] = "ready"
        state["active"] = None
        state["history"].append({"at": datetime.now(UTC).isoformat(), "topic": topic_id, "stage": "committed"})
        self.save(state)
        self.sync_vault_progress()
        return {"topic": topic_id, "status": "complete", "audit": audit, "local_issues": []}

    def _recover_archived_draft(
        self,
        topic_id: str,
        spec: SectionSpec,
        section: dict[str, Any],
        draft_path: Path,
    ) -> bool:
        """Recover a valid draft previously rejected because UI chrome leaked in."""
        revisions = self._artifact_dir(topic_id, spec.id) / "revisions"
        candidates = sorted(
            revisions.glob("section-attempt-*/draft.md"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for candidate in candidates:
            cleaned = _clean_model_block(candidate.read_text(encoding="utf-8"))
            if _validate_section_content(spec, cleaned):
                continue
            _atomic_write(draft_path, cleaned)
            section["draft"] = str(draft_path.relative_to(self.project_dir))
            section["draft_hash"] = _sha256(cleaned)
            section["status"] = "researched"
            section.pop("unresolved", None)
            section.pop("review_feedback", None)
            return True
        return False

    def _assemble(self, topic_id: str, original: str, topic: dict[str, Any]) -> str:
        first_heading = original.find("# ")
        first_section = original.find("## ۱.")
        if first_heading < 0 or first_section < 0:
            raise ValueError(f"Topic template is malformed: {topic_id}")
        prefix = original[:first_section]
        prefix = re.sub(r"(?m)^status:\s*[^\n]+", "status: complete", prefix, count=1)
        prefix = re.sub(r"(?m)^updated:\s*[^\n]+", f"updated: {datetime.now().date().isoformat()}", prefix, count=1)
        prefix = prefix.replace("**در انتظار پژوهش**", "**تکمیل‌شده**")
        parts = [prefix.rstrip()]
        for spec in SECTIONS:
            approved = self._artifact_dir(topic_id, spec.id) / "approved.md"
            parts.append(_clean_model_block(approved.read_text(encoding="utf-8")).rstrip())
        parts.append(
            """## وضعیت پژوهش

- [x] تعریف و کلیدواژه‌ها تکمیل شد.
- [x] جست‌وجوی منابع انجام شد.
- [x] منابع غربال و کیفیت‌سنجی شدند.
- [x] نظریه و مکانیسم جمع‌بندی شد.
- [x] مطالعات تجربی سنتز شدند.
- [x] محدودیت‌ها و نقدها ثبت شدند.
- [x] قانون عملی تدوین شد.
- [x] اصول قابل استفاده در کینتسوگی استخراج شد.
- [x] هر بخش توسط Critic مستقل بازبینی شد.

> [!success] وضعیت نهایی
> تکمیل‌شده با فرایند بخش‌بندی‌شدهٔ Researcher → Critic و ممیزی نهایی Orchestrator.

**وضعیت فعلی:** کامل"""
        )
        return "\n\n".join(parts).strip() + "\n"

    @staticmethod
    def validate_candidate(content: str) -> list[str]:
        issues: list[str] = []
        if not content.startswith("---\n") or not re.search(r"(?m)^status:\s*complete\s*$", content):
            issues.append("frontmatter/status is invalid")
        for spec in SECTIONS:
            if spec.title not in content:
                issues.append(f"missing heading: {spec.title}")
        if "## اصول عملی قابل استفاده در سیستم کینتسوگی" not in content:
            issues.append("missing practical principles")
        forbidden = ("<!--", "[ ]", "[!todo]", "در انتظار پژوهش")
        for token in forbidden:
            if token.lower() in content.lower():
                issues.append(f"placeholder remains: {token}")
        if len(content.split()) < 1800:
            issues.append("dossier is shorter than 1800 words")
        source_markers = re.findall(r"doi\.org|PMID|PMCID|pubmed\.ncbi|https?://", content, flags=re.IGNORECASE)
        if len(source_markers) < 6:
            issues.append("fewer than six traceable source markers")
        if "|" not in content:
            issues.append("evidence table is missing")
        return issues

    def sync_vault_progress(self) -> None:
        state = self.load()
        vault = Path(state["vault_dir"])
        statuses = [item["status"] for item in state["topics"].values()]
        complete = statuses.count("complete")
        pending = len(statuses) - complete
        in_progress = sum(value in {"in_progress", "paused", "needs_review"} for value in statuses)

        index_path = vault / "ایندکس پژوهش علمی کینتسوگی.md"
        if index_path.exists():
            text = index_path.read_text(encoding="utf-8")
            for key, value in (("completed_topics", complete), ("pending_topics", pending), ("in_progress_topics", in_progress)):
                text = re.sub(rf"(?m)^{key}:\s*\d+", f"{key}: {value}", text, count=1)
            text = re.sub(r"(?m)^\| تکمیل‌شده \|.*$", f"| تکمیل‌شده | {complete} |", text)
            text = re.sub(r"(?m)^\| در انتظار پژوهش \|.*$", f"| در انتظار پژوهش | {pending} |", text)
            text = re.sub(r"(?m)^\| در حال پژوهش \|.*$", f"| در حال پژوهش | {in_progress} |", text)
            for topic_id, item in state["topics"].items():
                if item["status"] != "complete":
                    continue
                lines = text.splitlines()
                for index, line in enumerate(lines):
                    if line.startswith(f"| {topic_id} |"):
                        prefix, _, ending = line.rpartition("|")
                        prefix, _, _old_status = prefix.rpartition("|")
                        lines[index] = f"{prefix}| 🟢 `complete` |{ending}"
                        break
                text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
            _atomic_write(index_path, text)

        report_value = state.get("report_file")
        if report_value and Path(report_value).exists():
            report_path = Path(report_value)
            text = report_path.read_text(encoding="utf-8")
            for key, value in (("completed_topics", complete), ("pending_topics", pending)):
                text = re.sub(rf"(?m)^{key}:\s*\d+", f"{key}: {value}", text, count=1)
            percent = round((complete / len(statuses)) * 100, 2) if statuses else 0
            text = re.sub(r"(?m)^completion_percent:\s*[\d.]+", f"completion_percent: {percent}", text, count=1)
            marker = (
                "<!-- orchestrator-status:start -->\n"
                "## گزارش خودکار Orchestrator\n\n"
                f"> [!info] آخرین همگام‌سازی\n> کامل: **{complete}** · باقی‌مانده: **{pending}** · در جریان/نیازمند بررسی: **{in_progress}**\n"
                "<!-- orchestrator-status:end -->"
            )
            if "<!-- orchestrator-status:start -->" in text:
                text = re.sub(
                    r"<!-- orchestrator-status:start -->[\s\S]*?<!-- orchestrator-status:end -->",
                    marker,
                    text,
                    count=1,
                )
            else:
                text = text.rstrip() + "\n\n" + marker + "\n"
            _atomic_write(report_path, text)
