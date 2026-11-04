import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "src/index.ts",
      name: "PayGuard",
      formats: ["iife", "es"],
      fileName: (format) => `pay-guard.${format}.js`,
    },
  },
});
