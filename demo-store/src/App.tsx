import { PayPalButtons, PayPalScriptProvider } from "@paypal/react-paypal-js";
import { useEffect, useMemo, useState } from "react";
import {
  type CartLine,
  type Config,
  createOrder,
  getConfig,
  getProducts,
  guardCancel,
  guardCheck,
  guardConfirm,
  type OrderSummary,
  type Product,
} from "./api";

// Placeholder for phase 4, which replaces it with the accessible guard dialog.
function GuardPanel(props: {
  summary: OrderSummary;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { summary, onConfirm, onCancel } = props;
  return (
    <section aria-label="Review payment">
      <h2>Review payment: {summary.risk.level} risk</h2>
      {summary.risk.hits.length > 0 && (
        <ul aria-label="Why this was flagged">
          {summary.risk.hits.map((hit) => (
            <li key={hit.code}>{hit.reason}</li>
          ))}
        </ul>
      )}
      <p>Paying: {summary.payee}</p>
      <p>
        Total: {summary.total} {summary.currency}
      </p>
      <ul>
        {summary.items.map((item) => (
          <li key={item.name}>
            {item.quantity} × {item.name}, {item.unit_price} each
          </li>
        ))}
      </ul>
      <button onClick={onConfirm}>Confirm payment</button> <button onClick={onCancel}>Cancel</button>
    </section>
  );
}

export function App() {
  const [config, setConfig] = useState<Config>();
  const [products, setProducts] = useState<Product[]>([]);
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [summary, setSummary] = useState<OrderSummary>();
  const [status, setStatus] = useState("");
  const [tampered, setTampered] = useState(false);

  useEffect(() => {
    Promise.all([getConfig(), getProducts()])
      .then(([loadedConfig, loadedProducts]) => {
        setConfig(loadedConfig);
        setProducts(loadedProducts);
      })
      .catch((error: Error) => setStatus(error.message));
  }, []);

  const cart: CartLine[] = useMemo(
    () =>
      Object.entries(quantities)
        .filter(([, quantity]) => quantity > 0)
        .map(([product_id, quantity]) => ({ product_id, quantity })),
    [quantities],
  );

  const add = (id: string) =>
    setQuantities((current) => ({ ...current, [id]: (current[id] ?? 0) + 1 }));

  const confirm = async () => {
    if (!summary) return;
    try {
      const result = await guardConfirm(summary.order_id);
      setStatus(`Paid. Capture ${result.capture_id}.`);
      setSummary(undefined);
      setQuantities({});
    } catch (error) {
      setStatus((error as Error).message);
    }
  };

  const cancel = async () => {
    if (!summary) return;
    try {
      await guardCancel(summary.order_id);
      setStatus("Cancelled. Nothing was charged.");
      setSummary(undefined);
    } catch (error) {
      setStatus((error as Error).message);
    }
  };

  return (
    <main>
      <h1>Pay Guard demo store</h1>
      <p role="status">{status}</p>

      <ul>
        {products.map((product) => (
          <li key={product.id}>
            {product.name}, {product.price} USD{" "}
            <button onClick={() => add(product.id)} aria-label={`Add ${product.name} to cart`}>
              Add
            </button>{" "}
            In cart: {quantities[product.id] ?? 0}
          </li>
        ))}
      </ul>

      {config?.demo_attacks_enabled && (
        <label>
          <input
            type="checkbox"
            checked={tampered}
            onChange={(event) => setTampered(event.target.checked)}
          />{" "}
          Demo attack: tamper with the total
        </label>
      )}

      {summary && <GuardPanel summary={summary} onConfirm={confirm} onCancel={cancel} />}

      {config && cart.length > 0 && !summary && (
        <PayPalScriptProvider
          options={{ clientId: config.paypal_client_id, currency: config.currency }}
        >
          <PayPalButtons
            // Rebuild the buttons when the cart changes so createOrder sends the current cart.
            forceReRender={[cart, tampered]}
            createOrder={async () => (await createOrder(cart, tampered)).order_id}
            // No capture here: approval only opens the guard.
            onApprove={async ({ orderID }) => {
              setSummary(await guardCheck(orderID));
              setStatus("");
            }}
            onError={(error) => setStatus(`PayPal error: ${String(error)}`)}
          />
        </PayPalScriptProvider>
      )}
    </main>
  );
}
