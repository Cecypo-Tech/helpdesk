# End-to-end tests (Playwright)

Browser-driven tests for the Helpdesk frontend. They drive a real Chromium
against a **running** Frappe site (the SPA lives under `/helpdesk/`).

## One-time setup

Already done in this repo, but for a fresh machine:

```bash
cd desk
yarn install                              # installs @playwright/test
# Ubuntu 26.04 isn't an officially supported Playwright OS yet, so the browser
# download needs the platform override:
PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 npx playwright install chromium
# System libraries Chromium needs (run once, needs sudo):
sudo apt-get install -y libnss3 libnspr4 libasound2t64
```

## Running

1. Start the site so it's reachable (default `http://dev.localhost:8002`):

   ```bash
   # from the bench root: /home/kushal/frappe-bench
   bench serve --port 8002
   ```

2. Run the tests (from `desk/`):

   ```bash
   yarn test:e2e            # headless, all projects
   yarn test:e2e:ui         # interactive UI mode — best for writing/debugging
   yarn test:e2e:headed     # watch the browser
   yarn test:e2e:report     # open the last HTML report
   yarn test:e2e:codegen    # record clicks into test code
   ```

   Point at a different site with `PLAYWRIGHT_BASE_URL`:

   ```bash
   PLAYWRIGHT_BASE_URL=https://dev.cecypo.tech yarn test:e2e
   ```

## Authenticated tests

Smoke tests run without credentials. Tests under `e2e/authenticated/` log in
first and need an agent account:

```bash
PLAYWRIGHT_HD_USER="agent@example.com" \
PLAYWRIGHT_HD_PASSWORD="••••••" \
yarn test:e2e
```

`auth.setup.ts` logs in once via Frappe's `/api/method/login` and caches the
session in `e2e/.auth/user.json` (git-ignored). The `authenticated` project
reuses that session, so individual tests don't re-login.

> Use a dedicated test user, not a personal account. Never commit credentials —
> pass them as environment variables (or a git-ignored `.env`).

## Layout

| Path | What |
|------|------|
| `playwright.config.ts` | Config: base URL, projects, reporters |
| `e2e/smoke.spec.ts` | Credential-free reachability/redirect checks |
| `e2e/auth.setup.ts` | Logs in, saves session (runs only with credentials) |
| `e2e/authenticated/` | Tests that need a logged-in agent |

## Writing a new test

`yarn test:e2e:codegen` opens a browser that records your clicks into runnable
code — the fastest way to start. Prefer locating elements by **role and visible
text** (`getByRole`, `getByText`) over CSS classes, which change often.
