import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "test",
  use: {
    baseURL: "http://localhost:5199",
    // Uses an installed browser. CI sets PW_CHANNEL=chromium.
    channel: process.env.PW_CHANNEL ?? "msedge",
  },
  webServer: {
    command: "npx vite --port 5199 --strictPort",
    url: "http://localhost:5199/test/fixture.html",
    reuseExistingServer: true,
  },
});
