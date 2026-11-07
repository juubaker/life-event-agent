import { type NodeName, type NodeState, type Thread, humanDates, statusLabel } from "./api";

// The graph, drawn as an interoffice routing slip: each desk the case visits,
// ticked off as it runs, crossed out when the case never goes there.
const ROWS: NodeName[][] = [
  ["classify"], ["escalate"], ["benefits", "leave"], ["plan"], ["human_review"], ["execute"], ["confirm"],
];
const NAME: Record<NodeName, string> = {
  classify: "Classify the request", escalate: "Escalate to an HR specialist",
  benefits: "Benefits", leave: "Leave", plan: "Draft the plan", human_review: "HR review",
  execute: "File the case", confirm: "Confirm with the employee",
};
const STATE_WORDS: Record<NodeState, string> = {
  done: "done", active: "running", waiting: "waiting for a person", skipped: "not needed", pending: "not yet",
};
const AUDIT: Record<NodeName, string> = {
  classify: "classify", escalate: "escalate", benefits: "benefits", leave: "leave",
  plan: "plan", human_review: "review", execute: "execute", confirm: "confirm",
};

function Mark({ s }: { s: NodeState }) {
  if (s === "done")
    return <svg className="mark" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10.5l4 4 8-9" /></svg>;
  if (s === "active") return <span className="mark pulse" aria-hidden="true" />;
  if (s === "waiting") return <span className="mark hold" aria-hidden="true" />;
  return <span className="mark empty-mark" aria-hidden="true" />;
}

function Step({ node, state, notes }: { node: NodeName; state: NodeState; notes: string[] }) {
  return (
    <div className="step" data-state={state}>
      <Mark s={state} />
      <div className="step-body">
        <span className="step-name">
          {NAME[node]}
          <span className="sr-only">, {STATE_WORDS[state]}</span>
        </span>
        {state === "waiting" && <span className="stamp">Waiting for HR</span>}
        {notes.map((n, i) => <span className="step-note" key={i}>{n}</span>)}
      </div>
    </div>
  );
}

export default function RoutingSlip({ thread, durable, live }: { thread: Thread | null; durable: boolean; live: boolean }) {
  if (!thread) {
    return (
      <div className="slip-inner">
        <div className="pane-head"><h2>Routing slip</h2></div>
        <p className="empty">Select a case to follow it through the agent, one step at a time.</p>
      </div>
    );
  }
  const REVIEW_WORDS: Record<string, string> = {
    approve: "approved", edit: "approved with edits", reject: "rejected",
  };
  const notes = (n: NodeName) =>
    thread.audit
      .filter((l) => l.startsWith(`${AUDIT[n]}:`))
      .map((l) => l.slice(l.indexOf(":") + 1).trim())
      .map((l) => (n === "human_review" ? REVIEW_WORDS[l] ?? l : humanDates(l)))
      .concat(n === "human_review" && thread.review?.note ? [`“${thread.review.note}”`] : []);
  const s = statusLabel(thread);

  return (
    <div className="slip-inner">
      <div className="pane-head">
        <h2>Routing slip</h2>
        <span className={`chip ${s.tone}`}>{live ? "Running" : s.text}</span>
      </div>
      <div className="slip" aria-live="polite">
        <p className="slip-ref">Case file {thread.case_id ?? thread.thread_id}</p>
        {ROWS.map((r) =>
          r.length === 1 ? (
            <Step key={r[0]} node={r[0]} state={thread.nodes[r[0]]} notes={notes(r[0])} />
          ) : (
            <div className="parallel" key="parallel">
              <span className="parallel-label">Specialists, side by side</span>
              <div className="parallel-steps">
                {r.map((n) => <Step key={n} node={n} state={thread.nodes[n]} notes={notes(n)} />)}
              </div>
            </div>
          ),
        )}
      </div>
      <p className="fine">
        {durable
          ? "Saved to Postgres after every step, so a restart picks up where it left off."
          : "Kept in memory. Set DATABASE_URL to survive a restart."}
      </p>
    </div>
  );
}
