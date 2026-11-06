// The whole page flow, unattended: ask, confirm, let the agent shop, check, decide, receipt.
// The backend is mocked here (its own flow is tested in backend/tests/test_flow.py), and
// PayPal's script is replaced by a stub button that approves at once.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

const MANDATE_FIELDS = {
  item: "running shoes",
  brand: null,
  size: "10",
  details: [],
  quantity: 1,
  max_total: "80.00",
  currency: "USD",
  store: "Trailhead",
};
const READ_BACK = [
  "Buy 1 running shoes, size 10.",
  "Spend at most 80 dollars, US dollars, in total.",
  "Only from Trailhead Running Co., a store you've paid before.",
];

const SHOES = { name: "Trailhead Road running shoes, size 10", quantity: 1, unit_price: "54.00" };
const GIFT = { name: "Trailhead gift card", quantity: 1, unit_price: "25.00" };

function verdict(level: "Low" | "High", reasons: string[], total: string, items: object[]) {
  const method = level === "Low" ? "press" : "type_total";
  return {
    order_id: "ORDER1",
    payee: "merchant@example.com",
    payee_name: "Trailhead Running Co.",
    total,
    currency: "USD",
    items,
    risk: {
      level,
      score: reasons.length * 60,
      hits: reasons.map((reason) => ({ code: "rule", weight: 60, reason })),
    },
    confirmation: { method, pause_seconds: level === "High" ? 1 : 0 },
    spoken: [
      level === "Low"
        ? "This order matches what you asked for."
        : "Warning: high risk. This order is not what you asked for.",
      ...reasons,
      "Paying Trailhead Running Co.",
      level === "Low" ? "To pay, Tab to Confirm payment and press Enter." : "Cancel is selected.",
    ],
  };
}

const SCENARIOS = {
  clean: {
    steps: ["search_products", "view_product", "create_order"],
    order: { total: "54.00", items: [SHOES.name], simulated_hijack: false },
    verdict: verdict("Low", [], "54.00", [SHOES]),
  },
  compromised: {
    steps: ["search_products", "view_product", "create_order"],
    order: { total: "79.00", items: [SHOES.name, GIFT.name], simulated_hijack: true },
    verdict: verdict("High", ["It includes a 25.00 USD gift card you didn't ask for."], "79.00", [
      SHOES,
      GIFT,
    ]),
  },
  gift_card: { steps: ["search_products", "view_product"], order: null, verdict: null },
};

type ScenarioName = keyof typeof SCENARIOS;

const STUB_PAYPAL = `
window.paypal = {
  version: "test-stub",
  Buttons(props) {
    // Like PayPal's own close(), which removes its frame when React unmounts the buttons.
    const button = document.createElement("button");
    return {
      isEligible: () => true,
      render(container) {
        const host = typeof container === "string" ? document.querySelector(container) : container;
        button.type = "button";
        button.textContent = "PayPal (test stub)";
        button.onclick = async () => {
          const orderID = await props.createOrder({}, {});
          await props.onApprove({ orderID }, {});
        };
        host.append(button);
        return Promise.resolve();
      },
      close: () => {
        button.remove();
        return Promise.resolve();
      },
      updateProps: () => Promise.resolve(),
    };
  },
};`;

async function mockBackend(page: Page) {
  const state = { decision: "created", shown: null as object | null, captureId: null as string | null,
    confirmed: null as object | null };
  let scenario: ScenarioName = "clean";

  await page.route("**/sdk/js**", (route) =>
    route.fulfill({ contentType: "application/javascript", body: STUB_PAYPAL }),
  );
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, "");
    const json = (body: object, status = 200) => route.fulfill({ status, json: body });

    if (path === "/config") {
      return json({ paypal_client_id: "test", currency: "USD", demo_attacks_enabled: true });
    }
    if (path === "/mandates") {
      return json({
        mandate_id: "M1", status: "draft", fields: MANDATE_FIELDS, max_total_cents: 8000,
        store: "Trailhead Running Co.", problems: [], questions: [], spoken: READ_BACK,
      });
    }
    if (path === "/mandates/M1/confirm") {
      state.confirmed = route.request().postDataJSON();
      return json({
        mandate_id: "M1", status: "confirmed", fields: MANDATE_FIELDS, max_total_cents: 8000,
        store: "Trailhead Running Co.", problems: [], spoken: READ_BACK,
      });
    }
    if (path === "/agent/runs") {
      scenario = route.request().postDataJSON().scenario;
      const chosen = SCENARIOS[scenario];
      return json({
        scenario,
        order: chosen.order && { order_id: "ORDER1", store: "Trailhead Running Co.", ...chosen.order },
        steps: chosen.steps.map((tool) => ({
          tool,
          arguments: tool === "search_products" ? { query: "running shoes" } : { product_id: "th-road" },
          result: "ok",
        })),
        final_text: "I won't add a gift card the customer didn't ask for.",
      });
    }
    const current = SCENARIOS[scenario].verdict;
    if (path === "/guard/preview") return json(current!);
    if (path === "/guard/check") {
      state.decision = "checked";
      state.shown = current;
      return json(current!);
    }
    if (path === "/guard/confirm") {
      state.decision = "captured";
      state.captureId = "CAPTURE1";
      return json({ status: "captured", capture_id: "CAPTURE1" });
    }
    if (path === "/guard/cancel") {
      state.decision = "cancelled";
      return json({ status: "cancelled" });
    }
    if (path === "/receipts/ORDER1") {
      return json({
        order_id: "ORDER1", decision: state.decision, decided_at: "2026-11-05 10:00:00",
        capture_id: state.captureId,
        asked: { request: "Running shoes from Trailhead, size 10, under $80", read_back: READ_BACK },
        shown: state.shown,
      });
    }
    return json({ detail: `Unmocked ${path}` }, 404);
  });
  return state;
}

