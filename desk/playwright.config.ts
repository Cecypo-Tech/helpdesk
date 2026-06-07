import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Playwright end-to-end test configuration for the Helpdesk frontend (`desk/`).
 *
 * The Helpdesk SPA is served by the Frappe site under the `/helpdesk/` base path.
 * Tests run against a *running* site — start the bench first (see e2e/README.md):
 *
 *   PLAYWRIGHT_BASE_URL=http://dev.localhost:8002 yarn test:e2e
 *
 * Authentication is opt-in: set PLAYWRIGHT_HD_USER and PLAYWRIGHT_HD_PASSWORD and
 * the "authenticated" project logs in once and reuses the session. Without them,
 * only the credential-free "smoke" project runs.
 */

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "http://dev.localhost:8002";

// Where the logged-in session (cookies) is cached between authenticated specs.
const STORAGE_STATE = path.join(__dirname, "e2e", ".auth", "user.json");

// Authenticated specs only run when credentials are provided in the environment.
const hasCredentials = Boolean(
  process.env.PLAYWRIGHT_HD_USER && process.env.PLAYWRIGHT_HD_PASSWORD
);

export default defineConfig({
  testDir: "./e2e",
  // Run tests in files in parallel.
  fullyParallel: true,
  // Fail the build on CI if you accidentally left test.only in the source.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["html", { open: "never" }], ["list"]],
  timeout: 30_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: BASE_URL,
    // Collect a trace on the first retry of a failing test — open with
    // `npx playwright show-trace`.
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // Frappe dev sites are commonly accessed over http on custom hostnames.
    ignoreHTTPSErrors: true,
  },

  projects: [
    {
      // Credential-free checks: the app is reachable and routes correctly.
      name: "smoke",
      testMatch: /smoke\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    // Authenticated flows — only registered when credentials are present so
    // `npx playwright test` works out of the box without secrets.
    ...(hasCredentials
      ? [
          {
            name: "setup",
            testMatch: /auth\.setup\.ts/,
            use: { ...devices["Desktop Chrome"] },
          },
          {
            name: "authenticated",
            testDir: "./e2e/authenticated",
            use: {
              ...devices["Desktop Chrome"],
              storageState: STORAGE_STATE,
            },
            dependencies: ["setup"],
          },
        ]
      : []),
  ],
});
