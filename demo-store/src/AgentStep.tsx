import { PayPalButtons } from "@paypal/react-paypal-js";
import { attach, type GuardOutcome, type GuardSummary } from "pay-guard-ui";
import { useEffect, useRef, useState } from "react";
import {
  type AgentRun,
  type AgentStepRecord,
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

function describe(step: AgentStepRecord): string {
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
  // Steps as they arrive, before the run is done.
  const [liveSteps, setLiveSteps] = useState<AgentStepRecord[]>([]);
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
    setLiveSteps([]);
    setStatus("The agent is shopping. This takes up to a minute.");
    try {
      const result = await runAgent(mandate.mandate_id, scenario, (step) => {
        setLiveSteps((steps) => [...steps, step]);
        setStatus(describe(step));
      });
      if (result.order) {
        setStatus("Checking the order PayPal holds against your request…");
        setPreview(await previewOrder(result.order.order_id));
      }
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

  const steps = agentRun?.steps ?? liveSteps;

  return (
    <>
      <section className="card" aria-labelledby="agent-heading">
        <p className="step-label">Step 2 of 3</p>
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
        <p role="status" tabIndex={-1} ref={statusRef} className={busy ? "working" : undefined}>
          {status}
        </p>

        {steps.length > 0 && (
          <>
            {agentRun ? (
              <h3 id="agent-result" tabIndex={-1} ref={resultHeading}>
                What the agent did
              </h3>
            ) : (
              <h3>What the agent is doing</h3>
            )}
            <ol className="timeline">
              {steps.map((step, index) => (
                <li key={index} data-tool={step.tool}>
                  {describe(step)}
                </li>
              ))}
            </ol>
          </>
        )}
        {agentRun && steps.length === 0 && (
          <h3 id="agent-result" tabIndex={-1} ref={resultHeading}>
            What the agent did
          </h3>
        )}
        {agentRun && !order && (
          <p>The agent did not order anything. It said: {agentRun.final_text}</p>
        )}
        {order?.simulated_hijack && (
          <p className="callout simulation">
            Simulation: a hijacked agent added a $25 gift card to this order.
          </p>
        )}
      </section>

      {order && preview && (
        <section className="card" aria-labelledby="check-heading">
          <p className="step-label">Step 3 of 3</p>
          <h2 id="check-heading">Before you approve: the guard's check of PayPal's order</h2>
          <p className={`badge badge-${preview.risk.level.toLowerCase()}`}>
            {preview.risk.level} risk
          </p>
          <ul aria-label="The guard's check" className="check-lines verdict">
            {preview.spoken.slice(0, -1).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {preview.risk.level !== "Low" && (
            <p className="callout warning">
              This order is not what you asked for. You can discard it now, or approve it in
              PayPal and decide in the review that follows.
            </p>
          )}
          <div className="pay-actions">
            <PayPalButtons
              // A new order needs new buttons, or PayPal reopens the old one.
              forceReRender={[order.order_id]}
              {...buttons}
              onError={(error) => setStatus(`PayPal error: ${String(error)}`)}
            />
            <button type="button" className="secondary" onClick={discard}>
              Discard this order
            </button>
          </div>
        </section>
      )}
    </>
  );
}
