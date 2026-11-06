"""Deterministic tools the specialists call.

Policy numbers here are MOCK plan rules for the demo, not any real
employer's plan. Dates and windows are computed in code, never by the LLM.
Swap `benefits_rules` for pgvector RAG over plan documents later; keep
the date math deterministic.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import date, timedelta

# --- mock plan rules ---------------------------------------------------------

ENROLLMENT_WINDOW_DAYS = 30  # qualifying-life-event change window (mock)

BENEFITS_RULES: dict[str, list[str]] = {
    "birth_adoption": [
        "Add dependent to medical, dental and vision",
        "Review life insurance and beneficiaries",
        "Consider dependent-care FSA election",
    ],
    "marriage": [
        "Add spouse to medical, dental and vision",
        "Update beneficiaries",
        "Update tax withholding (W-4)",
    ],
    "divorce": [
        "Remove former spouse from coverage",
        "Update beneficiaries",
        "Update tax withholding (W-4)",
    ],
    "relocation": [
        "Confirm medical plan network covers new address",
        "Update home address and state tax withholding",
    ],
    "medical_leave": [
        "Confirm benefit premiums continue during leave",
    ],
}

LEAVE_RULES: dict[str, dict] = {
    "birth_adoption": {"weeks": 12, "notice_days": 30, "label": "Parental leave"},
    "medical_leave": {"weeks": 12, "notice_days": 30, "label": "Medical leave"},
}


def parse_date(s: str | None, fallback: str) -> date:
    return date.fromisoformat(s or fallback)


def enrollment_deadline(event_date: date) -> date:
    return event_date + timedelta(days=ENROLLMENT_WINDOW_DAYS)


def benefits_rules(event_type: str) -> list[str]:
    return BENEFITS_RULES.get(event_type, [])


def leave_policy(event_type: str) -> dict | None:
    return LEAVE_RULES.get(event_type)


# --- mock case API (idempotent) ---------------------------------------------


class MockCaseAPI:
    """Stands in for ServiceNow/Workday case APIs.

    Every write takes an idempotency key, so replaying the execute node
    after a crash, or time-travel re-running it, never creates duplicates.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cases: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.write_count = 0  # real writes only; evals assert on this

    @staticmethod
    def _id(prefix: str, key: str) -> str:
        return f"{prefix}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"

    def create_case(self, idem_key: str, employee_id: str, event_type: str) -> str:
        case_id = self._id("CASE", idem_key)
        with self._lock:
            if case_id not in self.cases:
                self.cases[case_id] = {"employee_id": employee_id, "event": event_type}
                self.write_count += 1
        return case_id

    def create_task(self, idem_key: str, case_id: str, task: dict) -> str:
        task_id = self._id("TASK", idem_key)
        with self._lock:
            if task_id not in self.tasks:
                self.tasks[task_id] = {"case_id": case_id, **task}
                self.write_count += 1
        return task_id


case_api = MockCaseAPI()
