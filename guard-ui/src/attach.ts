import { openGuardDialog } from "./dialog";
import type { ConfirmBody, GuardApi, GuardOutcome, GuardSummary } from "./types";

export interface AttachOptions {
  /** Where the store proxies the guard backend, e.g. "/api". */
  apiBase?: string;
  onResult?: (outcome: GuardOutcome) => void;
  onError?: (error: Error) => void;
  /** Receives focus when the dialog closes. */
  returnFocus?: () => HTMLElement | null;
}

function createApi(apiBase: string): GuardApi {
  const post = async <T>(path: string, body: unknown): Promise<T> => {
    const response = await fetch(`${apiBase}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail ?? `Request failed: ${response.status}`);
    return data;
  };
  return {
    check: (orderId) => post<GuardSummary>("/guard/check", { order_id: orderId }),
    confirm: (body: ConfirmBody) => post("/guard/confirm", body),
    cancel: (orderId) => post("/guard/cancel", { order_id: orderId }),
  };
}

/**
 * Wraps a PayPal buttons config. Approval opens the guard instead of capturing, and any
 * onApprove the store passed is dropped so it cannot capture around the guard.
 */
export function attach<T extends object>(
  config: T,
  options: AttachOptions = {},
): Omit<T, "onApprove"> & { onApprove: (data: { orderID: string }) => Promise<void> } {
  const api = createApi(options.apiBase ?? "");
  return {
    ...config,
    onApprove: async ({ orderID }) => {
      try {
        const summary = await api.check(orderID);
        // Not awaited: PayPal keeps its own overlay up until onApprove settles.
        void openGuardDialog(summary, api).then((outcome) => {
          options.returnFocus?.()?.focus();
          options.onResult?.(outcome);
        });
      } catch (error) {
        options.onError?.(error as Error);
      }
    },
  };
}
