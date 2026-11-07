import { type FormEvent, useEffect, useRef, useState } from "react";
import { type EventType, type Thread, day, isWaiting } from "./api";

const SAMPLES = [
  "We just had a baby girl yesterday! What do I need to do?",
  "My partner and I tied the knot last Saturday. Can I add them to dental?",
  "How do I reset my VPN password?",
];

const EVENT_WORDS: Record<EventType, string> = {
  birth_adoption: "new child",
  marriage: "marriage",
  divorce: "divorce or separation",
  relocation: "move",
  medical_leave: "medical leave",
  unknown: "request",
};

function Reply({ t }: { t: Thread }) {
  if (t.status === "running" && !isWaiting(t)) {
    return <p className="thinking">Reading your request</p>;
  }
  if (isWaiting(t)) {
    const what = EVENT_WORDS[t.classification?.event_type ?? "unknown"];
    return (
      <p>
        Thanks. I've drafted a plan for your {what}. Someone on the HR team reviews it before
        anything changes, and your checklist will show up here once it's approved.
      </p>
    );
  }
  if (t.status === "escalated") {
    return <p>Thanks for reaching out. This one needs a person, so I've sent it to an HR specialist who will follow up with you.</p>;
  }
  if (t.status === "rejected") {
    return <p>An HR specialist reviewed your request and will contact you directly about next steps.</p>;
  }
  if (t.status === "executed" && t.plan) {
    const mine = t.plan.tasks.filter((k) => k.owner === "employee");
    return (
      <>
        <p>{t.plan.employee_message}</p>
        {mine.length > 0 && (
          <ul className="checklist">
            {mine.map((k) => (
              <li key={k.task_id}>
                <span>{k.title}</span>
                {k.due && <span className="due">by {day(k.due)}</span>}
              </li>
            ))}
          </ul>
        )}
        <p className="fine">Case {t.case_id}</p>
      </>
    );
  }
  return <p className="thinking">Working on it</p>;
}

export default function Employee(props: {
  employeeId: string;
  onEmployeeId: (id: string) => void;
  threads: Thread[];
  busy: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  // Oldest request first, like a chat. Sort by arrival, not last update, so a
  // case approved later doesn't jump below newer messages.
  const convo = props.threads.slice().sort((a, b) =>
    (a.received_at ?? "9").localeCompare(b.received_at ?? "9"));
  const last = convo.at(-1);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [convo.length, last?.status, last?.next.join()]);

  const send = (e?: FormEvent) => {
    e?.preventDefault();
    const v = text.trim();
    if (!v || props.busy) return;
    props.onSubmit(v);
    setText("");
  };

  return (
    <div className="employee-inner">
      <div className="pane-head">
        <h2>Employee</h2>
        <label className="field inline">
          <span className="caption">Employee ID</span>
          <input
            value={props.employeeId}
            onChange={(e) => props.onEmployeeId(e.target.value.trim() || "E1024")}
            size={8}
          />
        </label>
      </div>

      <div className="convo" aria-live="polite">
        {convo.length === 0 && (
          <p className="empty">Tell HR what changed, like a new baby, a marriage or a move. The agent drafts your checklist and a person approves it.</p>
        )}
        {convo.map((t) => (
          <div key={t.thread_id} className="exchange" data-selected={t.thread_id === props.selected || undefined}>
            <button className="bubble mine" onClick={() => props.onSelect(t.thread_id)}>
              {t.request}
            </button>
            <div className="bubble agent"><Reply t={t} /></div>
          </div>
        ))}
        <div ref={end} />
      </div>

      <form className="composer" onSubmit={send}>
        <label className="field">
          <span className="caption">Your message to HR</span>
          <textarea
            rows={3}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) send();
            }}
            placeholder="We had a baby on Saturday. What do I need to do?"
          />
        </label>
        <div className="composer-row">
          <div className="samples" aria-label="Examples">
            {SAMPLES.map((s) => (
              <button type="button" key={s} className="sample" onClick={() => setText(s)}>{s}</button>
            ))}
          </div>
          <button className="primary" disabled={props.busy || !text.trim()}>Send</button>
        </div>
      </form>
    </div>
  );
}
