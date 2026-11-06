"""Graph nodes. Each node takes state and returns a partial update.

Rule of thumb for nodes that hit `interrupt()`: everything before the
interrupt re-runs on resume, so keep that part side-effect free.
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from . import llm, tools
from .state import AgentState, Finding, Plan, PlanTask, Review

CONFIDENCE_FLOOR = 0.6
LEAVE_EVENTS = {"birth_adoption", "medical_leave"}


# --- classify / route --------------------------------------------------------


def classify(state: AgentState) -> AgentState:
    c = llm.classify(state["request"], state["today"])
    specialists = []
    if c.event_type != "unknown" and c.confidence >= CONFIDENCE_FLOOR:
        specialists = ["benefits"] + (["leave"] if c.event_type in LEAVE_EVENTS else [])
    return {
        "classification": c,
        "specialists": specialists,
        "audit": [f"classify: {c.event_type} ({c.confidence:.2f}) {c.rationale}"],
    }


def route_after_classify(state: AgentState) -> list[str] | str:
    """Fan out to every needed specialist in parallel, or escalate."""
    return state["specialists"] or "escalate"


def escalate(state: AgentState) -> AgentState:
    return {"status": "escalated", "audit": ["escalate: routed to HR queue"]}


# --- specialists (run in parallel; results merge via the findings reducer) ---


def benefits_specialist(state: AgentState) -> AgentState:
    c = state["classification"]
    event_day = tools.parse_date(c.event_date, state["today"])
    deadline = tools.enrollment_deadline(event_day).isoformat()
    actions = tools.benefits_rules(c.event_type)
    # TODO(rag): retrieve plan-document passages from pgvector and cite them
    return {
        "findings": [
            Finding(
                source="benefits",
                summary=f"{len(actions)} benefit changes; window closes {deadline}",
                actions=actions,
                deadline=deadline,
            )
        ],
        "audit": [f"benefits: {len(actions)} actions, deadline {deadline}"],
    }


def leave_specialist(state: AgentState) -> AgentState:
    c = state["classification"]
    policy = tools.leave_policy(c.event_type)
    if policy is None:
        return {"audit": ["leave: no policy for event"]}
    return {
        "findings": [
            Finding(
                source="leave",
                summary=f"{policy['label']}: up to {policy['weeks']} weeks",
                actions=[
                    f"Submit {policy['label'].lower()} request "
                    f"({policy['notice_days']} days' notice when foreseeable)",
                    "Agree on coverage plan with manager",
                ],
            )
        ],
        "audit": [f"leave: {policy['label']} {policy['weeks']}w"],
    }


# --- plan / human gate -------------------------------------------------------


def plan(state: AgentState) -> AgentState:
    c = state["classification"]
    tasks: list[PlanTask] = []
    for f in sorted(state.get("findings", []), key=lambda f: f.source):
        for i, action in enumerate(f.actions):
            tasks.append(
                PlanTask(task_id=f"{f.source}-{i}", title=action, due=f.deadline)
            )
    tasks.append(PlanTask(task_id="hr-0", title="Verify supporting documents", owner="hr"))
    msg = (
        f"Here is your checklist for this {c.event_type.replace('_', ' ')} event. "
        + " ".join(f.summary + "." for f in state.get("findings", []))
    )
    # TODO(llm): have the model rewrite `msg` in a warm tone, facts unchanged
    p = Plan(event_type=c.event_type, tasks=tasks, employee_message=msg)
    return {"plan": p, "status": "planned", "audit": [f"plan: {len(tasks)} tasks"]}


def human_review(state: AgentState) -> AgentState:
    """Pause for an HR specialist. Resume with Command(resume=Review-dict)."""
    raw = interrupt(
        {
            "kind": "hr_review",
            "employee_id": state["employee_id"],
            "plan": state["plan"].model_dump(),
            "allowed_actions": ["approve", "edit", "reject"],
        }
    )
    review = Review.model_validate(raw)
    update: AgentState = {"review": review, "audit": [f"review: {review.action}"]}
    if review.action == "edit":
        if review.plan is None:
            raise ValueError("edit requires a plan")
        update["plan"] = review.plan
    update["status"] = "rejected" if review.action == "reject" else "approved"
    return update


def route_after_review(state: AgentState) -> str:
    return "execute" if state["status"] == "approved" else "end"


# --- side effects ------------------------------------------------------------


def execute(state: AgentState, config: RunnableConfig) -> AgentState:
    """Write the case and tasks. Idempotency keys come from thread_id, so a
    replay of this node (crash/resume, time travel) is a no-op upstream."""
    thread_id = config["configurable"]["thread_id"]
    p = state["plan"]
    case_id = tools.case_api.create_case(
        f"{thread_id}:case", state["employee_id"], p.event_type
    )
    created = [
        tools.case_api.create_task(f"{thread_id}:{t.task_id}", case_id, t.model_dump())
        for t in p.tasks
    ]
    return {
        "case_id": case_id,
        "created_task_ids": created,
        "status": "executed",
        "audit": [f"execute: {case_id} with {len(created)} tasks"],
    }


def confirm(state: AgentState) -> AgentState:
    # TODO: notify employee (email/Slack); for now the audit line is the receipt
    return {"audit": [f"confirm: sent checklist for {state['case_id']}"]}
