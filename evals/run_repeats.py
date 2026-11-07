"""Run the eval suite several times per provider and report pass rates.

One 30/30 run is a single sample: a local model at temperature 0 can still
flip a borderline case. This runs the whole suite N times per provider and
reports the mean, the range, and which scenarios are flaky.

  python evals/run_repeats.py                                # rules + ollama + anthropic, 5 runs
  python evals/run_repeats.py --providers ollama --runs 10
  python evals/run_repeats.py --out evals/results            # also write JSON + markdown

A provider that can't answer a test call (no key, no credits, Ollama not
running) is reported as unavailable and skipped, not scored as all-fail.
Cost note: each Anthropic run is ~2 model calls per scenario that reaches
the plan (classify + message rewrite), so 5 runs of 30 is roughly 250 calls.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_evals  # noqa: E402  (also loads .env and puts src/ on the path)
from life_event_agent import llm  # noqa: E402

CHECKS = run_evals.CHECKS


def preflight(provider: str) -> str | None:
    """None if the provider answers a real classify call, else the reason."""
    os.environ["LLM_PROVIDER"] = provider
    llm._chat_model.cache_clear()
    if provider == "rules":
        return None
    try:
        llm._chat_model()  # key check, model construction
        out = llm._chat_model().with_structured_output(
            llm._LLMOutput, method="json_schema"
        ).invoke([("system", llm.CLASSIFY_SYSTEM), ("user", "We had a baby on 2026-11-01")])
        return None if out.event_type else "empty response"
    except Exception as e:  # auth, billing, connection refused, missing model...
        msg = str(e)
        for needle, why in [
            ("ANTHROPIC_API_KEY is empty", "no API key"),
            ("credit balance", "no API credits"),
            ("authentication", "API key rejected"),
            ("Connection refused", "server not reachable"),
            ("ConnectError", "server not reachable"),
            ("not found", "model not pulled (ollama pull)"),
        ]:
            if needle.lower() in f"{type(e).__name__} {msg}".lower():
                return why
        return f"{type(e).__name__}: {msg.splitlines()[0][:120]}"


def summarize(provider: str, runs: list[list[dict]]) -> dict:
    n = len(runs[0])
    full = [sum(run_evals.passed(r) for r in run) for run in runs]
    per_check = {}
    for c in CHECKS:
        vals = [r[c] for run in runs for r in run if r[c] is not None]
        per_check[c] = sum(vals) / len(vals) if vals else 1.0
    per_scenario = {
        sid: sum(run_evals.passed(r) for run in runs for r in run if r["id"] == sid)
        for sid in (r["id"] for r in runs[0])
    }
    sources = [r["got"]["message_source"] for run in runs for r in run
               if r.get("got", {}).get("message_source")]
    hard = [r for run in runs for r in run if "hard" in r.get("tags", [])]
    return {
        "provider": provider,
        "model": _model_name(provider),
        "runs": len(runs),
        "scenarios": n,
        "full_pass_per_run": full,
        "mean_pass_rate": statistics.mean(full) / n,
        "hard_pass_rate": (sum(run_evals.passed(r) for r in hard) / len(hard)) if hard else None,
        "per_check": per_check,
        "flaky": sorted(s for s, k in per_scenario.items() if 0 < k < len(runs)),
        "always_fail": sorted(s for s, k in per_scenario.items() if k == 0),
        "rewrite_accept_rate": (sources.count("llm") / len(sources)) if sources and provider != "rules" else None,
        "median_ms": statistics.median(r["ms"] for run in runs for r in run),
    }


def _model_name(provider: str) -> str:
    return {
        "rules": "keyword regex",
        "ollama": os.getenv("OLLAMA_MODEL", "qwen2.5"),
        "anthropic": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
    }.get(provider, "?")


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def markdown(summaries: list[dict], skipped: dict[str, str]) -> str:
    lines = [
        "| Provider | Model | Runs | Fully passing (mean) | Range | Hard cases | "
        "Message rewrite kept | Median time / scenario |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lo, hi = min(s["full_pass_per_run"]), max(s["full_pass_per_run"])
        mean = statistics.mean(s["full_pass_per_run"])
        ms = s["median_ms"]
        t = f"{ms:.0f} ms" if ms < 1000 else f"{ms / 1000:.1f} s"
        lines.append(
            f"| `{s['provider']}` | {s['model']} | {s['runs']} | "
            f"{mean:.1f}/{s['scenarios']} ({s['mean_pass_rate']:.0%}) | {lo}–{hi} | "
            f"{_pct(s['hard_pass_rate'])} | {_pct(s['rewrite_accept_rate'])} | {t} |"
        )
    for p, why in skipped.items():
        lines.append(f"| `{p}` | {_model_name(p)} | 0 | not run: {why} | | | | |")
    notes = []
    for s in summaries:
        if s["flaky"]:
            notes.append(f"- `{s['provider']}` flaky: {', '.join(s['flaky'])}")
        if s["always_fail"]:
            notes.append(f"- `{s['provider']}` always fails: {', '.join(s['always_fail'])}")
    return "\n".join(lines + ([""] + notes if notes else [])) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="rules,ollama,anthropic")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--tag", help="only scenarios with this tag")
    ap.add_argument("--out", help="directory for summary.md and results.json")
    args = ap.parse_args()

    scenarios = run_evals.load_scenarios(args.tag)
    summaries, skipped, raw = [], {}, {}
    for provider in [p.strip() for p in args.providers.split(",") if p.strip()]:
        why = preflight(provider)
        if why:
            print(f"{provider}: unavailable, skipping ({why})")
            skipped[provider] = why
            continue
        runs = []
        for i in range(args.runs):
            t0 = time.perf_counter()
            results = run_evals.run_suite(scenarios, run=f"{provider}-{i}")
            runs.append(results)
            ok = sum(run_evals.passed(r) for r in results)
            print(f"{provider} run {i + 1}/{args.runs}: {ok}/{len(results)} "
                  f"({time.perf_counter() - t0:.1f}s)")
        summaries.append(summarize(provider, runs))
        raw[provider] = runs

    md = markdown(summaries, skipped)
    print("\n" + md)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.md").write_text(md)
        (out / "results.json").write_text(json.dumps(
            {"summaries": summaries, "skipped": skipped, "runs": raw}, indent=2, default=str
        ))
        print(f"wrote {out / 'summary.md'} and {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
