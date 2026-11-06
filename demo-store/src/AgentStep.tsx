import { PayPalButtons } from "@paypal/react-paypal-js";
import { attach, type GuardOutcome, type GuardSummary } from "pay-guard-ui";
import { useEffect, useRef, useState } from "react";
import {
  type AgentRun,
  type Config,
  discardOrder,
  type Mandate,
  previewOrder,
  runAgent,
  type Scenario,
} from "./api";

const SCENARIOS: { value: Scenario; label: string }[] = [
  { value: "clean", label: "Honest store" },
  { value: "gift_card", label: "Poisoned product page: a review tells agents to add a gift card" },
  { value: "lookalike", label: "Lookalike seller: the page sends agents to another store" },
  {
    value: "compromised",
    label: "Simulated hijacked agent: adds a $25 gift card to whatever it buys",
  },
];

function describe(step: AgentRun["steps"][number]): string {
  const args = step.arguments;
  if (step.tool === "search_products") return `Searched for "${args.query}".`;
  if (step.tool === "view_product") return `Read the page of product ${args.product_id}.`;
  if (step.result.startsWith("Error")) return `Tried to order, and the store said: ${step.result}`;
  return "Created a PayPal order.";
}

export interface Outcome {
  orderId: string;
  status: GuardOutcome["status"];
}

/** The agent shops; the guard's verdict is shown before PayPal opens and again after. */
export function AgentStep({
  mandate,
  config,
  onOutcome,
}: {
  mandate: Mandate;
  config: Config;
  onOutcome: (outcome: Outcome) => void;
}) {
  const [scenario, setScenario] = useState<Scenario>("clean");
  const [agentRun, setAgentRun] = useState<AgentRun>();
  const [preview, setPreview] = useState<GuardSummary>();
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const resultHeading = useRef<HTMLHeadingElement>(null);
  const statusRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => resultHeading.current?.focus(), [agentRun]);

  const shop = async () => {
    setBusy(true);
    setAgentRun(undefined);
    setPreview(undefined);
    setStatus("The agent is shopping. This takes up to a minute.");
    try {
      const result = await runAgent(mandate.mandate_id, scenario);
      if (result.order) setPreview(await previewOrder(result.order.order_id));
      setAgentRun(result);
      setStatus("");
    } catch (error) {
      setStatus((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const order = agentRun?.order;

  const discard = async () => {
    if (!order) return;
    try {
      await discardOrder(order.order_id);
      onOutcome({ orderId: order.order_id, status: "cancelled" });
    } catch (error) {
      setStatus((error as Error).message);
    }
  };

  const buttons =
    order &&
    attach(
      { createOrder: async () => order.order_id },
      {
        apiBase: "/api",
        onResult: (outcome) => onOutcome({ orderId: order.order_id, status: outcome.status }),
        onError: (error) => setStatus(error.message),
        returnFocus: () => statusRef.current,
      },
    );

  return (
    <section aria-labelledby="agent-heading">
      <h2 id="agent-heading">Let the agent shop</h2>
      {config.demo_attacks_enabled && (
        <fieldset>
          <legend>Demo scenario</legend>
          {SCENARIOS.map((option) => (
            <label key={option.value} className="choice">
              <input
                type="radio"
                name="scenario"
                value={option.value}
                checked={scenario === option.value}
                onChange={() => setScenario(option.value)}
              />{" "}
              {option.label}
            </label>
          ))}
        </fieldset>
      )}
      <button type="button" onClick={shop} disabled={busy}>
        Let the agent shop
      </button>
      <p role="status" tabIndex={-1} ref={statusRef}>
        {status}
      </p>

      {agentRun && (
        <>
          <h3 id="agent-result" tabIndex={-1} ref={resultHeading}>
            What the agent did
          </h3>
          <ol>
            {agentRun.steps.map((step, index) => (
              <li key={index}>{describe(step)}</li>
            ))}
          </ol>
          {!order && <p>The agent did not order anything. It said: {agentRun.final_text}</p>}
          {order?.simulated_hijack && (
            <p className="simulation">
              Simulation: a hijacked agent added a $25 gift card to this order.
            </p>
          )}
        </>
      )}

      {order && preview && (
        <>
          <h3>Before you approve: the guard's check of PayPal's order</h3>
          <ul aria-label="The guard's check">
            {preview.spoken.slice(0, -1).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {preview.risk.level !== "Low" && (
            <p className="warning">
              This order is not what you asked for. You can discard it now, or approve it in
              PayPal and decide in the review that follows.
            </p>
          )}
          <button type="button" onClick={discard}>
            Discard this order
          </button>
          <PayPalButtons
            // A new order needs new buttons, or PayPal reopens the old one.
            forceReRender={[order.order_id]}
            {...buttons}
            onError={(error) => setStatus(`PayPal error: ${String(error)}`)}
          />
        </>
      )}
    </section>
  );
}
