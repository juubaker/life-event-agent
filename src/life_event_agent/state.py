"""Graph state schema.

Fields written by more than one node (findings, audit) use reducers so
parallel specialist branches can write without clobbering each other.
"""
from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

EventType = Literal[
    "birth_adoption",
    "marriage",
    "divorce",
    "relocation",
    "medical_leave",
    "unknown",
]

Specialist = Literal["benefits", "leave"]


class Classification(BaseModel):
    """Structured output of the classify node (LLM or rules)."""

    event_type: EventType
    event_date: str | None = Field(
        default=None, description="ISO date resolved in code from date_text"
    )
    date_text: str | None = Field(
        default=None, description="timing words as the employee wrote them"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class Finding(BaseModel):
    """One specialist's contribution to the plan."""

    source: Specialist
    summary: str
    actions: list[str] = []
    deadline: str | None = None  # ISO date


class PlanTask(BaseModel):
    task_id: str
    title: str
    owner: Literal["employee", "hr"] = "employee"
    due: str | None = None


class Plan(BaseModel):
    event_type: EventType
    tasks: list[PlanTask]
    employee_message: str
    # "llm" = model rewrite that passed the fact check; "template" = built in code
    message_source: Literal["llm", "template"] = "template"
    message_note: str = ""  # why a rewrite was rejected, for the HR reviewer


class Review(BaseModel):
    """What the HR reviewer sends back to resume the interrupt."""

    action: Literal["approve", "edit", "reject"]
    plan: Plan | None = None  # required when action == "edit"
    note: str = ""


class AgentState(TypedDict, total=False):
    # input
    employee_id: str
    request: str
    today: str  # ISO date; injected so runs are reproducible in evals
    received_at: str  # ISO timestamp the request arrived (set by the API)

    # classify
    classification: Classification
    specialists: list[Specialist]

    # specialists (parallel fan-out -> reducer)
    findings: Annotated[list[Finding], operator.add]

    # plan + human gate
    plan: Plan
    review: Review

    # execute
    case_id: str
    created_task_ids: list[str]
    status: Literal["planned", "approved", "rejected", "executed", "escalated"]

    # append-only audit trail (every node adds a line)
    audit: Annotated[list[str], operator.add]
