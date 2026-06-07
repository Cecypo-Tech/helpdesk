import { expect, test as setup } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Logs into the Frappe site once and caches the session cookies so the
 * "authenticated" project can reuse them instead of logging in per test.
 *
 * Requires PLAYWRIGHT_HD_USER and PLAYWRIGHT_HD_PASSWORD in the environment
 * (see e2e/README.md). This file only runs when those are set.
 */

const STORAGE_STATE = path.join(__dirname, ".auth", "user.json");

setup("authenticate", async ({ request }) => {
  const usr = process.env.PLAYWRIGHT_HD_USER;
  const pwd = process.env.PLAYWRIGHT_HD_PASSWORD;

  if (!usr || !pwd) {
    throw new Error(
      "PLAYWRIGHT_HD_USER and PLAYWRIGHT_HD_PASSWORD must be set to run authenticated tests."
    );
  }

  // Frappe's login endpoint sets the `sid` session cookie on success.
  const response = await request.post("/api/method/login", {
    form: { usr, pwd },
  });
  expect(
    response.ok(),
    `Login failed with HTTP ${response.status()} — check credentials and PLAYWRIGHT_BASE_URL.`
  ).toBeTruthy();

  fs.mkdirSync(path.dirname(STORAGE_STATE), { recursive: true });
  await request.storageState({ path: STORAGE_STATE });
});
