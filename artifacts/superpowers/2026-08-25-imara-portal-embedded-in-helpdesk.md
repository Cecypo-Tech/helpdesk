# Imara portal embedded in helpdesk's main body

Date: 2026-08-25
Branches: helpdesk `feat/imara-portal-in-helpdesk-body`; imara_backup `feat/embed-portal-in-helpdesk` (merged to `main`)

## Problem

The helpdesk sidebar carried an "Imara Backup Portal" link that ran
`window.location.href = "/backup"` -- leaving helpdesk entirely for a separate
server-rendered portal. Asked to render it inside helpdesk instead.

## Approach chosen

Iframe embed, selected by the user over a native Vue port and over a phased
port. The portal is ~3,500 lines of Jinja + inline JS across six pages
(`index`, `agents`, `events`, `notifications`, `plan`, `restore`), with
`plan.html` at 1,079 lines and `restore.html` at 1,257. Embedding keeps one
codebase as the source of truth: future portal changes appear in helpdesk with
no porting work.

## Changes

### helpdesk

- `desk/src/pages/backup/BackupPortal.vue` (new) -- a full-bleed iframe on
  `/backup`, no header of its own (the portal carries its own tab row; a
  LayoutHeader here would read as two navigations for one section). Renders an
  explanatory message instead when `backup_portal_enabled` is false, so a direct
  URL hit on a site without the app does not show a 404 inside a frame.
- `desk/src/router/index.ts` -- route `/backup`, name `BackupPortal`, with
  `public`/`auth` meta because the sidebar link reaches customers too. The SPA is
  mounted at `/helpdesk/` (`createWebHistory("/helpdesk/")`), so this is the URL
  `/helpdesk/backup` and does not collide with the web page at `/backup`.
- `desk/src/components/layouts/layoutSettings.ts` -- `backupPortalOption` swaps
  `onClick` for `to: "BackupPortal"`. Label shortened to "Imara Backup", since
  "Portal" no longer describes it.
- `Sidebar.vue`, `MobileSidebar.vue` -- bind `:to` and `:is-active` instead of
  `:on-click`, so the link highlights like every other sidebar entry.

### imara_backup

- `templates/includes/embed_head.html` (new) -- inline `<head>` script stamping
  `bp-embed` on `<html>` when `window.self !== window.top`.
- `public/css/portal.css` -- `html.bp-embed` hides `nav.navbar`,
  `footer.web-footer` and `.page-breadcrumbs`; sets full height and trims the
  container margin.

  The line is drawn at website SHELL vs PORTAL. Suppressed: chrome that wraps
  the page and that helpdesk already provides. Kept: the brand banner and the
  nav pills, which belong to the portal itself. The first pass also hid the
  brand banner on the reasoning that helpdesk names the section -- wrong, since
  helpdesk's sidebar entry is a link label, not a page heading, and hiding it
  left the pane opening straight onto the nav pills with no identity.
- All six `www/backup/*.html` pull the include into their `head_include` block.

### The one design decision worth recording

Frame detection, not `?embed=1`. Every page reached by clicking inside the frame
is itself framed, so the flag re-applies on its own -- **zero link rewriting and
no Python change**. A query parameter would have to be threaded through every
href in `backup_nav.html` and every JS redirect, and would fail silently the
first time one was missed. Keeping the script inline in `<head>` means it runs
before the body paints, so the navbar never flashes.

The nav pills deliberately stay: helpdesk contributes ONE sidebar link, so the
portal's tab row is the only route to the other five pages.

## Verification

Server-side render of all six pages with a real request context:

```
path                       script  in_head  navbar  pills  brand
/backup                    True    True     True    True   True
/backup/agents             True    True     True    True   True
/backup/events             True    True     True    True   True
/backup/notifications      True    True     True    True   True
/backup/plan               True    True     True    True   True
/backup/restore            True    True     True    True   True
```

In a real browser at `/helpdesk/backup`, inside the frame:

```
{"embedClass":"bp-embed","innerUrl":"/backup","navbar":"hidden",
 "footer":"hidden","breadcrumbs":"hidden","navPills":"VISIBLE",
 "tenantStrip":"VISIBLE"}

{"brandDisplay":"block","brandText":"Imara Cloud Backup",
 "brand":{"top":16,"h":139},"nav":{"top":155,"h":41},"navbar":"none"}
```

The banner sits at the top of the pane with the pills below it.

After clicking Plan inside the frame -- the claim that link rewriting is
unnecessary:

```
{"innerUrl":"/backup/plan","embedClass":"bp-embed","navbar":"hidden",
 "footer":"hidden","brand":"hidden","navPills":"VISIBLE",
 "outerUrl":"/helpdesk/backup"}
```

Screenshot confirms the helpdesk sidebar with "Imara Backup" highlighted active,
the Imara Cloud Backup banner heading the pane, and no website chrome.

Note for future browser checks: portal.css is cached aggressively. A CSS change
verified without restarting the browse daemon can report the PREVIOUS rule set --
that happened once here and briefly showed the banner as visible while the cached
stylesheet still hid it.

Tests: `imara_backup/imara_backup/portal/test_portal_embed.py`, 6 cases -- every
page pulls the include, it sits inside `head_include`, the mechanism is still
frame detection, the CSS still hides the website shell, and it does NOT hide the
brand banner or the nav pills. `bench --site dev.localhost run-tests --module
imara_backup.imara_backup.portal.test_portal_embed` -> 6 OK.
`bench build --app helpdesk` succeeds; `BackupPortal-9b06cc9a.js` chunk emitted.

## Review pass

- **Blocker**: none.
- **Major**: none.
- **Minor**: the inner page is not reflected in the browser URL -- `/helpdesk/backup`
  stays fixed while browsing Plan/Restore, so inner pages cannot be bookmarked or
  deep-linked. Fixable with `postMessage`; deliberately deferred.
- **Minor**: browser Back steps through iframe history before leaving the page.
  Inherent to the approach.
- **Minor**: the portal ships a custom dark "Ops" theme (`--bp-bg: #070c14`). In
  helpdesk's dark mode it blends (confirmed in the screenshot). In light mode it
  will read as a dark pane; I could not flip the theme from the automated browser
  session, so that contrast is stated but not visually verified.
- **Nit**: sidebar label changed from "Imara Backup Portal" to "Imara Backup".
  Trivially revertible in `layoutSettings.ts` if the old wording is preferred.

## Out of scope

Restyling the portal to match helpdesk's tokens is the native-port option that
was explicitly not chosen.
