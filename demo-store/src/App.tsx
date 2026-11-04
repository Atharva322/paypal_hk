import { PayPalButtons, PayPalScriptProvider } from "@paypal/react-paypal-js";
import { attach, type GuardOutcome } from "pay-guard-ui";
import { useEffect, useMemo, useRef, useState } from "react";
import { type CartLine, type Config, createOrder, getConfig, getProducts, type Product } from "./api";

export function App() {
  const [config, setConfig] = useState<Config>();
  const [products, setProducts] = useState<Product[]>([]);
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [status, setStatus] = useState("");
  const [tampered, setTampered] = useState(false);
  const statusRef = useRef<HTMLParagraphElement>(null);

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

  const onResult = (outcome: GuardOutcome) => {
    if (outcome.status === "captured") {
      setStatus(`Paid. Capture ${outcome.captureId}.`);
      setQuantities({});
    } else {
      setStatus("Cancelled. Nothing was charged.");
    }
  };

  const buttons = attach(
    { createOrder: async () => (await createOrder(cart, tampered)).order_id },
    {
      apiBase: "/api",
      onResult,
      onError: (error) => setStatus(error.message),
      returnFocus: () => statusRef.current,
    },
  );

  return (
    <main>
      <h1>Pay Guard demo store</h1>
      <p role="status" tabIndex={-1} ref={statusRef}>
        {status}
      </p>

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

      {config && cart.length > 0 && (
        <PayPalScriptProvider
          options={{ clientId: config.paypal_client_id, currency: config.currency }}
        >
          <PayPalButtons
            // Rebuild the buttons when the cart changes so createOrder sends the current cart.
            forceReRender={[cart, tampered]}
            {...buttons}
            onError={(error) => setStatus(`PayPal error: ${String(error)}`)}
          />
        </PayPalScriptProvider>
      )}
    </main>
  );
}
