"""Independent task-outcome evaluation primitives for P2.

P0 protects execution safety and P1 controls failed-action retries. P2 needs a
separate answer to a different question: did a successful execution actually
satisfy the task contract?

This first P2 layer is deliberately deterministic. It does not pretend that a
non-empty LLM response is semantically correct. Without an explicit evaluation
contract the result is UNASSESSED, keeping execution success and task success
separate until a real evaluator/gate decides otherwise.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from orchestrator.core.models import AgentOutput


class EvaluationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNASSESSED = "unassessed"


@dataclass(frozen=True)
class TaskEvaluation:
    status: EvaluationStatus
    task_success: bool | None
    reason: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    evaluator: str = "deterministic_contract_v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


class TaskEvaluator:
    """Evaluate an AgentOutput against an explicit, deterministic contract.

    Supported context key::

        _evaluation_contract = {
            "require_nonempty": true,
            "min_chars": 100,
            "required_terms": ["source"],
            "forbidden_terms": ["Something went wrong"],
            "case_sensitive": false,
            "expected_format": "text" | "json",
            "required_json_keys": ["answer", "sources"],
        }

    The evaluator is intentionally conservative: no explicit contract means
    task success is unknown rather than inferred from transport success.
    """

    CONTRACT_KEY = "_evaluation_contract"

    def evaluate(
        self,
        output: AgentOutput,
        context: dict[str, Any] | None,
        *,
        execution_success: bool,
    ) -> TaskEvaluation:
        if not execution_success:
            return TaskEvaluation(
                status=EvaluationStatus.FAILED,
                task_success=False,
                reason="execution_failed",
            )

        if not output.success:
            return TaskEvaluation(
                status=EvaluationStatus.FAILED,
                task_success=False,
                reason="agent_output_failed",
            )

        context = context or {}
        if self.CONTRACT_KEY not in context:
            return TaskEvaluation(
                status=EvaluationStatus.UNASSESSED,
                task_success=None,
                reason="no_evaluation_contract",
            )

        contract = context.get(self.CONTRACT_KEY)
        if not isinstance(contract, dict):
            return TaskEvaluation(
                status=EvaluationStatus.UNASSESSED,
                task_success=None,
                reason="invalid_evaluation_contract",
            )

        checks: list[dict[str, Any]] = []
        content = output.content or ""
        case_sensitive = bool(contract.get("case_sensitive", False))

        require_nonempty = bool(contract.get("require_nonempty", True))
        if require_nonempty:
            self._check(checks, "nonempty", bool(content.strip()))

        if "min_chars" in contract:
            minimum = self._nonnegative_int(contract.get("min_chars"))
            if minimum is None:
                return self._invalid_contract(checks, "min_chars")
            self._check(
                checks,
                "min_chars",
                len(content) >= minimum,
                expected=minimum,
                actual=len(content),
            )

        required_terms = contract.get("required_terms", [])
        if not self._is_string_list(required_terms):
            return self._invalid_contract(checks, "required_terms")
        searchable = content if case_sensitive else content.casefold()
        for term in required_terms:
            needle = term if case_sensitive else term.casefold()
            self._check(
                checks,
                "required_term",
                needle in searchable,
                term=term,
            )

        forbidden_terms = contract.get("forbidden_terms", [])
        if not self._is_string_list(forbidden_terms):
            return self._invalid_contract(checks, "forbidden_terms")
        for term in forbidden_terms:
            needle = term if case_sensitive else term.casefold()
            self._check(
                checks,
                "forbidden_term",
                needle not in searchable,
                term=term,
            )

        parsed_json: Any = None
        expected_format = contract.get("expected_format", "text")
        if expected_format not in {"text", "json"}:
            return self._invalid_contract(checks, "expected_format")
        if expected_format == "json":
            try:
                parsed_json = json.loads(content)
                self._check(checks, "valid_json", True)
            except json.JSONDecodeError:
                self._check(checks, "valid_json", False)

        required_json_keys = contract.get("required_json_keys", [])
        if not self._is_string_list(required_json_keys):
            return self._invalid_contract(checks, "required_json_keys")
        if required_json_keys and expected_format != "json":
            return self._invalid_contract(checks, "required_json_keys_requires_json")
        if required_json_keys and isinstance(parsed_json, dict):
            for key in required_json_keys:
                self._check(
                    checks,
                    "required_json_key",
                    key in parsed_json,
                    key=key,
                )
        elif required_json_keys:
            for key in required_json_keys:
                self._check(
                    checks,
                    "required_json_key",
                    False,
                    key=key,
                )

        failed = [item for item in checks if not item["passed"]]
        if failed:
            return TaskEvaluation(
                status=EvaluationStatus.FAILED,
                task_success=False,
                reason="evaluation_contract_failed",
                checks=checks,
            )
        return TaskEvaluation(
            status=EvaluationStatus.PASSED,
            task_success=True,
            reason="evaluation_contract_passed",
            checks=checks,
        )

    @staticmethod
    def _check(
        checks: list[dict[str, Any]],
        name: str,
        passed: bool,
        **details: Any,
    ) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _is_string_list(value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)

    @staticmethod
    def _invalid_contract(
        checks: list[dict[str, Any]],
        field_name: str,
    ) -> TaskEvaluation:
        return TaskEvaluation(
            status=EvaluationStatus.UNASSESSED,
            task_success=None,
            reason="invalid_evaluation_contract",
            checks=[*checks, {"name": "contract_field", "passed": False, "field": field_name}],
        )
