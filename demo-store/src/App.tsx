import { PayPalScriptProvider } from "@paypal/react-paypal-js";
import { useEffect, useState } from "react";
import { AgentStep, type Outcome } from "./AgentStep";
import { type Config, getConfig, type Mandate } from "./api";
import { MandateStep } from "./MandateStep";
import { ReceiptView } from "./ReceiptView";

// Ask, confirm, let the agent shop, decide, keep the receipt. Each step waits on the last.
export function App() {
  const [config, setConfig] = useState<Config>();
  const [error, setError] = useState("");
  const [mandate, setMandate] = useState<Mandate>();
  const [outcome, setOutcome] = useState<Outcome>();
  // Remounts the steps for a fresh request.
  const [round, setRound] = useState(0);

  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const restart = () => {
    setMandate(undefined);
    setOutcome(undefined);
    setRound((current) => current + 1);
  };

  return (
    <main>
      <header className="masthead">
        <h1>Pay Guard</h1>
        <p>
          An AI agent shops for you. Before any money moves, the guard holds PayPal's record of
          the order against what you asked for, and tells you what differs.
        </p>
      </header>
      {error && <p role="alert" className="callout warning">{error}</p>}
      {outcome ? (
        <ReceiptView orderId={outcome.orderId} onRestart={restart} />
      ) : (
        <>
          <MandateStep key={round} onConfirmed={setMandate} />
          {mandate && config && (
            <PayPalScriptProvider
              options={{ clientId: config.paypal_client_id, currency: config.currency }}
            >
              <AgentStep mandate={mandate} config={config} onOutcome={setOutcome} />
            </PayPalScriptProvider>
          )}
        </>
      )}
    </main>
  );
}
