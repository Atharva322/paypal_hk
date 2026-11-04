import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import type { GuardSummary, RiskLevel } from "../src/types";

const METHOD = { Low: "press", Medium: "press_twice", High: "type_total" } as const;

function summary(level: RiskLevel, pauseSeconds = 3): GuardSummary {
  const hits =
    level === "Low" ? [] : [{ code: "price_mismatch", weight: 60, reason: "The total changed." }];
  return {
    order_id: "ORDER1",
    payee: "merchant@example.com",
    total: "49.99",
    currency: "USD",
    items: [{ name: "Wireless earbuds", quantity: 1, unit_price: "49.99" }],
    risk: { level, score: hits.length * 60, hits },
    confirmation: {
      method: METHOD[level],
      pause_seconds: level === "High" ? pauseSeconds : 0,
    },
    spoken: [`Line for ${level}.`, "Second line."],
  };
}

interface Calls {
  confirms: Record<string, unknown>[];
  cancels: number;
}

async function open(page: Page, level: RiskLevel, confirmStatus = 200): Promise<Calls> {
  const calls: Calls = { confirms: [], cancels: 0 };
  await page.route("**/api/guard/check", (route) => route.fulfill({ json: summary(level) }));
  await page.route("**/api/guard/confirm", (route) => {
    calls.confirms.push(route.request().postDataJSON());
    return confirmStatus === 200
      ? route.fulfill({ json: { status: "captured", capture_id: "CAPTURE1" } })
      : route.fulfill({ status: confirmStatus, json: { detail: "Order changed" } });
  });
  await page.route("**/api/guard/cancel", (route) => {
    calls.cancels += 1;
    return route.fulfill({ json: { status: "cancelled" } });
  });
  await page.goto("/test/fixture.html");
  await page.click("#pay");
  await expect(page.locator("dialog")).toBeVisible();
  return calls;
}

const CAPTURED = '{"status":"captured","captureId":"CAPTURE1"}';
const CANCELLED = '{"status":"cancelled"}';

for (const level of ["Low", "Medium", "High"] as const) {
  // What a screen reader is handed when the dialog opens: its name, then its description.
  test(`${level}: assistive technology gets the title and the spoken summary`, async ({
    page,
  }) => {
    await open(page, level);
    const dialog = page.getByRole("dialog");
    await expect(dialog).toHaveAccessibleName(`Review payment: ${level} risk`);
    await expect(dialog).toHaveAccessibleDescription(`Line for ${level}. Second line.`);
    // Nothing is pushed through the live region on open, so the two cannot overlap.
    await expect(page.getByTestId("live")).toHaveText("");
  });
}

test("the dialog exposes its controls by role and name", async ({ page }) => {
  await open(page, "High");
  await expect(page.getByRole("dialog")).toMatchAriaSnapshot(`
    - 'heading "Review payment: High risk" [level=2]'
    - paragraph: Line for High. Second line.
    - list "Why this was flagged":
      - listitem: The total changed.
    - textbox "Type the total to pay anyway"
    - button "Confirm payment" [disabled]
    - button "Cancel"
  `);
});

test("low risk: focus lands on the title", async ({ page }) => {
  await open(page, "Low");
  await expect(page.locator("#pg-title")).toBeFocused();
});

test("low risk: Tab to Confirm and Enter pays, as the instruction says", async ({ page }) => {
  const calls = await open(page, "Low");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Confirm payment" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#result")).toHaveText(CAPTURED);
  expect(calls.confirms).toHaveLength(1);
});

test("medium risk: Enter twice on Confirm pays", async ({ page }) => {
  const calls = await open(page, "Medium");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("live")).toHaveText("Press Confirm payment again to pay.");
  expect(calls.confirms).toHaveLength(0);
  await page.waitForTimeout(450);
  await page.keyboard.press("Enter");
  await expect(page.locator("#result")).toHaveText(CAPTURED);
  expect(calls.confirms[0]).toMatchObject({ acknowledged_risk: true });
});

test("medium risk: a held Enter does not pay", async ({ page }) => {
  const calls = await open(page, "Medium");
  await page.keyboard.press("Tab");
  await page.keyboard.down("Enter");
  await page.waitForTimeout(450);
  for (let i = 0; i < 5; i += 1) {
    await page.keyboard.down("Enter");
  }
  await page.keyboard.up("Enter");
  await page.waitForTimeout(100);
  await expect(page.locator("dialog")).toBeVisible();
  expect(calls.confirms).toHaveLength(0);
});

test("low risk: C pays once and focus returns to the store", async ({ page }) => {
  const calls = await open(page, "Low");
  await page.keyboard.press("c");
  await expect(page.locator("#result")).toHaveText(CAPTURED);
  await expect(page.locator("#result")).toBeFocused();
  await expect(page.locator("dialog")).toHaveCount(0);
  expect(calls.confirms).toHaveLength(1);
});

test("the store's own onApprove can never run", async ({ page }) => {
  await open(page, "Low");
  await page.keyboard.press("x");
  await expect(page.locator("#result")).toHaveText(CANCELLED);
});

test("X cancels and never confirms", async ({ page }) => {
  const calls = await open(page, "Low");
  await page.keyboard.press("x");
  await expect(page.locator("#result")).toHaveText(CANCELLED);
  expect(calls).toEqual({ confirms: [], cancels: 1 });
});

