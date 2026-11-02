export interface Product {
  id: string;
  name: string;
  price: string;
}

export interface Config {
  paypal_client_id: string;
  currency: string;
}

export interface CartLine {
  product_id: string;
  quantity: number;
}

export interface OrderSummary {
  order_id: string;
  payee: string;
  total: string;
  currency: string;
  items: { name: string; quantity: number; unit_price: string }[];
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail ?? `Request failed: ${response.status}`);
  return data;
}

export const getConfig = () => request<Config>("/config");
export const getProducts = () => request<Product[]>("/products");
export const createOrder = (items: CartLine[]) =>
  request<{ order_id: string }>("/orders", { items });
export const guardCheck = (order_id: string) =>
  request<OrderSummary>("/guard/check", { order_id });
export const guardConfirm = (order_id: string) =>
  request<{ status: string; capture_id: string }>("/guard/confirm", { order_id });
export const guardCancel = (order_id: string) =>
  request<{ status: string }>("/guard/cancel", { order_id });
