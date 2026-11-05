export interface Product {
  id: string;
  name: string;
  price: string;
}

export interface Config {
  paypal_client_id: string;
  currency: string;
  demo_attacks_enabled: boolean;
}

export interface CartLine {
  product_id: string;
  quantity: number;
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

export const draftMandate = (text: string) => request<Mandate>("/mandates", { request: text });
export const confirmMandate = (id: string, fields: MandateFields) =>
  request<Mandate>(`/mandates/${id}/confirm`, fields);

export const getConfig = () => request<Config>("/config");
export const getProducts = () => request<Product[]>("/products");
export const createOrder = (items: CartLine[], tampered: boolean) =>
  request<{ order_id: string }>("/orders", {
    items,
    demo_attack: tampered ? "tampered_total" : null,
  });
