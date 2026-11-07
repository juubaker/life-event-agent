// Types mirror server.py's thread_detail(); fetch + SSE helpers.

export type NodeName =
  | "classify" | "escalate" | "benefits" | "leave"
  | "plan" | "human_review" | "execute" | "confirm";
export type NodeState = "done" | "active" | "waiting" | "skipped" | "pending";
export type EventType =
  | "birth_adoption" | "marriage" | "divorce" | "relocation" | "medical_leave" | "unknown";

export interface PlanTask {
  task_id: string;
  title: string;
  owner: "employee" | "hr";
  due: string | null;
}

export interface Plan {
  event_type: EventType;
  tasks: PlanTask[];
  employee_message: string;
  message_source: "llm" | "template";
  message_note: string;
}

export interface Thread {
  thread_id: string;
  employee_id: string;
  request: string;
  today: string;
  received_at: string | null;
  status: "running" | "planned" | "approved" | "rejected" | "executed" | "escalated";
  next: NodeName[];
  nodes: Record<NodeName, NodeState>;
  classification: {
    event_type: EventType;
    event_date: string | null;
    date_text: string | null;
    confidence: number;
    rationale: string;
  } | null;
  findings: { source: string; summary: string; actions: string[]; deadline: string | null }[];
  plan: Plan | null;
  review: { action: "approve" | "edit" | "reject"; note: string } | null;
  case_id: string | null;
  created_task_ids: string[];
  audit: string[];
  updated_at: string | null;
}

export interface Config {
  provider: "rules" | "ollama" | "anthropic";
  model: string | null;
  durable: boolean;
  today: string;
}

export type StreamEvent =
  | { type: "start"; thread_id: string; next: NodeName[] }
  | { type: "step"; node: NodeName; thread: Thread }
  | { type: "done"; thread: Thread }
  | { type: "error"; message: string };

export const isWaiting = (t: Thread) => t.next.includes("human_review");

async function json<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const getConfig = () => json<Config>("/api/config");
export const getThreads = () => json<Thread[]>("/api/threads");

/** POST that answers with Server-Sent Events; calls onEvent for each one. */
async function stream(url: string, body: unknown, onEvent: (e: StreamEvent) => void) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok || !r.body) {
    let detail = `${r.status}`;
    try { detail = (await r.json()).detail ?? detail; } catch { /* not JSON */ }
    onEvent({ type: "error", message: String(detail) });
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      if (frame.startsWith("data: ")) onEvent(JSON.parse(frame.slice(6)));
    }
  }
}

export const submitRequest = (employee_id: string, request: string, on: (e: StreamEvent) => void) =>
  stream("/api/requests", { employee_id, request }, on);

export const submitReview = (
  threadId: string,
  body: { action: "approve" | "edit" | "reject"; note: string; plan?: Plan },
  on: (e: StreamEvent) => void,
) => stream(`/api/threads/${threadId}/review`, body, on);

// --- wording shared by the panes ----------------------------------------------

export const EVENT_LABEL: Record<EventType, string> = {
  birth_adoption: "Birth or adoption",
  marriage: "Marriage or partnership",
  divorce: "Divorce or separation",
  relocation: "Move",
  medical_leave: "Medical leave",
  unknown: "Not a life event",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-11-01" -> "Nov 1, 2026", read straight from the string (no time zone shifts). */
export function day(iso: string | null | undefined): string {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}

/** Swap ISO dates inside agent-written text for "Dec 5, 2026". */
export const humanDates = (s: string) => s.replace(/\b\d{4}-\d{2}-\d{2}\b/g, (m) => day(m));

export function statusLabel(t: Thread): { text: string; tone: "signal" | "done" | "stop" | "quiet" } {
  if (isWaiting(t)) return { text: "Waiting for review", tone: "signal" };
  switch (t.status) {
    case "executed": return { text: "Approved", tone: "done" };
    case "rejected": return { text: "Rejected", tone: "stop" };
    case "escalated": return { text: "Escalated", tone: "stop" };
    default: return { text: "In progress", tone: "quiet" };
  }
}
