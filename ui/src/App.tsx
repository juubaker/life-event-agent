import { useCallback, useEffect, useRef, useState } from "react";
import {
  type Config, type NodeName, type Plan, type StreamEvent, type Thread,
  getConfig, getThreads, isWaiting, submitRequest, submitReview,
} from "./api";
import Employee from "./Employee";
import Review from "./Review";
import RoutingSlip from "./RoutingSlip";

type Pane = "employee" | "hr" | "slip";
const POLL_MS = 3000;

const NODES: NodeName[] = ["classify", "escalate", "benefits", "leave", "plan",
  "human_review", "execute", "confirm"];

/** A thread the server hasn't saved yet: shows "classify" running right away. */
function placeholder(thread_id: string, employee_id: string, request: string): Thread {
  const nodes = Object.fromEntries(NODES.map((n) => [n, n === "classify" ? "active" : "pending"]));
  return {
    thread_id, employee_id, request, today: "", received_at: new Date().toISOString(), status: "running", next: ["classify"],
    nodes: nodes as Thread["nodes"], classification: null, findings: [], plan: null,
    review: null, case_id: null, created_task_ids: [], audit: [], updated_at: null,
  };
}

function providerText(c: Config | null): string {
  if (!c) return "";
  if (c.provider === "rules") return "Classifier: keyword rules, offline";
  if (c.provider === "ollama") return `Classifier: ${c.model} on Ollama`;
  return `Classifier: ${c.model}`;
}

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // thread being streamed
  const [error, setError] = useState<string | null>(null);
  const [employeeId, setEmployeeId] = useState("E1024");
  const [pane, setPane] = useState<Pane>("employee");
  const streaming = useRef(false);

  const upsert = useCallback((t: Thread) => {
    setThreads((prev) => {
      const i = prev.findIndex((p) => p.thread_id === t.thread_id);
      if (i < 0) return [t, ...prev];
      const next = prev.slice();
      next[i] = t;
      return next;
    });
  }, []);

  const refresh = useCallback(async () => {
    if (streaming.current) return; // the stream is the fresher source
    try {
      setThreads(await getThreads());
    } catch (e) {
      setError(`Can't reach the agent server. ${String(e)}`);
    }
  }, []);

  useEffect(() => {
    getConfig().then(setConfig).catch(() => setError("Can't reach the agent server on /api."));
    refresh();
    const id = setInterval(() => document.visibilityState === "visible" && refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const handle = (seed?: (id: string) => Thread) => (e: StreamEvent) => {
    if (e.type === "start") {
      setBusy(e.thread_id);
      setSelected(e.thread_id);
      if (seed) upsert(seed(e.thread_id));
    } else if (e.type === "step" || e.type === "done") {
      upsert(e.thread);
    } else {
      setError(e.message);
    }
  };

  const run = async (go: () => Promise<void>) => {
    setError(null);
    streaming.current = true;
    try {
      await go();
    } catch (e) {
      setError(String(e));
    } finally {
      streaming.current = false;
      setBusy(null);
      refresh();
    }
  };

  const onSubmit = (text: string) =>
    run(() => submitRequest(employeeId, text, handle((id) => placeholder(id, employeeId, text))));

  const onReview = (t: Thread, action: "approve" | "edit" | "reject", note: string, plan?: Plan) => {
    upsert({ ...t, next: ["human_review"], nodes: { ...t.nodes, human_review: "active" } });
    return run(() => submitReview(t.thread_id, { action, note, plan }, handle()));
  };

  const selectedThread = threads.find((t) => t.thread_id === selected) ?? null;
  const waiting = threads.filter(isWaiting).length;

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <h1>Life-Event Agent</h1>
          <p className="lede">The agent drafts the benefits checklist. A person approves it before anything is filed.</p>
        </div>
        {config && (
          <p className="setup">
            <span>{providerText(config)}</span>
            <span>{config.durable ? "Cases saved in Postgres" : "Cases kept in memory until restart"}</span>
          </p>
        )}
      </header>

      {error && (
        <p className="banner" role="alert">
          {error}
          <button className="link" onClick={() => setError(null)}>Dismiss</button>
        </p>
      )}

      <nav className="switcher" aria-label="Views">
        {([["employee", "Employee"], ["hr", `HR review${waiting ? ` (${waiting})` : ""}`],
          ["slip", "Routing slip"]] as [Pane, string][]).map(([p, label]) => (
          <button key={p} aria-pressed={pane === p} onClick={() => setPane(p)}>{label}</button>
        ))}
      </nav>

      <main className="desk">
        <section className="pane employee" data-active={pane === "employee" || undefined} aria-label="Employee">
          <Employee
            employeeId={employeeId}
            onEmployeeId={setEmployeeId}
            threads={threads.filter((t) => t.employee_id === employeeId)}
            busy={busy !== null}
            selected={selected}
            onSelect={setSelected}
            onSubmit={onSubmit}
          />
        </section>
        <section className="pane hr" data-active={pane === "hr" || undefined} aria-label="HR review">
          <Review
            threads={threads}
            selected={selectedThread}
            onSelect={(id) => setSelected(id)}
            busy={busy}
            onReview={onReview}
          />
        </section>
        <section className="pane slip-pane" data-active={pane === "slip" || undefined} aria-label="Routing slip">
          <RoutingSlip thread={selectedThread} durable={config?.durable ?? false} live={busy !== null && busy === selected} />
        </section>
      </main>
    </div>
  );
}
