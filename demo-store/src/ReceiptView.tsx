import { useEffect, useRef, useState } from "react";
import { getReceipt, type Receipt } from "./api";

const DECISIONS: Record<Receipt["decision"], string> = {
  captured: "You paid.",
  cancelled: "You cancelled. Nothing was charged.",
  created: "Not decided yet.",
  checked: "Not decided yet.",
  capturing: "Payment in progress.",
};

/** What was asked, what PayPal's order said, what you were told, and what you decided. */
export function ReceiptView({ orderId, onRestart }: { orderId: string; onRestart: () => void }) {
  const [receipt, setReceipt] = useState<Receipt>();
  const [error, setError] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    getReceipt(orderId)
      .then(setReceipt)
      .catch((reason: Error) => setError(reason.message));
  }, [orderId]);
  useEffect(() => heading.current?.focus(), [receipt]);

  if (error) return <p role="alert">{error}</p>;
  if (!receipt) return <p role="status">Loading the receipt…</p>;

  const shown = receipt.shown;
  return (
    <section className="card receipt" aria-labelledby="receipt-heading">
      <h2 id="receipt-heading" tabIndex={-1} ref={heading}>
        Consent receipt
      </h2>
      <p className={`decision decision-${receipt.decision}`}>
        <strong>{DECISIONS[receipt.decision]}</strong>
        {receipt.capture_id && ` PayPal capture ${receipt.capture_id}.`} Order {receipt.order_id},
        recorded {receipt.decided_at} UTC.
      </p>

      {receipt.asked && (
        <>
          <h3>What you asked for</h3>
          <blockquote>{receipt.asked.request}</blockquote>
          <ul aria-label="The mandate you confirmed">
            {receipt.asked.read_back.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </>
      )}

      {shown ? (
        <>
          <h3>The order as PayPal recorded it</h3>
          <dl className="receipt-details">
            <dt>Paying</dt>
            <dd>{shown.payee_name ?? shown.payee}</dd>
            <dt>Total</dt>
            <dd>
              {shown.total} {shown.currency}
            </dd>
            <dt>Items</dt>
            <dd>
              <ul>
                {shown.items.map((item) => (
                  <li key={item.name}>
                    {item.quantity} × {item.name}
                  </li>
                ))}
              </ul>
            </dd>
          </dl>
          <h3>What you were told before deciding ({shown.risk.level} risk)</h3>
          <ul className="check-lines verdict">
            {shown.spoken.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </>
      ) : (
        <p>You discarded the order before approving it, so it was never checked in PayPal.</p>
      )}

      <button type="button" className="secondary" onClick={onRestart}>
        Start a new request
      </button>
    </section>
  );
}
