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
  if (!response.ok) throw new Error(data.detail ?? `Request failed: ${response.status}`);
  return data;
}

export const getConfig = () => request<Config>("/config");
export const getProducts = () => request<Product[]>("/products");
export const createOrder = (items: CartLine[], tampered: boolean) =>
  request<{ order_id: string }>("/orders", {
    items,
    demo_attack: tampered ? "tampered_total" : null,
  });
