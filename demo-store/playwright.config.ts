import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "test",
  use: {
    baseURL: "http://localhost:5198",
    // Uses an installed browser. CI sets PW_CHANNEL=chromium.
    channel: process.env.PW_CHANNEL ?? "msedge",
  },
  webServer: {
    command: "npx vite --port 5198 --strictPort",
    url: "http://localhost:5198",
    reuseExistingServer: true,
  },
});
