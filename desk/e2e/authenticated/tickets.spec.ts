import { expect, test } from "@playwright/test";

/**
 * Example authenticated test. Runs only when PLAYWRIGHT_HD_USER /
 * PLAYWRIGHT_HD_PASSWORD are set (the "authenticated" project logs in first
 * via auth.setup.ts and reuses the session).
 *
 * Treat this as a template: copy the pattern to cover real flows your team
 * cares about (creating a ticket, replying, assigning, WhatsApp tab, etc.).
 */

test("agent can open the tickets list", async ({ page }) => {
  await page.goto("/helpdesk/tickets");

  // We should NOT be bounced to login when authenticated.
  await expect(page).not.toHaveURL(/login/i);
  await expect(page).toHaveURL(/\/helpdesk\/tickets/);

  // The app shell renders.
  await expect(page.locator("body")).toBeVisible();
});
