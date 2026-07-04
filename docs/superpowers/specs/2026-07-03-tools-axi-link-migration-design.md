# Migrate marketing site to Cloudflare (tools.axi.link) + legal pages

**Date:** 2026-07-03
**Status:** Approved design, pending implementation plan
**Operator:** AxiTools · **Contact:** support@axi.link

## Goal

Move the AxiTools marketing site off GitHub Pages onto Cloudflare Workers Static
Assets at `tools.axi.link`, and add a Privacy Policy and Terms of Service so the
Discord application can be submitted for verification (verification requires
public ToS + Privacy Policy URLs in the Developer Portal).

## Context

- Current site: single self-contained `docs/index.html` (~70KB, GW2/cyberpunk
  theme) plus `docs/assets/`. Deployed to GitHub Pages by
  `.github/workflows/deploy-pages.yml` on push to `main` touching `docs/**`.
- `axi.link` DNS is already a zone in Cloudflare.
- Sibling static sites already use **Workers Static Assets** with auto-provisioned
  custom domains:
  - `roster.axi.link` → `axiroster/wrangler.jsonc`
  - `build.axi.link` → `axiforge/wrangler.jsonc`
  Both deploy via `wrangler deploy`; the custom domain + DNS record are
  provisioned automatically on first deploy because the zone is on Cloudflare.
- `axitools` is a Python repo (no existing `package.json`/Node tooling).

## Non-goals (YAGNI)

- No CMS, bundler, or build step (site is hand-authored static HTML).
- No analytics, no cookie banner (site sets no cookies).
- No redirect from the old GitHub Pages URL (add later only if requested).
- No SPA fallback (this is a multi-page static site, not an SPA).

## Section 1 — Hosting & deploy

Serve the site from **Cloudflare Workers Static Assets** at `tools.axi.link`,
mirroring `roster.axi.link` / `build.axi.link`.

- **New `site/` directory** at repo root is the entire deployable site:
  - `site/index.html` (moved from `docs/index.html`)
  - `site/assets/**` (moved from `docs/assets/`)
  - `site/privacy.html` (new)
  - `site/terms.html` (new)
  Non-site docs (`docs/*.md`, `docs/superpowers/`) stay in `docs/`.
- **`wrangler.jsonc`** at repo root:
  ```jsonc
  {
    "$schema": "node_modules/wrangler/config-schema.json",
    "name": "axitools",
    "compatibility_date": "2026-07-03",
    "assets": { "directory": "./site" },
    "routes": [{ "pattern": "tools.axi.link", "custom_domain": true }]
  }
  ```
  No `not_found_handling: single-page-application` — default static 404 handling
  is correct for a multi-page site.
- **Minimal `package.json`** at repo root pinning `wrangler` as a devDependency
  with `"deploy:site": "wrangler deploy"`, so deploys are `npm run deploy:site`,
  consistent with siblings. (`.gitignore` already ignores `node_modules/` and
  `.wrangler/`; confirm and add if missing.)
- **First deploy** auto-provisions `tools.axi.link` + its DNS record.
- **Retire GitHub Pages:** delete `.github/workflows/deploy-pages.yml`. Site files
  are *moved* (not copied) so there is a single source of truth.

## Section 2 — Privacy Policy & Terms pages

Two new static pages styled to match the existing site (reuse its `<style>`
design tokens, fonts, dark theme, header/footer shell so they don't look
bolted on). Operator **AxiTools**, contact **support@axi.link**, generic
jurisdiction.

### privacy.html — accurate to the real data model (`DATABASE_SCHEMA.md` + cogs)

- **What is collected & why:**
  - Discord guild IDs, user IDs, role/channel configuration (per-guild bot setup).
  - GW2 API keys + linked account name, character names, granted permissions,
    and GW2 guild IDs (roster / verification features).
  - Cached GW2 guild names/tags.
  - Build records (professions, URLs, chat codes, descriptions, edit metadata).
  - RSS subscriptions and game-update-notes subscriptions.
  - Audit-log events: Discord actions and GW2 guild-log entries.
  - AxiVale desktop key **hashes** (SHA-256; raw keys are never stored).
- **How it is stored:** SQLite, **encrypted at rest with SQLCipher**, isolated
  per guild (`data/guild_<id>/`).
- **Sharing:** not sold or shared; GW2 data is fetched from ArenaNet's official
  GW2 API using the user's own API key.
- **Retention & deletion:** data is removed when a server admin deletes it,
  revokes keys, or removes the bot from the server; deletion requests via
  **support@axi.link**.
- **Third parties:** Discord and the ArenaNet GW2 API.
- **Children:** governed by Discord's 13+ Terms.
- **Changes:** dated "last updated".

### terms.html — plain-language ToS for a free Discord bot

Acceptable use; provided "as-is" with no warranty; no liability; users must
respect Discord's and ArenaNet's/GW2's terms; the bot may change or be
discontinued; generic governing law; contact **support@axi.link**; dated.

### Discoverability

Add footer links to `privacy.html` and `terms.html` in `index.html` (and
cross-link between the two legal pages). Discord verification and users expect
these reachable from the landing page.

> These are practical, honest policies written to satisfy Discord verification.
> They are **not** lawyer-reviewed.

## Section 3 — Email routing, Discord verification, testing

### support@axi.link (Cloudflare Email Routing)

- Add a custom-address rule: `support@axi.link` → forwards to
  `project96@gmail.com`.
- Set up via Cloudflare API/wrangler if an API token with Email Routing edit
  scope is available; otherwise provide exact dashboard steps.
- **Preconditions / gotchas (check, do not hide):**
  1. Cloudflare sends a verification email to `project96@gmail.com` that must be
     clicked before forwarding works.
  2. Email Routing requires the zone's MX records to point at Cloudflare. Check
     current `axi.link` MX state first — if mail is already handled elsewhere,
     enabling this could disrupt it. Do not change MX without confirming.

### Discord verification wiring (manual portal step)

In the Discord Developer Portal (App → General Information) set:
- **Terms of Service URL** = `https://tools.axi.link/terms.html`
- **Privacy Policy URL** = `https://tools.axi.link/privacy.html`

Provide exact instructions; this cannot be automated from here.

### Verification / testing

- After `npm run deploy:site`: `curl -I` against `/`, `/privacy.html`,
  `/terms.html` → expect `200`.
- Confirm assets load (favicon, logo, profession icons) and footer links resolve.
- Confirm the old GitHub Pages deploy no longer double-publishes (workflow
  deleted).
- Send a test email to `support@axi.link`; confirm delivery to
  `project96@gmail.com`.

## Manual steps required from the user (summary)

1. Provide a Cloudflare API token with Email Routing edit scope, **or** perform
   the Email Routing dashboard step.
2. Click the Cloudflare destination-verification email in `project96@gmail.com`.
3. Set the ToS + Privacy Policy URLs in the Discord Developer Portal.
4. `wrangler` must be authenticated (`wrangler login` / `CLOUDFLARE_API_TOKEN`)
   for the first `deploy:site`.
