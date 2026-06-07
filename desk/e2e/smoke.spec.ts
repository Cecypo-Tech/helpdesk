import { expect, test } from "@playwright/test";

/**
 * Credential-free smoke tests. These verify that the Helpdesk SPA is reachable
 * and that an unauthenticated visitor is routed to the login screen. They are
 * the first thing to run after starting the bench — if these fail, the site
 * isn't up at PLAYWRIGHT_BASE_URL.
 */

test("helpdesk frontend is reachable", async ({ page }) => {
  const response = await page.goto("/helpdesk/");
  expect(response, "no HTTP response — is the bench running?").not.toBeNull();
  expect(response!.status(), "expected a successful status").toBeLessThan(400);
});

test("signed-out visitors are routed to login", async ({ page }) => {
  await page.goto("/helpdesk/tickets");
  // Frappe / the SPA router sends unauthenticated users to /login.
  await expect(page).toHaveURL(/login/i);
});
