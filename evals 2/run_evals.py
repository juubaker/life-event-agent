"""Eval runner: scripted life-event scenarios through the real graph.

  python evals/run_evals.py                 # rules provider (offline)
  LLM_PROVIDER=anthropic python evals/run_evals.py
  python evals/run_evals.py --tag hard --json results.json

Checks per scenario:
  routing     classified event_type matches
  specialists the parallel fan-out hit exactly the expected specialists
  deadline    deterministic enrollment deadline is correct
  outcome     final status after the scripted HR review
  idempotent  replaying `execute` from its checkpoint creates no new writes
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from life_event_agent import llm  # noqa: E402
from life_event_agent.checkpointer import SERDE  # noqa: E402
from life_event_agent.graph import build_graph  # noqa: E402
from life_event_agent.tools import case_api  # noqa: E402

import os  # noqa: E402

from dotenv import dotenv_values  # noqa: E402

# Fill unset or empty vars from .env; anything set on the command line wins.
for _k, _v in dotenv_values(Path(__file__).resolve().parents[1] / ".env").items():
    if _v and not os.environ.get(_k):
        os.environ[_k] = _v

SCENARIOS = Path(__file__).with_name("scenarios.jsonl")
CHECKS = ["routing", "specialists", "deadline", "outcome", "idempotent"]


def run_scenario(graph, sc: dict) -> dict:
    cfg = {"configurable": {"thread_id": f"eval-{sc['id']}"}}
    exp = sc["expect"]
    t0 = time.perf_counter()

    out = graph.invoke(
        {"employee_id": "E-EVAL", "request": sc["request"], "today": sc["today"]}, cfg
    )
    if "__interrupt__" in out and sc.get("review"):
        out = graph.invoke(Command(resume=sc["review"]), cfg)

    state = graph.get_state(cfg).values
    findings = state.get("findings", [])
    deadline = next((f.deadline for f in findings if f.deadline), None)

    res: dict = {
        "routing": state["classification"].event_type == exp["event_type"],
        "specialists": sorted(state.get("specialists", [])) == sorted(exp["specialists"]),
        "deadline": ("deadline" not in exp) or deadline == exp["deadline"],
        "outcome": state.get("status") == exp["final_status"],
        "idempotent": None,  # only meaningful when execute ran
    }
    if "task_count" in exp:
        res["outcome"] = res["outcome"] and len(state.get("created_task_ids", [])) == exp["task_count"]

    if state.get("status") == "executed":
        # Time travel: find the checkpoint just before `execute`, replay from it.
        before = next(
            s for s in graph.get_state_history(cfg) if s.next == ("execute",)
        )
        writes = case_api.write_count
        graph.invoke(None, before.config)
        res["idempotent"] = case_api.write_count == writes

    res["ms"] = round((time.perf_counter() - t0) * 1000)
    res["got"] = {
        "event_type": state["classification"].event_type,
        "deadline": deadline,
        "status": state.get("status"),
    }
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", help="only run scenarios with this tag")
    ap.add_argument("--json", help="write per-scenario results to this path")
    ap.add_argument("--min-pass", type=float, default=0.0,
                    help="exit 1 if overall pass rate is below this (CI gate)")
    args = ap.parse_args()

    scenarios = [json.loads(l) for l in SCENARIOS.read_text().splitlines() if l.strip()]
    if args.tag:
        scenarios = [s for s in scenarios if args.tag in s.get("tags", [])]

    graph = build_graph(InMemorySaver(serde=SERDE))
    results = []
    print(f"provider={llm.provider()}  scenarios={len(scenarios)}\n")
    print(f"{'id':<22}" + "".join(f"{c:<12}" for c in CHECKS) + "ms")
    for sc in scenarios:
        try:
            r = run_scenario(graph, sc)
        except Exception as e:  # one bad scenario shouldn't kill the run
            r = {c: False for c in CHECKS} | {"ms": 0, "error": f"{type(e).__name__}: {e}"[:200]}
            print(f"  ! {sc['id']}: {r['error']}")
        r["id"] = sc["id"]
        results.append(r)
        cells = "".join(
            f"{('-' if r[c] is None else 'PASS' if r[c] else 'FAIL'):<12}" for c in CHECKS
        )
        print(f"{sc['id']:<22}{cells}{r['ms']}")

    print()
    totals = {}
    for c in CHECKS:
        vals = [r[c] for r in results if r[c] is not None]
        totals[c] = sum(vals) / len(vals) if vals else 1.0
        print(f"{c:<12} {sum(vals)}/{len(vals)}  ({totals[c]:.0%})")
    full = sum(all(r[c] in (True, None) for c in CHECKS) for r in results)
    overall = full / len(results) if results else 1.0
    print(f"\nscenarios fully passing: {full}/{len(results)} ({overall:.0%})")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"provider": llm.provider(), "totals": totals, "results": results}, indent=2
        ))
    return 1 if overall < args.min_pass else 0


if __name__ == "__main__":
    sys.exit(main())
