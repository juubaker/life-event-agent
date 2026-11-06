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

Dates and enrollment windows are computed in Python, never by the model: the classifier extracts the date phrase as written ("last Saturday") and `dates.py` resolves it. Plan rules in `tools.py` are mock data.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

docker compose up -d          # Postgres (pgvector image) on :5442
pytest                        # 38 tests; 39 with TEST_DATABASE_URL set
python evals/run_evals.py     # offline, LLM_PROVIDER=rules
```

### Durable-execution demo

Run `start` and `resume` as separate processes:

```bash
life-event start --thread demo1 "We just had a baby on 2026-11-01"
# ...paused at the HR gate; kill the terminal, restart Postgres, whatever...
life-event resume --thread demo1 --action approve
life-event history --thread demo1      # every checkpoint, newest first
```

## Evals

`evals/scenarios.jsonl` holds 12 scripted scenarios: happy paths, disambiguation (divorce vs. marriage), no-date, HITL approve/edit/reject, escalation, and two `hard` cases. Each scenario is scored on routing, specialist fan-out, deadline, final outcome, and replay idempotency.

```bash
python evals/run_evals.py                          # offline baseline (rules)
LLM_PROVIDER=ollama python evals/run_evals.py      # local model
LLM_PROVIDER=anthropic python evals/run_evals.py   # Claude
python evals/run_evals.py --tag hitl --json out.json --min-pass 1.0   # CI gate
```

The two `hard` scenarios ("twins arrived", "tied the knot last Saturday") fail on the rules provider by design. They show what the LLM classifier adds and give the eval suite something real to measure.

## Results

All runs use the same 12 scenarios and the same graph; only the classifier changes.

| Provider | Model | Fully passing | Routing | Deadline | Hard cases | Latency / scenario |
|---|---|---|---|---|---|---|
| `rules` | keyword regex | 10/12 (83%) | 10/12 | 10/12 | 0/2 | ~3 ms |
| `ollama` | qwen2.5 (7B, local) | **12/12 (100%)** | 12/12 | 12/12 | 2/2 | ~1.3 s |
| `anthropic` | claude-sonnet-5-5 | _pending_ | | | | |

Replay idempotency passed in every run where `execute` ran. Ollama latency was measured on an M-series MacBook Air.

**Stability.** A single 12/12 run is one sample. A small local model can flip a borderline case between runs, so the number to report is the pass rate across repeated runs:

```bash
for i in 1 2 3 4 5; do LLM_PROVIDER=ollama python evals/run_evals.py | tail -1; done
```

### What the evals caught

Getting from a first local-model run to 12/12 took three fixes. Each one moved responsibility out of the model and into code that can be tested.

| Symptom in the eval | Root cause | Fix |
|---|---|---|
| Graph crashed on `ambiguous-01` | Tool-calling output let qwen2.5 omit the required `confidence` field | `json_schema` constrained decoding on every provider; invalid output now escalates to HR instead of raising |
| `hard-relative-date` deadline off by 3 days | The model resolved "last Saturday" (Thu Nov 5) to Nov 3, a Tuesday | Model returns the date phrase verbatim; `dates.py` resolves it deterministically |
| Every dated scenario missed its deadline | `date_text` had a default, so constrained decoding treated it as optional and skipped it | Field made required (nullable), plus a fallback that scans the request text for a resolvable date |

A prompt edit also fixed one regression along the way. After the date-rules change, "relocating next month" came back `unknown` because the model read vague timing as a vague event. The prompt now defines each event type and states that timing never makes an event unknown.

## Next steps

- [ ] `benefits_specialist`: swap the rules table for pgvector RAG over plan docs, with citations
- [ ] `plan`: LLM rewrites `employee_message` (tone only; facts locked)
- [ ] Clarify loop: low-confidence requests `interrupt()` back to the *employee* instead of escalating
- [ ] Wrap specialists as subgraphs; add a supervisor node
- [ ] LangSmith / OpenTelemetry tracing: per-node latency and cost
- [ ] React UI: employee chat, HR approval queue, live graph state
- [ ] Grow evals to ~30 scenarios; add LLM-judge scoring for `employee_message`
