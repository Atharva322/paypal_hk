import type { GuardSummary } from "pay-guard-ui";

export interface Config {
  paypal_client_id: string;
  currency: string;
  demo_attacks_enabled: boolean;
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(errorText(data.detail) || `Request failed: ${response.status}`);
  return data;
}

// FastAPI sends a string, a list of our own messages, or a list of validation errors.
function errorText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (typeof item === "string" ? item : `${item.loc?.at(-1)}: ${item.msg}`))
      .join(" ");
  }
  return "";
}

export interface MandateFields {
  item: string;
  brand: string | null;
  size: string | null;
  details: string[];
  quantity: number;
  max_total: string | null;
  currency: string | null;
  store: string | null;
}

export interface Mandate {
  mandate_id: string;
  status: "draft" | "confirmed";
  fields: MandateFields;
  max_total_cents: number | null;
  store: string | null;
  problems: string[];
  questions?: string[];
  spoken: string[];
}

export type Scenario = "clean" | "gift_card" | "lookalike" | "compromised";

export interface AgentOrder {
  order_id: string;
  store: string;
  total: string;
  items: string[];
  simulated_hijack: boolean;
}

export interface AgentStepRecord {
  tool: string;
  arguments: Record<string, unknown>;
  result: string;
}

export interface AgentRun {
  scenario: Scenario;
  order: AgentOrder | null;
  steps: AgentStepRecord[];
  final_text: string;
}

type RunEvent =
  | { type: "step"; step: AgentStepRecord }
  | { type: "done"; run: AgentRun }
  | { type: "error"; detail: string };

export interface Receipt {
  order_id: string;
  decision: "created" | "checked" | "capturing" | "captured" | "cancelled";
  decided_at: string;
  capture_id: string | null;
  asked: { request: string; read_back: string[] } | null;
  shown: GuardSummary | null;
}

/** Runs the agent, calling onStep for each step as it happens. Resolves with the whole run. */
export async function runAgent(
  mandateId: string,
  scenario: Scenario,
  onStep: (step: AgentStepRecord) => void,
): Promise<AgentRun> {
  const response = await fetch("/api/agent/runs/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mandate_id: mandateId, scenario }),
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(errorText(data.detail) || `Request failed: ${response.status}`);
  }
  // One JSON event per line; a chunk can end mid-line.
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffered = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffered += value;
    const lines = buffered.split("\n");
    buffered = lines.pop() ?? "";
    for (const line of lines.filter(Boolean)) {
      const event = JSON.parse(line) as RunEvent;
      if (event.type === "step") onStep(event.step);
      else if (event.type === "done") return event.run;
      else throw new Error(event.detail);
    }
  }
  throw new Error("The agent stopped without finishing. Try again.");
}
export const previewOrder = (orderId: string) =>
  request<GuardSummary>("/guard/preview", { order_id: orderId });
export const discardOrder = (orderId: string) =>
  request<{ status: string }>("/guard/cancel", { order_id: orderId });
export const getReceipt = (orderId: string) => request<Receipt>(`/receipts/${orderId}`);

export const draftMandate = (text: string) => request<Mandate>("/mandates", { request: text });
export const confirmMandate = (id: string, fields: MandateFields) =>
  request<Mandate>(`/mandates/${id}/confirm`, fields);

export const getConfig = () => request<Config>("/config");
