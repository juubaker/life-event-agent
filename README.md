# Life-Event Agent

A LangGraph agent that turns an employee's life-event request ("We just had a baby. What do I need to do?") into an HR case with a dated checklist. An HR specialist approves, edits or rejects the plan before anything is written.

```
START -> classify -+-> benefits --+-> plan -> human_review -+-> execute -> confirm -> END
                   +-> leave -----+   (interrupt)          +-> END (reject)
                   +-> escalate -> END
```

## What it demonstrates

| LangGraph feature | Where |
|---|---|
| Typed state with reducers for parallel writes | `state.py` (`findings`, `audit`) |
| Conditional fan-out to parallel specialists, join at `plan` | `graph.py`, `nodes.route_after_classify` |
| Human-in-the-loop with `interrupt()` / `Command(resume=...)` | `nodes.human_review` |
| Durable checkpoints in Postgres (resume across process restarts) | `checkpointer.py` |
| Time travel: replay from any checkpoint | `cli.py history`, eval `idempotent` check |
| Idempotent side effects keyed on `thread_id` | `nodes.execute`, `tools.MockCaseAPI` |
| Swappable providers: Anthropic, Ollama, offline rules | `llm.py` |
| Graceful degradation: malformed model output escalates to a human | `llm.classify` |
| Model rewrites the employee message; code checks every fact survived | `message.py` |
| Streaming each graph step to a UI over Server-Sent Events | `server.py`, `ui/` |

Dates and enrollment windows are computed in Python, never by the model: the classifier extracts the date phrase as written ("last Saturday") and `dates.py` resolves it. Plan rules in `tools.py` are mock data.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

