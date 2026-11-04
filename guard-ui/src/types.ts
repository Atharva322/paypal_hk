export type RiskLevel = "Low" | "Medium" | "High";

export interface GuardSummary {
  order_id: string;
  payee: string;
  total: string;
  currency: string;
  items: { name: string; quantity: number; unit_price: string }[];
  risk: {
    level: RiskLevel;
    score: number;
    hits: { code: string; weight: number; reason: string }[];
  };
  confirmation: {
    method: "press" | "press_twice" | "type_total";
    pause_seconds: number;
  };
  spoken: string[];
}

export type GuardOutcome =
  | { status: "captured"; captureId: string }
  | { status: "cancelled" };

export interface ConfirmBody {
  order_id: string;
  acknowledged_risk: boolean;
  typed_total?: string;
}

export interface GuardApi {
  check(orderId: string): Promise<GuardSummary>;
  confirm(body: ConfirmBody): Promise<{ status: string; capture_id: string }>;
  cancel(orderId: string): Promise<{ status: string }>;
}
