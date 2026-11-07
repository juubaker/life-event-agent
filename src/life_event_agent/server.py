"""HTTP API for the UI: employee requests, the HR review queue, live graph state.

  life-event serve            # http://localhost:8000 (serves ui/dist if built)

POST endpoints stream Server-Sent Events, one per graph step, so the UI can
light up each node as it runs. Everything else is plain JSON.

Set LIFE_EVENT_STEP_DELAY_MS (say 400) to slow the stream down for a demo
with the offline rules provider, which otherwise finishes in milliseconds.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from . import llm
from .checkpointer import get_checkpointer
from .graph import build_graph
from .state import Review

NODES = ["classify", "escalate", "benefits", "leave", "plan", "human_review",
         "execute", "confirm"]
# audit-line prefix -> node (every node writes one; human_review writes "review:")
AUDIT_PREFIX = {"classify": "classify", "escalate": "escalate", "benefits": "benefits",
                "leave": "leave", "plan": "plan", "review": "human_review",
                "execute": "execute", "confirm": "confirm"}
TERMINAL = {"executed", "rejected", "escalated"}
UI_DIST = Path(__file__).resolve().parents[2] / "ui" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    with get_checkpointer() as cp:
        app.state.cp = cp
        app.state.graph = build_graph(cp)
        yield


app = FastAPI(title="Life-Event Agent", lifespan=lifespan)


# --- reading state -------------------------------------------------------------


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _thread_ids(limit: int = 100) -> list[str]:
    """Most recently updated threads first, without loading every checkpoint."""
    cp = app.state.cp
    if isinstance(cp, InMemorySaver):
        # storage: thread_id -> namespace -> checkpoint_id (time-ordered) -> ...
        latest = {t: max(ns.get("", {}), default="") for t, ns in cp.storage.items()}
        return sorted(latest, key=latest.get, reverse=True)[:limit]
    with cp.lock, cp.conn.cursor() as cur:  # PostgresSaver
        cur.execute(
            "SELECT thread_id FROM checkpoints WHERE checkpoint_ns = '' "
            "GROUP BY thread_id ORDER BY max(checkpoint_id) DESC LIMIT %s", (limit,))
        return [r["thread_id"] for r in cur.fetchall()]


def node_status(values: dict, next_nodes: tuple[str, ...]) -> dict[str, str]:
    """done | active | waiting | skipped | pending, for every node in the graph."""
    done = {AUDIT_PREFIX[line.split(":", 1)[0]] for line in values.get("audit", [])
            if line.split(":", 1)[0] in AUDIT_PREFIX}
    finished = not next_nodes and values.get("status") in TERMINAL
    out = {}
    for n in NODES:
        if n in next_nodes:
            out[n] = "waiting" if n == "human_review" else "active"
        elif n in done:
            out[n] = "done"
        else:
            out[n] = "skipped" if finished else "pending"
    return out


def thread_detail(thread_id: str) -> dict:
    snap = app.state.graph.get_state(_cfg(thread_id))
    if not snap.values:
        raise HTTPException(404, f"no thread {thread_id}")
    v = snap.values
    return jsonable_encoder({
        "thread_id": thread_id,
        "employee_id": v.get("employee_id"),
        "request": v.get("request"),
        "today": v.get("today"),
        "received_at": v.get("received_at"),
        "status": v.get("status", "running"),
        "next": list(snap.next),
        "nodes": node_status(v, snap.next),
        "classification": v.get("classification"),
        "findings": v.get("findings", []),
        "plan": v.get("plan"),
        "review": v.get("review"),
        "case_id": v.get("case_id"),
        "created_task_ids": v.get("created_task_ids", []),
        "audit": v.get("audit", []),
        "updated_at": snap.created_at,
    })


# --- streaming runs ------------------------------------------------------------


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def _stream(thread_id: str, graph_input, first: list[str]) -> Iterator[str]:
    delay = int(os.getenv("LIFE_EVENT_STEP_DELAY_MS", "0")) / 1000
    yield _sse({"type": "start", "thread_id": thread_id, "next": first})
    try:
        for chunk in app.state.graph.stream(graph_input, _cfg(thread_id),
                                            stream_mode="updates"):
            for node in chunk:
                if node == "__interrupt__":
                    continue
                if delay:
                    time.sleep(delay)
                yield _sse({"type": "step", "node": node, "thread": thread_detail(thread_id)})
        yield _sse({"type": "done", "thread": thread_detail(thread_id)})
    except Exception as e:  # surface it in the UI instead of a dropped stream
        yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"[:300]})


def _event_stream(gen: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- endpoints -----------------------------------------------------------------


class NewRequest(BaseModel):
    employee_id: str = Field(min_length=1, max_length=40)
    request: str = Field(min_length=1, max_length=2000)


@app.get("/api/config")
def config() -> dict:
    provider = llm.provider()
    model = {"anthropic": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
             "ollama": os.getenv("OLLAMA_MODEL", "qwen2.5")}.get(provider)
    return {"provider": provider, "model": model,
            "durable": not isinstance(app.state.cp, InMemorySaver),
            "today": date.today().isoformat()}


@app.get("/api/threads")
def list_threads(employee_id: str | None = None) -> list[dict]:
    out = []
    for tid in _thread_ids():
        try:
            d = thread_detail(tid)
        except HTTPException:
            continue
        if employee_id and d["employee_id"] != employee_id:
            continue
        out.append(d)
    return out


@app.get("/api/threads/{thread_id}")
def get_thread(thread_id: str) -> dict:
    return thread_detail(thread_id)


@app.post("/api/requests")
def submit(req: NewRequest) -> StreamingResponse:
    thread_id = uuid.uuid4().hex[:12]
    graph_input = {"employee_id": req.employee_id.strip(), "request": req.request.strip(),
                   "today": date.today().isoformat(),
                   "received_at": datetime.now(UTC).isoformat(timespec="seconds")}
    return _event_stream(_stream(thread_id, graph_input, ["classify"]))


@app.post("/api/threads/{thread_id}/review")
def review(thread_id: str, body: Review) -> StreamingResponse:
    snap = app.state.graph.get_state(_cfg(thread_id))
    if snap.next != ("human_review",):
        raise HTTPException(409, "this case is not waiting for review")
    if body.action == "edit" and body.plan is None:
        raise HTTPException(422, "an edit needs the edited plan")
    return _event_stream(_stream(thread_id, Command(resume=body.model_dump()),
                                 ["human_review"]))


if UI_DIST.exists():  # built UI; in dev, Vite serves it and proxies /api here
    app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")


def main() -> None:
    import argparse

    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    ap = argparse.ArgumentParser(prog="life-event-serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port)


if __name__ == "__main__":
    main()