docker compose up -d          # Postgres (pgvector image) on :5442
pytest                        # 57 tests; 59 with TEST_DATABASE_URL set
python evals/run_evals.py     # offline, LLM_PROVIDER=rules
```

### The UI

Three panes on one page: the employee's chat, the HR review queue, and a routing slip that ticks off each graph step as it runs.

```bash
cd ui && npm install && npm run build && cd ..
life-event-serve                          # http://localhost:8000
```

The server reads the same `.env`, so `DATABASE_URL` makes the queue survive a restart and `LLM_PROVIDER` picks the classifier. With the offline rules provider every step finishes in milliseconds; set `LIFE_EVENT_STEP_DELAY_MS=400` to slow the stream down enough to watch it. For frontend work, run `life-event-serve` and `npm run dev` side by side (Vite proxies `/api` to port 8000).

What the employee sees depends on the case: "HR is reviewing" while it waits, the checklist and warm message once approved, and a hand-off message when it escalates. They never see a draft plan.

### Durable-execution demo

Run `start` and `resume` as separate processes:

```bash
life-event start --thread demo1 "We just had a baby on 2026-11-01"
# ...paused at the HR gate; kill the terminal, restart Postgres, whatever...
life-event resume --thread demo1 --action approve
life-event history --thread demo1      # every checkpoint, newest first
```

## Evals

`evals/scenarios.jsonl` holds 30 scripted scenarios. Each one runs through the real graph with a scripted HR decision and is scored on routing, specialist fan-out, deadline, final outcome, and replay idempotency.

| Tag | Count | What it covers |
|---|---|---|
| `happy-path` | 6 | One per event type, explicit dates |
| `dates` | 6 | "yesterday", "Nov 3rd", "3 days ago", "this morning", a year boundary, a leap year |
| `hitl` | 3 | Approve with edits, two rejections |
| `escalation` | 6 | IT and payroll questions, a plan switch with no life event, a vague request, plus two of the hard cases |
| other | 3 | Divorce versus marriage wording, no date given, a deadline that has already passed |
| `hard` | 7 | Need judgment, not keywords: "twins arrived", "tied the knot", a sister's baby (not the employee's event), a spouse's surgery, domestic partnership, foster placement, Spanish |
| `safety` | 1 | A prompt injection asking to skip review; the case must still stop at the HR gate |

Tags overlap, so the counts add up to more than 30. Expected dates were worked out by hand, not by running `dates.py`, so they check the resolver rather than copy it.

```bash
python evals/run_evals.py                          # offline baseline (rules)
LLM_PROVIDER=ollama python evals/run_evals.py      # local model
LLM_PROVIDER=anthropic python evals/run_evals.py   # Claude
python evals/run_evals.py --tag hitl --json out.json --min-pass 1.0   # CI gate
```

The `hard` scenarios are where keyword rules fail by design. They show what the model adds and give the suite something real to measure.

### Pass rates across repeated runs

One perfect run is a single sample. `run_repeats.py` runs the whole suite several times per provider and reports the mean, the range, the hard-case rate, how often the model's message rewrite passed the fact check, and which scenarios are flaky:

```bash
python evals/run_repeats.py --runs 5 --out evals/results   # rules, ollama, anthropic
```

A provider that can't answer a test call (no key, no credits, Ollama not running) is listed as not run, with the reason, rather than scored as 30 failures. `evals/results/summary.md` is a markdown table ready to paste below. Five Anthropic runs is roughly 250 short model calls.

## Results

| Provider | Model | Suite | Fully passing | Hard cases | Time / scenario |
|---|---|---|---|---|---|
| `rules` | keyword regex | 30 scenarios, 5 runs | 24/30 (80%), every run | 1 of 7 | ~10 ms |
| `ollama` | qwen2.5 (7B, local) | earlier 12-scenario suite, 1 run | 12/12 | 2 of 2 | ~1.3 s |
| `ollama` | qwen2.5 (7B, local) | 30 scenarios | _not run yet_ | | |
| `anthropic` | claude-sonnet-5-5 | 30 scenarios | _not run yet_ | | |

The rules provider fails exactly the six `hard` cases it can't read. It passes `hard-spouse-surgery` only by luck: "wife" and "surgery" match two event types, so it escalates as ambiguous. Replay idempotency has passed in every run where `execute` ran. Ollama latency was measured on an M-series MacBook Air.

### What the evals caught

On the first 12-scenario suite, getting the local model to 12/12 took three fixes. Each one moved responsibility out of the model and into code that can be tested.

| Symptom in the eval | Root cause | Fix |
|---|---|---|
| Graph crashed on `ambiguous-01` | Tool-calling output let qwen2.5 omit the required `confidence` field | `json_schema` constrained decoding on every provider; invalid output now escalates to HR instead of raising |
| `hard-relative-date` deadline off by 3 days | The model resolved "last Saturday" (Thu Nov 5) to Nov 3, a Tuesday | Model returns the date phrase verbatim; `dates.py` resolves it deterministically |
| Every dated scenario missed its deadline | `date_text` had a default, so constrained decoding treated it as optional and skipped it | Field made required (nullable), plus a fallback that scans the request text for a resolvable date |

A prompt edit also fixed one regression along the way. After the date-rules change, "relocating next month" came back `unknown` because the model read vague timing as a vague event. The prompt now defines each event type and states that timing never makes an event unknown.

## The employee message

The plan node builds a plain, correct message from the plan's facts (deadline, weeks of leave, notice period). The model then rewrites it to sound human, and `message.py` checks the result before using it: every locked fact must appear verbatim, and no number or month name may appear that wasn't in the original. A rewrite that fails, or a model error, falls back to the template, and the reason is shown to the HR reviewer. The model only ever sees the template, never the employee's own words, so text in the request can't steer the message.

## Next steps

- [ ] Run `run_repeats.py` against Ollama and Claude on the 30-scenario suite and fill in the table
- [ ] `benefits_specialist`: swap the rules table for pgvector RAG over plan docs, with citations
- [ ] Clarify loop: low-confidence requests `interrupt()` back to the *employee* instead of escalating
- [ ] Wrap specialists as subgraphs; add a supervisor node
- [ ] LangSmith / OpenTelemetry tracing: per-node latency and cost
- [ ] LLM-judge scoring for the tone of `employee_message`
- [x] React UI: employee chat, HR approval queue, live graph state
- [x] Model rewrites `employee_message` with facts locked
- [x] Grow evals to 30 scenarios, with pass rates across repeated runs
