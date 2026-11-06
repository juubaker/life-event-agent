import os
import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from life_event_agent.checkpointer import SERDE, postgres_checkpointer
from life_event_agent.graph import build_graph
from life_event_agent.tools import case_api

TODAY = "2026-11-05"


def _start(graph, request, thread=None):
    cfg = {"configurable": {"thread_id": thread or uuid.uuid4().hex}}
    out = graph.invoke({"employee_id": "E1", "request": request, "today": TODAY}, cfg)
    return cfg, out


@pytest.fixture
def graph():
    return build_graph(InMemorySaver(serde=SERDE))


def test_pauses_at_hr_gate_with_plan(graph):
    cfg, out = _start(graph, "We had a baby on 2026-11-01")
    assert "__interrupt__" in out
    payload = out["__interrupt__"][0].value
    assert payload["kind"] == "hr_review"
    assert graph.get_state(cfg).next == ("human_review",)


def test_parallel_specialists_both_contribute(graph):
    cfg, _ = _start(graph, "We had a baby on 2026-11-01")
    sources = {f.source for f in graph.get_state(cfg).values["findings"]}
    assert sources == {"benefits", "leave"}


def test_approve_executes_and_confirms(graph):
    cfg, _ = _start(graph, "I got married 2026-10-20")
    out = graph.invoke(Command(resume={"action": "approve"}), cfg)
    assert out["status"] == "executed"
    assert out["case_id"].startswith("CASE-")
    assert out["audit"][-1].startswith("confirm:")


def test_reject_skips_side_effects(graph):
    cfg, _ = _start(graph, "I got married 2026-10-20")
    before = case_api.write_count
    out = graph.invoke(Command(resume={"action": "reject"}), cfg)
    assert out["status"] == "rejected"
    assert case_api.write_count == before


def test_off_topic_escalates_without_interrupt(graph):
    _, out = _start(graph, "How do I reset my VPN password?")
    assert out["status"] == "escalated"
    assert "__interrupt__" not in out


def test_replaying_execute_is_idempotent(graph):
    cfg, _ = _start(graph, "We had a baby on 2026-11-01")
    graph.invoke(Command(resume={"action": "approve"}), cfg)
    writes = case_api.write_count
    before_exec = next(s for s in graph.get_state_history(cfg) if s.next == ("execute",))
    graph.invoke(None, before_exec.config)  # time-travel replay
    assert case_api.write_count == writes


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL")
def test_postgres_resume_survives_process_restart():
    """Start with one saver/connection, resume with a brand-new one:
    the same thing that happens when the server restarts at the HR gate."""
    url = os.environ["TEST_DATABASE_URL"]
    thread = uuid.uuid4().hex
    with postgres_checkpointer(url) as cp:
        _, out = _start(build_graph(cp), "We had a baby on 2026-11-01", thread)
        assert "__interrupt__" in out
    with postgres_checkpointer(url) as cp:  # "new process"
        g = build_graph(cp)
        cfg = {"configurable": {"thread_id": thread}}
        assert g.get_state(cfg).next == ("human_review",)
        out = g.invoke(Command(resume={"action": "approve"}), cfg)
        assert out["status"] == "executed"
        # Pydantic models round-trip through Postgres as models, not dicts
        assert type(out["plan"]).__name__ == "Plan"


def test_malformed_llm_output_escalates_instead_of_crashing(graph, monkeypatch):
    """A model that drops a required field must route to a human, not crash."""
    from pydantic import ValidationError

    from life_event_agent import llm
    from life_event_agent.state import Classification

    class BadModel:
        def with_structured_output(self, *a, **k):
            return self

        def invoke(self, _):
            Classification.model_validate({"event_type": "unknown"})  # no confidence

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm, "_chat_model", lambda: BadModel())
    _, out = _start(graph, "Something big changed at home")
    assert out["status"] == "escalated"
    assert "classifier output invalid" in out["classification"].rationale
