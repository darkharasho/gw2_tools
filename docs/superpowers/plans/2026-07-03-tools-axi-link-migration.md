# tools.axi.link Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the AxiTools marketing site off GitHub Pages onto Cloudflare Workers Static Assets at `tools.axi.link`, add Privacy Policy + Terms of Service pages, and route `support@axi.link` — so the Discord app can be submitted for verification.

**Architecture:** A new repo-root `site/` directory holds the entire static site (moved `index.html` + `assets/`, plus new `privacy.html`/`terms.html`). A root `wrangler.jsonc` serves `site/` as Workers Static Assets with `tools.axi.link` as an auto-provisioned custom domain, deployed via `wrangler deploy`. The GitHub Pages workflow is retired. `support@axi.link` is added as a Cloudflare Email Routing rule to `project96@gmail.com`.

**Tech Stack:** Cloudflare Workers Static Assets, `wrangler` (via minimal root `package.json`), static HTML/CSS. Repo is otherwise Python.

## Global Constraints

- Operator name in all legal copy: **AxiTools**. Contact email: **support@axi.link**. Jurisdiction: **generic** (do not name a state/country).
- Custom domain: **tools.axi.link**. Cloudflare zone `axi.link` is already on Cloudflare.
- Mirror sibling convention exactly: `axiroster/wrangler.jsonc` and `axiforge/wrangler.jsonc` (Workers Static Assets + `custom_domain: true`). **Do not** add `not_found_handling: single-page-application` (multi-page site, not an SPA).
- **Use the installed Cloudflare plugin skills.** Load `cloudflare:wrangler` before running any `wrangler` command, `cloudflare:workers-best-practices` when authoring/reviewing `wrangler.jsonc`, and `cloudflare:cloudflare-email-service` for the `support@axi.link` routing. Bias to current Cloudflare docs over memory.
- Legal pages are practical, honest, and **not lawyer-reviewed** — state this in-page.
- Site files are **moved**, not copied. One source of truth.
- Reuse existing site design tokens for legal pages (fonts + `:root` palette below) so they match the landing page.

**Shared design tokens** (copy verbatim into legal pages `<head>`):
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=Orbitron:wght@600;700;800&display=swap" rel="stylesheet">
```
```css
:root{
  --bg:#06070f; --ink:#eaf6ff; --muted:#8ea2c4; --line:rgba(120,160,255,.18);
  --cyan:#46e6ff; --mag:#c764ff; --teal:#39ffd0; --brand:#995d25;
}
```

---

## Task 1: Scaffold `site/`, wrangler config, retire GitHub Pages

**Files:**
- Create: `site/` (move `docs/index.html` → `site/index.html`, `docs/assets/` → `site/assets/`)
- Create: `wrangler.jsonc`
- Create: `package.json`
- Modify: `.gitignore` (add `node_modules/`, `.wrangler/`)
- Delete: `.github/workflows/deploy-pages.yml`

**Interfaces:**
- Produces: a deployable `site/` asset root and `npm run deploy:site` → `wrangler deploy`. Later tasks add `site/privacy.html`, `site/terms.html`, and footer links in `site/index.html`.

- [ ] **Step 1: Move the site into `site/` (preserve git history)**

```bash
mkdir -p site
git mv docs/index.html site/index.html
git mv docs/assets site/assets
```
Verify asset paths still resolve (they are relative, e.g. `assets/axitools-logo.svg`, so no rewrite needed):
```bash
grep -n 'src="assets/\|href="assets/' site/index.html | head
```
Expected: matches print; paths remain `assets/...` (now relative to `site/`).

- [ ] **Step 2: Create `wrangler.jsonc`**

```jsonc
{
  "$schema": "node_modules/wrangler/config-schema.json",
  "name": "axitools",
  "compatibility_date": "2026-07-03",
  // Pure static multi-page marketing site (no Worker script): serve ./site as-is.
  // Static-asset serving on Workers is free + unlimited.
  "assets": { "directory": "./site" },
  // Custom domain — axi.link DNS is already on Cloudflare, so the first deploy
  // provisions tools.axi.link + its DNS record.
  "routes": [{ "pattern": "tools.axi.link", "custom_domain": true }]
}
```

- [ ] **Step 3: Create minimal `package.json`**

```json
{
  "name": "axitools-site",
  "private": true,
  "scripts": {
    "deploy:site": "wrangler deploy"
  },
  "devDependencies": {
    "wrangler": "^4"
  }
}
```

- [ ] **Step 4: Update `.gitignore`**

Append:
```
node_modules/
.wrangler/
```

- [ ] **Step 5: Retire the GitHub Pages workflow**

```bash
git rm .github/workflows/deploy-pages.yml
```

- [ ] **Step 6: Install wrangler and dry-run validate the config**

First load the `cloudflare:wrangler` skill. Then:
```bash
npm install
npx wrangler deploy --dry-run
```
Expected: dry run succeeds, reports it will upload assets from `./site`, and shows the `tools.axi.link` custom domain route. No auth needed for `--dry-run`. If it errors on config, reconcile against `cloudflare:workers-best-practices`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(site): move marketing site to site/ for Cloudflare Workers static assets

Retire GitHub Pages deploy in favor of wrangler deploy to tools.axi.link."
```

