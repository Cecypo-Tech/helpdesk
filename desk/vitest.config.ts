import { defineConfig } from "vitest/config";

// Unit tests for framework-free logic (see src/utils). Component and flow
// coverage lives in the Playwright suite, so no DOM environment is set up here.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/__tests__/**/*.spec.ts"],
  },
});
