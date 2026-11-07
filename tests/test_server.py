import json
import os

import pytest
from fastapi.testclient import TestClient

from life_event_agent import server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)  # in-memory checkpoints
    monkeypatch.setenv("LLM_PROVIDER", "rules")
    with TestClient(server.app) as c:
        yield c


def events(resp) -> list[dict]:
    assert resp.status_code == 200, resp.text
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def submit(client, text, employee="E1") -> list[dict]:
    return events(client.post("/api/requests", json={"employee_id": employee, "request": text}))


def test_submit_streams_each_node_then_pauses_for_hr(client):
    ev = submit(client, "We had a baby on 2026-11-01")
    assert ev[0]["type"] == "start" and ev[-1]["type"] == "done"
    steps = [e["node"] for e in ev if e["type"] == "step"]
    assert steps[0] == "classify" and set(steps[1:3]) == {"benefits", "leave"}
    assert steps[-1] == "plan"
    t = ev[-1]["thread"]
    assert t["nodes"]["human_review"] == "waiting"
    assert t["nodes"]["execute"] == "pending"
    assert t["plan"]["tasks"]


def test_queue_lists_waiting_case_and_review_completes_it(client):
    tid = submit(client, "I got married 2026-10-20")[-1]["thread"]["thread_id"]
    waiting = [t for t in client.get("/api/threads").json() if t["next"] == ["human_review"]]
    assert tid in {t["thread_id"] for t in waiting}

    ev = events(client.post(f"/api/threads/{tid}/review", json={"action": "approve"}))
    final = ev[-1]["thread"]
    assert [e["node"] for e in ev if e["type"] == "step"] == ["human_review", "execute", "confirm"]
    assert final["status"] == "executed" and final["case_id"].startswith("CASE-")
    assert final["nodes"]["escalate"] == "skipped"
    assert all(final["nodes"][n] == "done" for n in ["classify", "benefits", "plan", "execute"])


def test_edit_replaces_plan_before_execute(client):
    t = submit(client, "I got married 2026-10-20")[-1]["thread"]
    plan = t["plan"] | {"tasks": t["plan"]["tasks"][:1]}
    ev = events(client.post(f"/api/threads/{t['thread_id']}/review",
                            json={"action": "edit", "plan": plan, "note": "trimmed"}))
    assert len(ev[-1]["thread"]["created_task_ids"]) == 1


def test_review_rejected_when_not_waiting(client):
    t = submit(client, "How do I reset my VPN password?")[-1]["thread"]
    assert t["status"] == "escalated"
    r = client.post(f"/api/threads/{t['thread_id']}/review", json={"action": "approve"})
    assert r.status_code == 409


def test_employee_filter_and_unknown_thread(client):
    submit(client, "We had a baby on 2026-11-01", employee="E-A")
    submit(client, "We had a baby on 2026-11-01", employee="E-B")
    assert {t["employee_id"] for t in client.get("/api/threads?employee_id=E-A").json()} == {"E-A"}
    assert client.get("/api/threads/nope").status_code == 404


def test_config_reports_provider_and_storage(client):
    c = client.get("/api/config").json()
    assert c["provider"] == "rules" and c["durable"] is False


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL")
def test_queue_survives_server_restart(monkeypatch):
    """Submit on one server, restart it, approve from the queue on the new one."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("LLM_PROVIDER", "rules")
    with TestClient(server.app) as c:
        tid = submit(c, "We had a baby on 2026-11-01")[-1]["thread"]["thread_id"]
    with TestClient(server.app) as c:  # new lifespan = new connection, nothing in memory
        assert tid in {t["thread_id"] for t in c.get("/api/threads").json()}
        ev = events(c.post(f"/api/threads/{tid}/review", json={"action": "approve"}))
        assert ev[-1]["thread"]["status"] == "executed"


def test_received_at_is_set_and_stable_through_review(client):
    """The chat sorts by arrival; a review must not change it."""
    t = submit(client, "I got married 2026-10-20")[-1]["thread"]
    assert t["received_at"] and t["received_at"].endswith("+00:00")
    after = events(client.post(f"/api/threads/{t['thread_id']}/review", json={"action": "approve"}))
    assert after[-1]["thread"]["received_at"] == t["received_at"]