---

## Task 2: Privacy Policy page

**Files:**
- Create: `site/privacy.html`

**Interfaces:**
- Consumes: shared design tokens (Global Constraints).
- Produces: `https://tools.axi.link/privacy.html`, linked from footer in Task 4.

- [ ] **Step 1: Write `site/privacy.html`**

Self-contained page using the shared tokens. Full content:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AxiTools — Privacy Policy</title>
<meta name="description" content="How the AxiTools Guild Wars 2 Discord bot collects, stores, and handles data.">
<link rel="icon" type="image/svg+xml" href="assets/favicon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=Orbitron:wght@600;700;800&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#06070f; --ink:#eaf6ff; --muted:#8ea2c4; --line:rgba(120,160,255,.18);
    --cyan:#46e6ff; --mag:#c764ff; --teal:#39ffd0; --brand:#995d25;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth}
  body{color:var(--ink);font-family:'Rajdhani',system-ui,-apple-system,sans-serif;line-height:1.6;
    background:
      radial-gradient(900px 600px at 80% -5%, rgba(199,100,255,.16), transparent 55%),
      radial-gradient(900px 600px at 8% 8%, rgba(70,230,255,.14), transparent 55%),
      #06070f;min-height:100vh;}
  .wrap{max-width:820px;margin:0 auto;padding:48px 22px 80px}
  a{color:var(--cyan)}
  h1{font-family:'Orbitron',sans-serif;font-size:30px;letter-spacing:.02em;margin-bottom:6px}
  h2{font-family:'Orbitron',sans-serif;font-size:18px;margin:34px 0 10px;color:var(--cyan)}
  p,li{color:var(--ink);font-size:16px;margin-bottom:10px}
  ul{padding-left:22px}
  .muted{color:var(--muted);font-size:14px}
  .back{display:inline-block;margin-bottom:26px;color:var(--muted);text-decoration:none}
  .note{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:22px 0;color:var(--muted);font-size:14px}
  footer{border-top:1px solid var(--line);margin-top:40px;padding-top:20px}
  footer a{color:var(--muted);margin-right:16px}
</style>
</head>
<body>
  <div class="wrap">
    <a class="back" href="index.html">← AxiTools</a>
    <h1>Privacy Policy</h1>
    <p class="muted">Last updated: 3 July 2026 · Operator: AxiTools · Contact: <a href="mailto:support@axi.link">support@axi.link</a></p>

    <div class="note">AxiTools is a free, community Guild Wars 2 Discord bot. This policy explains what data the bot stores and why. It is written to be honest and practical; it is not legal advice and has not been lawyer-reviewed.</div>

    <h2>What we collect and why</h2>
    <ul>
      <li><strong>Discord identifiers &amp; configuration</strong> — guild (server) IDs, user IDs, and the roles/channels an admin configures, so the bot can operate per-server with isolated settings.</li>
      <li><strong>Guild Wars 2 API keys &amp; account data</strong> — when a user adds a GW2 API key, we store the key plus the linked account name, character names, granted permissions, and associated GW2 guild IDs, to power roster, verification, and build features.</li>
      <li><strong>Cached GW2 guild details</strong> — guild names and tags fetched from the official GW2 API for display labels.</li>
      <li><strong>Build records</strong> — professions, URLs, chat codes, descriptions, and edit metadata for builds you save.</li>
      <li><strong>Subscriptions</strong> — RSS feeds and game-update-notes channels you configure.</li>
      <li><strong>Audit events</strong> — records of Discord actions and GW2 guild-log entries, for server audit features.</li>
      <li><strong>AxiVale desktop keys</strong> — we store only a SHA-256 <em>hash</em> of each desktop key; the raw key is never stored.</li>
    </ul>

    <h2>How your data is stored</h2>
    <p>Data is stored in SQLite databases that are <strong>encrypted at rest with SQLCipher</strong>, and is isolated per Discord server. We do not sell or share your data. Guild Wars 2 data is retrieved from ArenaNet's official GW2 API using the API key you provide.</p>

    <h2>Third parties</h2>
    <p>The bot interacts with <a href="https://discord.com/privacy" target="_blank" rel="noopener">Discord</a> and the ArenaNet Guild Wars 2 API. Your use of Discord is governed by Discord's own policies.</p>

    <h2>Data retention &amp; deletion</h2>
    <p>Data is removed when a server administrator deletes it, revokes keys, or removes the bot from the server. You may request deletion of your data at any time by emailing <a href="mailto:support@axi.link">support@axi.link</a>.</p>

    <h2>Children</h2>
    <p>AxiTools is used through Discord and is not intended for anyone under the minimum age permitted by Discord's Terms of Service (13+, or higher where required).</p>

    <h2>Changes to this policy</h2>
    <p>We may update this policy; the "last updated" date above will change accordingly. Continued use of the bot after an update constitutes acceptance of the revised policy.</p>

    <footer>
      <a href="index.html">Home</a>
      <a href="terms.html">Terms of Service</a>
      <a href="mailto:support@axi.link">support@axi.link</a>
    </footer>
  </div>
</body>
</html>
```

- [ ] **Step 2: Verify structure and required content**

```bash
python3 -c "import html.parser,sys; p=html.parser.HTMLParser(); p.feed(open('site/privacy.html').read()); print('parsed ok')"
grep -c "support@axi.link" site/privacy.html   # expect >=3
grep -q "SQLCipher" site/privacy.html && grep -q "not lawyer-reviewed\|not legal advice" site/privacy.html && echo "content ok"
```
Expected: `parsed ok`, count ≥ 3, `content ok`.

- [ ] **Step 3: Commit**

```bash
git add site/privacy.html
git commit -m "feat(site): add Privacy Policy page"
```

---

## Task 3: Terms of Service page

**Files:**
- Create: `site/terms.html`

**Interfaces:**
- Consumes: shared design tokens (Global Constraints).
- Produces: `https://tools.axi.link/terms.html`, linked from footer in Task 4.

- [ ] **Step 1: Write `site/terms.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AxiTools — Terms of Service</title>
<meta name="description" content="Terms of Service for the AxiTools Guild Wars 2 Discord bot.">
<link rel="icon" type="image/svg+xml" href="assets/favicon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=Orbitron:wght@600;700;800&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#06070f; --ink:#eaf6ff; --muted:#8ea2c4; --line:rgba(120,160,255,.18);
    --cyan:#46e6ff; --mag:#c764ff; --teal:#39ffd0; --brand:#995d25;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth}
  body{color:var(--ink);font-family:'Rajdhani',system-ui,-apple-system,sans-serif;line-height:1.6;
    background:
      radial-gradient(900px 600px at 80% -5%, rgba(199,100,255,.16), transparent 55%),
      radial-gradient(900px 600px at 8% 8%, rgba(70,230,255,.14), transparent 55%),
      #06070f;min-height:100vh;}
  .wrap{max-width:820px;margin:0 auto;padding:48px 22px 80px}
  a{color:var(--cyan)}
  h1{font-family:'Orbitron',sans-serif;font-size:30px;letter-spacing:.02em;margin-bottom:6px}
  h2{font-family:'Orbitron',sans-serif;font-size:18px;margin:34px 0 10px;color:var(--cyan)}
  p,li{color:var(--ink);font-size:16px;margin-bottom:10px}
  ul{padding-left:22px}
  .muted{color:var(--muted);font-size:14px}
  .back{display:inline-block;margin-bottom:26px;color:var(--muted);text-decoration:none}
  .note{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:22px 0;color:var(--muted);font-size:14px}
  footer{border-top:1px solid var(--line);margin-top:40px;padding-top:20px}
  footer a{color:var(--muted);margin-right:16px}
</style>
</head>
<body>
  <div class="wrap">
    <a class="back" href="index.html">← AxiTools</a>
    <h1>Terms of Service</h1>
    <p class="muted">Last updated: 3 July 2026 · Operator: AxiTools · Contact: <a href="mailto:support@axi.link">support@axi.link</a></p>

    <div class="note">These terms govern your use of the AxiTools Discord bot. They are written to be clear and practical; they are not legal advice and have not been lawyer-reviewed.</div>

    <h2>1. Acceptance</h2>
    <p>By adding AxiTools to a Discord server or using its commands, you agree to these Terms of Service and to the <a href="privacy.html">Privacy Policy</a>. If you do not agree, do not use the bot.</p>

    <h2>2. The service</h2>
    <p>AxiTools is a free, community-run Guild Wars 2 Discord bot providing build management, squad compositions, WvW information, patch-note notifications, and related features. The service is provided at no cost and on a best-effort basis.</p>

    <h2>3. Acceptable use</h2>
    <ul>
      <li>Do not use the bot for any unlawful purpose or to harass, abuse, or harm others.</li>
      <li>Do not attempt to disrupt, overload, reverse-engineer for abuse, or gain unauthorized access to the bot or its infrastructure.</li>
      <li>You are responsible for the content and configuration you create through the bot.</li>
    </ul>

    <h2>4. Third-party services</h2>
    <p>Use of AxiTools also requires compliance with <a href="https://discord.com/terms" target="_blank" rel="noopener">Discord's Terms of Service</a> and ArenaNet's Guild Wars 2 <a href="https://www.guildwars2.com/en/legal/guild-wars-2-user-agreement/" target="_blank" rel="noopener">User Agreement</a>. AxiTools is not affiliated with, endorsed by, or sponsored by Discord or ArenaNet.</p>

    <h2>5. Availability &amp; changes</h2>
    <p>The bot may be modified, interrupted, or discontinued at any time without notice. We may update these terms; the "last updated" date above will change and continued use constitutes acceptance.</p>

    <h2>6. Disclaimer of warranty</h2>
    <p>AxiTools is provided "as is" and "as available", without warranties of any kind, express or implied, including fitness for a particular purpose and non-infringement. We do not warrant that the service will be uninterrupted, error-free, or secure.</p>

    <h2>7. Limitation of liability</h2>
    <p>To the maximum extent permitted by applicable law, the operator of AxiTools shall not be liable for any indirect, incidental, or consequential damages, or any loss of data, arising from your use of or inability to use the bot.</p>

    <h2>8. Contact</h2>
    <p>Questions about these terms: <a href="mailto:support@axi.link">support@axi.link</a>.</p>

    <footer>
      <a href="index.html">Home</a>
      <a href="privacy.html">Privacy Policy</a>
      <a href="mailto:support@axi.link">support@axi.link</a>
    </footer>
  </div>
</body>
</html>
```

- [ ] **Step 2: Verify structure and required content**

```bash
python3 -c "import html.parser; p=html.parser.HTMLParser(); p.feed(open('site/terms.html').read()); print('parsed ok')"
grep -q "as is" site/terms.html && grep -q "Privacy Policy" site/terms.html && grep -q "not lawyer-reviewed\|not legal advice" site/terms.html && echo "content ok"
```
Expected: `parsed ok`, `content ok`.

- [ ] **Step 3: Commit**

```bash
git add site/terms.html
git commit -m "feat(site): add Terms of Service page"
```

---

## Task 4: Link legal pages from the landing page footer

**Files:**
- Modify: `site/index.html` (footer `.flinks` block, ~line 968)

**Interfaces:**
- Consumes: `site/privacy.html`, `site/terms.html` from Tasks 2–3.

- [ ] **Step 1: Add Privacy + Terms links to the footer**

In `site/index.html`, find the footer links block:
```html
      <div class="flinks">
        <a href="#builds">Builds</a>
        <a href="#comps">Comps</a>
        <a href="#wvw">WvW</a>
        <a href="#commands">Commands</a>
        <a href="https://github.com/darkharasho/axitools">GitHub</a>
      </div>
```
Replace with (adds two links, preserves the rest):
```html
      <div class="flinks">
        <a href="#builds">Builds</a>
        <a href="#comps">Comps</a>
        <a href="#wvw">WvW</a>
        <a href="#commands">Commands</a>
        <a href="https://github.com/darkharasho/axitools">GitHub</a>
        <a href="privacy.html">Privacy</a>
        <a href="terms.html">Terms</a>
      </div>
```

- [ ] **Step 2: Verify links present and targets exist**

```bash
grep -q 'href="privacy.html"' site/index.html && grep -q 'href="terms.html"' site/index.html && test -f site/privacy.html && test -f site/terms.html && echo "links ok"
```
Expected: `links ok`.

- [ ] **Step 3: Commit**

```bash
git add site/index.html
git commit -m "feat(site): link Privacy Policy and Terms from footer"
```

---

## Task 5: Deploy to Cloudflare + verify tools.axi.link

**Files:** none (deploy + verification)

**Preconditions (user-provided):** `wrangler` authenticated — either `npx wrangler login` completed, or `CLOUDFLARE_API_TOKEN` (+ `CLOUDFLARE_ACCOUNT_ID` if the account is ambiguous) exported with Workers Scripts edit + Workers Routes edit permission on the `axi.link` zone.

- [ ] **Step 1: Confirm auth**

Load `cloudflare:wrangler` skill, then:
```bash
npx wrangler whoami
```
Expected: prints the authenticated account. If not authed, stop and have the user run `npx wrangler login` or export a token.

- [ ] **Step 2: Deploy**

```bash
npm run deploy:site
```
Expected: uploads assets from `./site`, deploys the `axitools` Worker, and provisions the `tools.axi.link` custom domain + DNS record (first deploy). Note any "custom domain is being provisioned" message — DNS/cert can take a few minutes.

- [ ] **Step 3: Verify the three pages return 200**

Wait for cert provisioning if needed (retry up to a few minutes), then:
```bash
for p in / /privacy.html /terms.html; do echo -n "$p -> "; curl -s -o /dev/null -w "%{http_code}\n" "https://tools.axi.link$p"; done
```
Expected: `200` for all three.

- [ ] **Step 4: Verify a key asset loads**

```bash
curl -s -o /dev/null -w "%{http_code}\n" "https://tools.axi.link/assets/axitools-logo.svg"
```
Expected: `200`.

- [ ] **Step 5: Confirm GitHub Pages no longer double-publishes**

Confirm `.github/workflows/deploy-pages.yml` is deleted (from Task 1) and, optionally, disable the GitHub Pages source in repo settings (manual, note for user). No commit needed here.

---

## Task 6: Route support@axi.link (Cloudflare Email Routing)

**Files:** none (Cloudflare configuration)

**Interfaces:** Delivers mail sent to `support@axi.link` → `project96@gmail.com`, matching the contact address used in the legal pages.

- [ ] **Step 1: MX preflight — do not disrupt existing mail**

Load `cloudflare:cloudflare-email-service` skill. Check current MX so enabling Email Routing won't break existing mail:
```bash
dig +short MX axi.link
```
If MX already points somewhere **other than** Cloudflare's `route*.mx.cloudflare.net` (e.g. Google Workspace), STOP and confirm with the user before enabling Email Routing — enabling it repoints MX to Cloudflare and can disrupt existing delivery. If MX is empty or already Cloudflare, proceed.

- [ ] **Step 2: Enable Email Routing and add the rule**

Preferred (dashboard, per `cloudflare:cloudflare-email-service`): Cloudflare Dashboard → `axi.link` → Email → Email Routing → enable → add destination address `project96@gmail.com` → add custom address `support@axi.link` forwarding to it.

Or via API (needs a token with `Zone → Email Routing → Edit`). Add the destination address:
```bash
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/email/routing/addresses" \
  -H "Authorization: Bearer $CF_EMAIL_TOKEN" -H "Content-Type: application/json" \
  --data '{"email":"project96@gmail.com"}'
```
Then create the routing rule (replace `$ZONE_ID` with the `axi.link` zone id):
```bash
curl -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/email/routing/rules" \
  -H "Authorization: Bearer $CF_EMAIL_TOKEN" -H "Content-Type: application/json" \
  --data '{"name":"support to project96","enabled":true,"matchers":[{"type":"literal","field":"to","value":"support@axi.link"}],"actions":[{"type":"forward","value":["project96@gmail.com"]}]}'
```

- [ ] **Step 3: Verify destination (user action)**

Cloudflare sends a verification email to `project96@gmail.com`. The user must click the confirmation link before forwarding works. Confirm the destination shows **verified** in the dashboard.

- [ ] **Step 4: Send a live test**

After verification, send a test email to `support@axi.link` from any external account and confirm it lands in `project96@gmail.com`.

---

## Task 7: Wire Discord verification URLs (manual portal step)

**Files:** none (Discord Developer Portal)

- [ ] **Step 1: Set the URLs**

Discord Developer Portal → Applications → AxiTools → **General Information**:
- **Terms of Service URL** = `https://tools.axi.link/terms.html`
- **Privacy Policy URL** = `https://tools.axi.link/privacy.html`

Save. (This cannot be automated from the repo — user action.)

- [ ] **Step 2: Confirm reachability from Discord's perspective**

Re-run the 200 checks from Task 5 Step 3 to confirm both URLs are publicly reachable (Discord validates them on submission). Then the app is ready to submit for verification.

---

## Self-Review

- **Spec coverage:** Section 1 (hosting/deploy) → Tasks 1, 5. Section 2 (privacy/terms + footer links) → Tasks 2, 3, 4. Section 3 (email routing → Task 6; Discord verification → Task 7; testing → Task 5 Step 3–4, Task 6 Step 4). Manual-steps summary → covered across Tasks 5–7 preconditions. No gaps.
- **Placeholders:** none — full HTML and commands inline; `$ZONE_ID`/`$CLOUDFLARE_ACCOUNT_ID`/token vars are legitimate user-supplied values, flagged as such.
- **Type/name consistency:** `site/` paths, `deploy:site` script, `tools.axi.link`, `support@axi.link`, and file names (`privacy.html`, `terms.html`) are consistent across all tasks and the footer link targets.
