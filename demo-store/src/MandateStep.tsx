import { type FormEvent, useEffect, useRef, useState } from "react";
import { confirmMandate, draftMandate, type Mandate, type MandateFields } from "./api";

const blankToNull = (value: string) => (value.trim() === "" ? null : value.trim());

/** Ask in plain words, hear it read back, correct it, then confirm. Nothing shops before that. */
export function MandateStep({ onConfirmed }: { onConfirmed: (mandate: Mandate) => void }) {
  const [request, setRequest] = useState("");
  const [draft, setDraft] = useState<Mandate>();
  const [fields, setFields] = useState<MandateFields>();
  const [confirmed, setConfirmed] = useState<Mandate>();
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const reviewHeading = useRef<HTMLHeadingElement>(null);
  const doneHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => reviewHeading.current?.focus(), [draft]);
  useEffect(() => doneHeading.current?.focus(), [confirmed]);

  const read = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setStatus("Reading your request…");
    try {
      const mandate = await draftMandate(request);
      setDraft(mandate);
      setFields(mandate.fields);
      setConfirmed(undefined);
      setStatus("");
    } catch (error) {
      setStatus((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft || !fields) return;
    setBusy(true);
    try {
      // The limit field is labelled in US dollars, so that is the currency confirmed.
      const mandate = await confirmMandate(draft.mandate_id, { ...fields, currency: "USD" });
      setConfirmed(mandate);
      setStatus("");
      onConfirmed(mandate);
    } catch (error) {
      setStatus((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const set = (name: keyof MandateFields, value: MandateFields[keyof MandateFields]) =>
    setFields((current) => current && { ...current, [name]: value });

  if (confirmed) {
    return (
      <section aria-labelledby="mandate-done">
        <h2 id="mandate-done" tabIndex={-1} ref={doneHeading}>
          Confirmed. The agent may now shop for:
        </h2>
        <ul>
          {confirmed.spoken.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <p>Every order it makes is checked against this before you pay.</p>
      </section>
    );
  }

  return (
    <section aria-labelledby="mandate-ask">
      <h2 id="mandate-ask">What should the agent buy?</h2>
      <form onSubmit={read}>
        <label htmlFor="mandate-request">
          Say what you want, with a spending limit. For example: running shoes from Trailhead,
          size 10, under $80.
        </label>
        <textarea
          id="mandate-request"
          rows={3}
          maxLength={500}
          required
          value={request}
          onChange={(event) => setRequest(event.target.value)}
        />
        <button type="submit" disabled={busy}>
          Read it back to me
        </button>
      </form>

      {draft && fields && (
        <>
          <h2 id="mandate-review" tabIndex={-1} ref={reviewHeading}>
            Check what the agent will do
          </h2>
          <ul aria-label="What the agent understood">
            {draft.spoken.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>

          <form onSubmit={confirm} aria-labelledby="mandate-review">
            <p>Correct anything that is wrong, then confirm.</p>
            <label>
              Item
              <input value={fields.item} onChange={(e) => set("item", e.target.value)} required />
            </label>
            <label>
              Brand (optional)
              <input value={fields.brand ?? ""} onChange={(e) => set("brand", blankToNull(e.target.value))} />
            </label>
            <label>
              Size (optional)
              <input value={fields.size ?? ""} onChange={(e) => set("size", blankToNull(e.target.value))} />
            </label>
            <label>
              Must be, separated by commas (optional)
              <input
                value={fields.details.join(", ")}
                onChange={(e) =>
                  set("details", e.target.value.split(",").map((part) => part.trim()).filter(Boolean))
                }
              />
            </label>
            <label>
              Quantity
              <input
                type="number"
                min={1}
                max={10}
                value={fields.quantity}
                onChange={(e) => set("quantity", Number(e.target.value))}
                required
              />
            </label>
            <label>
              Spending limit in US dollars, for the whole order
              <input
                inputMode="decimal"
                value={fields.max_total ?? ""}
                onChange={(e) => set("max_total", blankToNull(e.target.value))}
                required
              />
            </label>
            <label>
              Store (leave blank to allow any store you've paid before)
              <input value={fields.store ?? ""} onChange={(e) => set("store", blankToNull(e.target.value))} />
            </label>
            <button type="submit" disabled={busy}>
              Confirm and let the agent shop
            </button>
          </form>
        </>
      )}

      <p role="status">{status}</p>
    </section>
  );
}