test("Escape cancels through the backend", async ({ page }) => {
  const calls = await open(page, "Low");
  await page.keyboard.press("Escape");
  await expect(page.locator("#result")).toHaveText(CANCELLED);
  expect(calls.cancels).toBe(1);
});

test("Ctrl+C does not pay", async ({ page }) => {
  const calls = await open(page, "Low");
  await page.keyboard.press("Control+c");
  await expect(page.locator("dialog")).toBeVisible();
  expect(calls.confirms).toHaveLength(0);
});

test("buttons work by keyboard alone", async ({ page }) => {
  const calls = await open(page, "Low");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Confirm payment" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#result")).toHaveText(CANCELLED);
  expect(calls.confirms).toHaveLength(0);
});

test("focus cannot reach the store behind the dialog", async ({ page }) => {
  await open(page, "Low");
  for (let i = 0; i < 8; i += 1) {
    await page.keyboard.press("Tab");
    const outside = await page.evaluate(
      () => document.querySelector("main")!.contains(document.activeElement),
    );
    expect(outside).toBe(false);
  }
});

test("medium risk: one press warns, a second press pays", async ({ page }) => {
  const calls = await open(page, "Medium");
  await page.keyboard.press("c");
  await expect(page.getByTestId("live")).toHaveText("Press Confirm payment again to pay.");
  expect(calls.confirms).toHaveLength(0);
  await page.waitForTimeout(450);
  await page.keyboard.press("c");
  await expect(page.locator("#result")).toHaveText(CAPTURED);
  expect(calls.confirms[0]).toMatchObject({ acknowledged_risk: true });
});

test("medium risk: a bounced key does not pay", async ({ page }) => {
  const calls = await open(page, "Medium");
  await page.keyboard.press("c");
  await page.keyboard.press("c");
  await page.waitForTimeout(100);
  await expect(page.locator("dialog")).toBeVisible();
  expect(calls.confirms).toHaveLength(0);
});

test("medium risk: a held key does not pay", async ({ page }) => {
  const calls = await open(page, "Medium");
  await page.keyboard.down("c");
  await page.waitForTimeout(450);
  await page.keyboard.down("c");
  await page.keyboard.up("c");
  await page.waitForTimeout(100);
  expect(calls.confirms).toHaveLength(0);
});

test("high risk: Cancel has focus and Enter backs out", async ({ page }) => {
  const calls = await open(page, "High");
  await expect(page.getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#result")).toHaveText(CANCELLED);
  expect(calls.confirms).toHaveLength(0);
});

test("high risk: C does nothing", async ({ page }) => {
  const calls = await open(page, "High");
  await page.keyboard.press("c");
  await page.waitForTimeout(3200);
  await page.keyboard.press("c");
  await expect(page.locator("dialog")).toBeVisible();
  expect(calls.confirms).toHaveLength(0);
});

test("high risk: confirm is refused during the pause, then needs the total", async ({ page }) => {
  const calls = await open(page, "High");
  const confirm = page.getByRole("button", { name: "Confirm payment" });
  await expect(confirm).toHaveAttribute("aria-disabled", "true");
  await page.getByLabel("Type the total to pay anyway").fill("49.99");
  // By keyboard: aria-disabled keeps the button reachable so the refusal can be heard.
  await confirm.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("live")).toHaveText(/^Wait \d more seconds?\.$/);
  expect(calls.confirms).toHaveLength(0);

  await expect(confirm).toHaveAttribute("aria-disabled", "false");
  await expect(page.getByTestId("live")).toContainText("The pause is over");
  await confirm.click();
  await expect(page.locator("#result")).toHaveText(CAPTURED);
  expect(calls.confirms[0]).toMatchObject({ typed_total: "49.99" });
});

test("high risk: letters typed in the total do not trigger shortcuts", async ({ page }) => {
  const calls = await open(page, "High");
  const input = page.getByLabel("Type the total to pay anyway");
  await input.focus();
  await page.keyboard.type("cx");
  await expect(input).toHaveValue("cx");
  expect(calls).toEqual({ confirms: [], cancels: 0 });
});

test("a refused payment stays open, is announced and can be cancelled", async ({ page }) => {
  const calls = await open(page, "Low", 409);
  await page.keyboard.press("c");
  await expect(page.getByTestId("live")).toHaveText("Order changed");
  await expect(page.locator("dialog")).toBeVisible();
  await page.keyboard.press("x");
  await expect(page.locator("#result")).toHaveText(CANCELLED);
  expect(calls.cancels).toBe(1);
});

for (const level of ["Low", "Medium", "High"] as const) {
  test(`${level}: axe finds no WCAG A or AA violations`, async ({ page }) => {
    await open(page, level);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  });

  test(`${level}: confirm stays reachable at phone width`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await open(page, level);
    const overflow = await page.evaluate(() => {
      const dialog = document.querySelector("dialog")!;
      return dialog.scrollWidth - dialog.clientWidth;
    });
    expect(overflow).toBe(0);
    await page.getByRole("button", { name: "Confirm payment" }).scrollIntoViewIfNeeded();
    await expect(page.getByRole("button", { name: "Confirm payment" })).toBeInViewport();
  });
}
