import { MandateStep } from "./MandateStep";

// The agent and its checkout plug in after the mandate; until then the page stops here,
// so nothing on it suggests a payment is checked when it is not.
export function App() {
  return (
    <main>
      <h1>Pay Guard</h1>
      <MandateStep onConfirmed={() => undefined} />
    </main>
  );
}
