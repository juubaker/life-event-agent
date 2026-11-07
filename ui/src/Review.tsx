import { useEffect, useMemo, useState } from "react";
import { type Plan, type PlanTask, type Thread, EVENT_LABEL, day, humanDates, isWaiting, statusLabel } from "./api";

type Filter = "waiting" | "all";

function Queue(props: { threads: Thread[]; selected: Thread | null; onSelect: (id: string) => void }) {
  const waiting = props.threads.filter(isWaiting);
  const [filter, setFilter] = useState<Filter>("waiting");
  const rows = filter === "waiting" ? waiting : props.threads;

  return (
    <div className="queue">
      <div className="pane-head">
        <h2>HR review</h2>
        <div className="segmented" role="group" aria-label="Show">
          <button aria-pressed={filter === "waiting"} onClick={() => setFilter("waiting")}>
            Waiting ({waiting.length})
          </button>
          <button aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
            All cases ({props.threads.length})
          </button>
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="empty">
          {filter === "waiting"
            ? "Nothing waiting. A case lands here once the agent has drafted a plan."
            : "No cases yet. Send a request from the employee view to start one."}
        </p>
      ) : (
        <ul className="ledger">
          {rows.map((t) => {
            const s = statusLabel(t);
            return (
              <li key={t.thread_id}>
                <button
                  className="ledger-row"
                  aria-current={t.thread_id === props.selected?.thread_id || undefined}
                  onClick={() => props.onSelect(t.thread_id)}
                >
                  <span className="ledger-text">{t.request}</span>
                  <span className="ledger-meta">
                    <span>{t.employee_id}</span>
                    {t.classification && <span>{EVENT_LABEL[t.classification.event_type]}</span>}
                  </span>
                  <span className={`chip ${s.tone}`}>{s.text}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Facts({ t }: { t: Thread }) {
  const c = t.classification;
  if (!c) return null;
  return (
    <dl className="facts">
      <div>
        <dt>Event</dt>
        <dd>{EVENT_LABEL[c.event_type]}</dd>
      </div>
      <div>
        <dt>Event date</dt>
        <dd>
          {c.event_date ? day(c.event_date) : "Not given"}
          {c.date_text && c.date_text !== c.event_date && (
            <span className="as-written">written as “{c.date_text}”</span>
          )}
        </dd>
      </div>
      <div>
        <dt>Confidence</dt>
        <dd>{Math.round(c.confidence * 100)}%</dd>
      </div>
      <div className="wide">
        <dt>Why</dt>
        <dd>{c.rationale || "No rationale given"}</dd>
      </div>
    </dl>
  );
}

function TaskEditor(props: { tasks: PlanTask[]; onChange: (t: PlanTask[]) => void; disabled: boolean }) {
  const set = (i: number, patch: Partial<PlanTask>) =>
    props.onChange(props.tasks.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  return (
    <ol className="tasks">
      {props.tasks.map((t, i) => (
        <li className="task-row" key={t.task_id}>
          <input className="task-title" aria-label={`Task ${i + 1}`} value={t.title} disabled={props.disabled}
            placeholder="Describe the task" onChange={(e) => set(i, { title: e.target.value })} />
          <div className="task-meta">
            <label className="field inline">
              <span className="caption">Owner</span>
              <select value={t.owner} disabled={props.disabled}
                onChange={(e) => set(i, { owner: e.target.value as PlanTask["owner"] })}>
                <option value="employee">Employee</option>
                <option value="hr">HR</option>
              </select>
            </label>
            <label className="field inline">
              <span className="caption">Due</span>
              <input type="date" value={t.due ?? ""} disabled={props.disabled}
                onChange={(e) => set(i, { due: e.target.value || null })} />
            </label>
            <button className="remove" disabled={props.disabled} aria-label={`Remove task ${i + 1}: ${t.title}`}
              onClick={() => props.onChange(props.tasks.filter((_, j) => j !== i))}>
              Remove
            </button>
          </div>
        </li>
      ))}
      <li className="task-add">
        <button className="link" disabled={props.disabled}
          onClick={() => props.onChange([...props.tasks,
            { task_id: `hr-added-${Date.now().toString(36)}`, title: "", owner: "employee", due: null }])}>
          Add a task
        </button>
      </li>
    </ol>
  );
}

function TaskList({ tasks }: { tasks: PlanTask[] }) {
  return (
    <ol className="tasks read">
      {tasks.map((t) => (
        <li className="task-row" key={t.task_id}>
          <span className="task-title-read">{t.title}</span>
          <span className="task-meta-read">
            {t.owner === "hr" ? "HR" : "Employee"}{t.due ? `, due ${day(t.due)}` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Outcome({ t }: { t: Thread }) {
  if (t.status === "executed")
    return <p className="outcome done">Approved and filed as case {t.case_id} with {t.created_task_ids.length} tasks.</p>;
  if (t.status === "rejected")
    return <p className="outcome stop">Rejected{t.review?.note ? `: ${t.review.note}` : "."}</p>;
  if (t.status === "escalated")
    return (
      <p className="outcome stop">
        Sent to the HR queue without a plan. The classifier couldn't match this to a life event it handles,
        so a specialist should reply directly.
      </p>
    );
  return <p className="outcome">The agent is still working on this case.</p>;
}

function Detail(props: {
  t: Thread;
  busy: string | null;
  onReview: (t: Thread, action: "approve" | "edit" | "reject", note: string, plan?: Plan) => void;
}) {
  const { t } = props;
  const original = t.plan?.tasks ?? [];
  const [tasks, setTasks] = useState<PlanTask[]>(original);
  const [note, setNote] = useState("");
  // Reset only when the plan's content changes. Polling hands us a new object
  // every few seconds, and resetting on identity would wipe the reviewer's edits.
  const planKey = JSON.stringify(original);
  useEffect(() => {
    setTasks(JSON.parse(planKey));
  }, [planKey]);

  const waiting = isWaiting(t);
  const working = props.busy !== null;
  const edited = useMemo(() => JSON.stringify(tasks) !== JSON.stringify(original), [tasks, original]);
  const incomplete = tasks.some((k) => !k.title.trim());

  return (
    <article className="detail">
      <header className="request">
        <p className="from">
          From {t.employee_id}{t.today && <>, received {day(t.today)}</>}
        </p>
        <blockquote>{t.request}</blockquote>
      </header>

      <Facts t={t} />

      {t.findings.length > 0 && (
        <ul className="findings">
          {t.findings.map((f) => (
            <li key={f.source}>
              <span className="finding-source">{f.source === "benefits" ? "Benefits" : "Leave"}</span>
              {humanDates(f.summary)}
            </li>
          ))}
        </ul>
      )}

      {t.plan && (
        <>
          <h3>Checklist</h3>
          {waiting
            ? <TaskEditor tasks={tasks} onChange={setTasks} disabled={working} />
            : <TaskList tasks={t.plan.tasks} />}

          <h3>Message to the employee</h3>
          <div className="message-preview">
            <p>{t.plan.employee_message}</p>
            <p className="provenance">
              {t.plan.message_source === "llm"
                ? "Reworded by the model. Every date and number was checked against the plan."
                : t.plan.message_note
                  ? `Written from the template. The model's version was not used: ${t.plan.message_note.replace(/^rewrite (rejected|failed): /, "")}.`
                  : "Written from the template."}
            </p>
          </div>
        </>
      )}

      {waiting ? (
        <div className="decision">
          <label className="field">
            <span className="caption">Note for the case record</span>
            <textarea rows={2} value={note} disabled={working} onChange={(e) => setNote(e.target.value)}
              placeholder="Optional. Required context if you reject." />
          </label>
          <div className="actions">
            <button className="primary" disabled={working || incomplete}
              onClick={() => edited && t.plan
                ? props.onReview(t, "edit", note, { ...t.plan, tasks })
                : props.onReview(t, "approve", note)}>
              {edited ? "Approve with my edits" : "Approve plan"}
            </button>
            <button className="quiet stop" disabled={working} onClick={() => props.onReview(t, "reject", note)}>
              Reject
            </button>
            {edited && (
              <button className="link" disabled={working} onClick={() => setTasks(original)}>Undo my edits</button>
            )}
          </div>
          {incomplete && <p className="hint">Give every task a name, or remove it.</p>}
        </div>
      ) : (
        <Outcome t={t} />
      )}
    </article>
  );
}

export default function Review(props: {
  threads: Thread[];
  selected: Thread | null;
  onSelect: (id: string) => void;
  busy: string | null;
  onReview: (t: Thread, action: "approve" | "edit" | "reject", note: string, plan?: Plan) => void;
}) {
  return (
    <div className="hr-inner">
      <Queue threads={props.threads} selected={props.selected} onSelect={props.onSelect} />
      {props.selected ? (
        <Detail key={props.selected.thread_id} t={props.selected} busy={props.busy} onReview={props.onReview} />
      ) : (
        <p className="empty detail-empty">Pick a case to see the request, the agent's plan and the message the employee will get.</p>
      )}
    </div>
  );
}