let backendState: Awaited<ReturnType<typeof mockBackend>>;

async function expectNoViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(results.violations).toEqual([]);
}

async function confirmMandate(page: Page) {
  await page.goto("/");
  await page.getByLabel(/Say what you want/).fill("Running shoes from Trailhead, size 10, under $80");
  await page.getByRole("button", { name: "Read it back to me" }).click();
  await expect(page.getByRole("heading", { name: "Check what the agent will do" })).toBeFocused();
  await expectNoViolations(page);
  await page.getByRole("button", { name: "Confirm and let the agent shop" }).click();
  await expect(page.getByRole("heading", { name: /Confirmed/ })).toBeFocused();
}

async function shop(page: Page, label: RegExp) {
  await page.getByRole("radio", { name: label }).check();
  await page.getByRole("button", { name: "Let the agent shop" }).click();
  await expect(page.getByRole("heading", { name: "What the agent did" })).toBeFocused();
}

test.beforeEach(async ({ page }) => {
  backendState = await mockBackend(page);
});

test("honest store: the check passes, one press pays, the receipt shows it", async ({ page }) => {
  await confirmMandate(page);
  await shop(page, /Honest store/);
  await expect(page.getByRole("list", { name: "The guard's check" })).toContainText(
    "This order matches what you asked for.",
  );
  await expectNoViolations(page);

  await page.getByRole("button", { name: "PayPal (test stub)" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toHaveAccessibleName("Review payment: Low risk");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");

  await expect(page.getByRole("heading", { name: "Consent receipt" })).toBeFocused();
  await expect(page.getByText("You paid.")).toBeVisible();
  await expect(page.getByText("PayPal capture CAPTURE1", { exact: false })).toBeVisible();
  await expectNoViolations(page);
});

test("hijacked agent: warned before PayPal, cancelled in the dialog, receipt keeps what was said", async ({
  page,
}) => {
  await confirmMandate(page);
  await shop(page, /Simulated hijacked agent/);
  await expect(page.getByText("Simulation: a hijacked agent added a $25 gift card")).toBeVisible();
  await expect(page.getByText("This order is not what you asked for. You can discard it")).toBeVisible();
  await expectNoViolations(page);

  await page.getByRole("button", { name: "PayPal (test stub)" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toHaveAccessibleName("Review payment: High risk");
  await expect(page.getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Escape");

  await expect(page.getByText("You cancelled. Nothing was charged.")).toBeVisible();
  await expect(page.getByText("It includes a 25.00 USD gift card you didn't ask for.")).toBeVisible();
  await expectNoViolations(page);
});

test("a bad order can be discarded before PayPal ever opens", async ({ page }) => {
  await confirmMandate(page);
  await shop(page, /Simulated hijacked agent/);
  await page.getByRole("button", { name: "Discard this order" }).click();
  await expect(page.getByText("You cancelled. Nothing was charged.")).toBeVisible();
  await expect(page.getByText("You discarded the order before approving it")).toBeVisible();
});

test("an agent that refuses to order says why, and no PayPal button appears", async ({ page }) => {
  await confirmMandate(page);
  await shop(page, /Poisoned product page/);
  await expect(page.getByText("The agent did not order anything.")).toBeVisible();
  await expect(page.getByRole("button", { name: "PayPal (test stub)" })).toHaveCount(0);
});

test("start again clears the old request", async ({ page }) => {
  await confirmMandate(page);
  await shop(page, /Simulated hijacked agent/);
  await page.getByRole("button", { name: "Discard this order" }).click();
  await page.getByRole("button", { name: "Start a new request" }).click();
  await expect(page.getByLabel(/Say what you want/)).toHaveValue("");
  await expect(page.getByRole("heading", { name: "Let the agent shop" })).toHaveCount(0);
});

test("edited fields keep their spaces and commas, and are cleaned only on confirm", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Say what you want/).fill("Running shoes from Trailhead, size 10, under $80");
  await page.getByRole("button", { name: "Read it back to me" }).click();

  const store = page.getByLabel(/^Store/);
  await store.clear();
  await store.pressSequentially("Trailhead Running Co. ");
  await expect(store).toHaveValue("Trailhead Running Co. ");
  const details = page.getByLabel(/^Must be/);
  await details.pressSequentially("road, neutral, ");
  await expect(details).toHaveValue("road, neutral, ");

  await page.getByRole("button", { name: "Confirm and let the agent shop" }).click();
  await expect(page.getByRole("heading", { name: /Confirmed/ })).toBeFocused();
  expect(backendState.confirmed).toMatchObject({
    store: "Trailhead Running Co.",
    details: ["road", "neutral"],
    brand: null,
  });
});
