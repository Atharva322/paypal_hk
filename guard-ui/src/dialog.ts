import { injectStyles } from "./styles";
import type { GuardApi, GuardOutcome, GuardSummary } from "./types";

// A second press sooner than this is a bounced key, not a decision.
const MIN_SECOND_PRESS_MS = 400;

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
): HTMLElementTagNameMap[K] {
  return Object.assign(document.createElement(tag), props);
}

const seconds = (count: number) => (count === 1 ? "second" : "seconds");

function detailsList(summary: GuardSummary): HTMLDListElement {
  const list = element("dl", { className: "pg-details" });
  const add = (term: string, content: string | HTMLElement) => {
    const value = element("dd");
    value.append(content);
    list.append(element("dt", { textContent: term }), value);
  };
  add("Paying", summary.payee);
  add("Total", `${summary.total} ${summary.currency}`);
  const items = element("ul");
  for (const item of summary.items) {
    items.append(element("li", { textContent: `${item.quantity} × ${item.name}` }));
  }
  add("Items", items);
  return list;
}

/** Resolves once the buyer has paid or cancelled. Every failure stays inside the dialog. */
export function openGuardDialog(summary: GuardSummary, api: GuardApi): Promise<GuardOutcome> {
  injectStyles();
  const { method, pause_seconds: pauseSeconds } = summary.confirmation;

  return new Promise((resolve) => {
    // showModal gives the focus trap, Escape and an inert page behind for free.
    const dialog = element("dialog", { className: "pg-dialog" });
    dialog.setAttribute("aria-labelledby", "pg-title");
    dialog.setAttribute("aria-describedby", "pg-description");

    const title = element("h2", {
      id: "pg-title",
      tabIndex: -1,
      textContent: `Review payment: ${summary.risk.level} risk`,
    });
    // The backend's spoken summary, read with the title as the dialog opens. Hidden because
    // it repeats the visible details, in listening order and with a suspect payee spelled.
    const description = element("p", {
      id: "pg-description",
      className: "pg-visually-hidden",
      textContent: summary.spoken.join(" "),
    });
    // Only for what changes after opening: a second press, the pause ending, an error.
    const live = element("div", { className: "pg-visually-hidden" });
    live.setAttribute("aria-live", "assertive");
    live.setAttribute("aria-atomic", "true");
    live.dataset.testid = "live";

    const message = element("p", { className: "pg-message" });
    const actions = element("div", { className: "pg-actions" });
    const confirmButton = element("button", {
      type: "button",
      className: "pg-confirm",
      textContent: "Confirm payment",
    });
    const cancelButton = element("button", { type: "button", textContent: "Cancel" });
    actions.append(confirmButton, cancelButton);

    dialog.append(title, description, live);
    if (summary.risk.hits.length > 0) {
      const reasons = element("ul", { className: "pg-reasons" });
      reasons.setAttribute("aria-label", "Why this was flagged");
      for (const hit of summary.risk.hits) {
        reasons.append(element("li", { textContent: hit.reason }));
      }
      dialog.append(reasons);
    }
    dialog.append(detailsList(summary));

    let totalInput: HTMLInputElement | undefined;
    let pauseLabel: HTMLParagraphElement | undefined;
    if (method === "type_total") {
      pauseLabel = element("p", { className: "pg-pause" });
      const label = element("label", {
        className: "pg-field",
        textContent: "Type the total to pay anyway",
      });
      totalInput = element("input", { type: "text", inputMode: "decimal", autocomplete: "off" });
      label.append(totalInput);
      dialog.append(pauseLabel, label);
    }
    dialog.append(message, actions);

    let settled = false;
    let busy = false;
    let armedAt = 0;
    let secondsLeft = method === "type_total" ? pauseSeconds : 0;
    let pauseTimer: number | undefined;

    const announce = (text: string) => {
      live.textContent = text;
    };
    const say = (text: string) => {
      message.textContent = text;
      announce(text);
    };
    const finish = (outcome: GuardOutcome) => {
      settled = true;
      window.clearInterval(pauseTimer);
      dialog.close();
      dialog.remove();
      resolve(outcome);
    };

    const pay = async () => {
      if (settled || busy) return;
      busy = true;
      try {
        const result = await api.confirm({
          order_id: summary.order_id,
          acknowledged_risk: method !== "press",
          typed_total: totalInput?.value,
        });
        finish({ status: "captured", captureId: result.capture_id });
      } catch (error) {
        say((error as Error).message);
      } finally {
        busy = false;
      }
    };

    const cancel = async () => {
      if (settled || busy) return;
      busy = true;
      try {
        await api.cancel(summary.order_id);
        finish({ status: "cancelled" });
      } catch (error) {
        say((error as Error).message);
      } finally {
        busy = false;
      }
    };

    const pressConfirm = () => {
      if (method === "type_total") {
        if (secondsLeft > 0) say(`Wait ${secondsLeft} more ${seconds(secondsLeft)}.`);
        else if (!totalInput?.value.trim()) say("Type the total first.");
        else void pay();
      } else if (method === "press_twice" && armedAt === 0) {
        armedAt = performance.now();
        confirmButton.textContent = "Press again to confirm";
        say("Press Confirm payment again to pay.");
      } else if (method === "press_twice") {
        if (performance.now() - armedAt >= MIN_SECOND_PRESS_MS) void pay();
      } else {
        void pay();
      }
    };

    confirmButton.addEventListener("click", pressConfirm);
    cancelButton.addEventListener("click", () => void cancel());

    dialog.addEventListener("keydown", (event) => {
      // A held Enter repeats clicks on the focused button, which could make the second press.
      if (event.repeat && event.key === "Enter") {
        event.preventDefault();
        return;
      }
      // A held key repeats and a shortcut like Ctrl+C is not a choice to pay.
      if (event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.target === totalInput) {
        if (event.key === "Enter") pressConfirm();
        return;
      }
      const key = event.key.toLowerCase();
      if (key === "x") {
        event.preventDefault();
        void cancel();
      } else if (key === "c" && method !== "type_total") {
        event.preventDefault();
        pressConfirm();
      }
    });
    dialog.addEventListener("cancel", (event) => {
      // Escape must go through the backend so the decision is logged.
      event.preventDefault();
      void cancel();
    });

    if (method === "type_total") {
      const showPause = () => {
        pauseLabel!.textContent =
          secondsLeft > 0
            ? `You can pay in ${secondsLeft} ${seconds(secondsLeft)}.`
            : "The pause is over.";
        confirmButton.setAttribute("aria-disabled", String(secondsLeft > 0));
      };
      showPause();
      pauseTimer = window.setInterval(() => {
        secondsLeft -= 1;
        showPause();
        if (secondsLeft <= 0) {
          window.clearInterval(pauseTimer);
          announce("The pause is over. You can type the total to pay.");
        }
      }, 1000);
    }

    document.body.append(dialog);
    dialog.showModal();
    // Cancel is the easy path on High risk: Enter or Space there backs out.
    (method === "type_total" ? cancelButton : title).focus();
  });
}
