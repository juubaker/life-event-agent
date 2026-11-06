"""CLI for running the agent.

  life-event start  --employee E123 "We just had a baby on 2026-11-01"
  life-event resume --thread <id> --action approve
  life-event history --thread <id>

With DATABASE_URL set, `start` and `resume` can be separate processes,
which is the durable-execution demo: kill the process at the HR gate,
come back later, resume.
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import date

from dotenv import load_dotenv
from langgraph.types import Command

from .checkpointer import get_checkpointer
from .graph import build_graph


def _show(result: dict) -> None:
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print("\n== Paused for HR review ==")
        for t in payload["plan"]["tasks"]:
            due = f"  (due {t['due']})" if t["due"] else ""
            print(f"  [{t['owner']}] {t['task_id']}: {t['title']}{due}")
        return
    print(f"\n== status: {result.get('status')} ==")
    if result.get("case_id"):
        print(f"case {result['case_id']}: {len(result['created_task_ids'])} tasks")
    for line in result.get("audit", []):
        print("  ", line)


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="life-event")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start")
    s.add_argument("request")
    s.add_argument("--employee", default="E123")
    s.add_argument("--thread", default=None)

    r = sub.add_parser("resume")
    r.add_argument("--thread", required=True)
    r.add_argument("--action", choices=["approve", "reject"], required=True)
    r.add_argument("--note", default="")

    h = sub.add_parser("history")
    h.add_argument("--thread", required=True)

    args = ap.parse_args()

    with get_checkpointer() as cp:
        graph = build_graph(cp)

        if args.cmd == "start":
            thread = args.thread or uuid.uuid4().hex[:12]
            cfg = {"configurable": {"thread_id": thread}}
            print(f"thread: {thread}")
            _show(graph.invoke(
                {"employee_id": args.employee, "request": args.request,
                 "today": date.today().isoformat()},
                cfg,
            ))
        elif args.cmd == "resume":
            cfg = {"configurable": {"thread_id": args.thread}}
            _show(graph.invoke(
                Command(resume={"action": args.action, "note": args.note}), cfg
            ))
        else:  # history: every checkpoint, newest first (time-travel targets)
            cfg = {"configurable": {"thread_id": args.thread}}
            for snap in graph.get_state_history(cfg):
                print(json.dumps({
                    "checkpoint_id": snap.config["configurable"]["checkpoint_id"],
                    "next": list(snap.next),
                    "status": snap.values.get("status"),
                }))


if __name__ == "__main__":
    main()
