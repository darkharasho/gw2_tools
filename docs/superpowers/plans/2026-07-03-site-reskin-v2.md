# AxiTools Site Reskin v2 (de-aegis) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Reskin the already-built AxiTools site so its visual DNA no longer traces to the aegis site, while keeping the spacious layout and profession-color identity.

**Architecture:** The v1 redesign (currently on `main`, live) copied aegis's signature moves — skewed diagonal gradient bands, teardrop "pin" badges, faint outline doodles, and a Discord app-mock hero. This reskin replaces those with an approved new language, captured as three reference mockups under `docs/superpowers/mockups/`:
- **`hero-B-command-console.html`** — the new hero: a centered command-palette (`/build guardian` typed with a blinking caret) + a live profession-tinted result list. Replaces the skewed hero band + Discord app-mock.
- **`accent-A-angular-hud.html`** — the page-wide accent language: `aurora` + `grid` (hex) fixed backdrops replacing `.ambient`/skew bands; angular clip-path panels with corner brackets (`.panel`/`.br`) + a profession-color accent bar (`.bar`) replacing `.mockcard`/`.pin`/`.doodle`; clipped buttons (`.cta`/`.b1`).
- **`wvw-C-tactical-readout.html`** — the WvW feature row's mock: a tactical readout (mini objective map + live standings).

We edit the existing three files in `site/` in place. Structure/section order and copy stay; only the skin changes.

**Tech Stack:** Plain HTML/CSS/vanilla JS; Cloudflare Workers (`npm run deploy:site`).

## Global Constraints

- Files: `site/index.html`, `site/privacy.html`, `site/terms.html`. Each stays ONE self-contained file (one `<style>`, at most one `<script>`), no external CSS/JS beyond the Google Fonts link.
- Fonts unchanged: Orbitron (wordmark + single hero accent word only), Chakra Petch (`--display`, all h1/h2/h3), Inter (`--font` body), JetBrains Mono (`--mono`).
- Palette tokens unchanged: `--bg:#06070f --ink:#eaf6ff --muted:#8ea2c4 --cyan:#46e6ff --mag:#c764ff --teal:#39ffd0` + the nine `--p-*` profession tokens. Profession colors remain accents only.
- **Remove every aegis tell:** no `skewY` anywhere; no `.bgband`/`.bgband2`/`.stripes`; no teardrop `.pin` (the `border-radius:50% 50% 50% 4px` badge); no `.doodle` SVGs; no Discord `.app`/`.side`/`.feed` chrome in the hero.
- Reference mockups are the source of truth for the new CSS/markup — adapt their rules verbatim, wired to real copy and `--p-*` tokens.
- Invite/CTA URL verbatim: `https://discord.com/oauth2/authorize?client_id=1433732142629912626&permissions=8&integration_type=0&scope=bot`
- Internal links extensionless (`/privacy`, `/terms`, `#commands`, `#features`); legal pages use `/#commands`.
- The shared shell (`:root` tokens, fonts link, the new `aurora`+`grid` backdrop CSS+markup, `<header>` CSS+markup, `<footer>` CSS) stays byte-identical across the three files.
- Keep the `.rv`→`.inview` scroll-reveal observer, the `@media (prefers-reduced-motion:reduce)` guard, the `<noscript>` `.rv` fallback, `<main>` landmark, and trailing newlines. Reduced-motion must also freeze the hero caret and show all content.

## Verification model

No unit tests. Each task ends with a visual render (`mcp__sai__sai_render_html` — read the file, pass its contents; load via ToolSearch `select:mcp__sai__sai_render_html` if deferred) plus targeted greps. Final task adds a real-browser (Playwright MCP) desktop + mobile + reduced-motion pass and a `wrangler deploy --dry-run`.

---

### Task 1: Shell reskin — aurora/hex backdrop, angular buttons (all three files)

Replace the `.ambient` backdrop with the `aurora` + `grid` system and switch buttons/CTAs to the clipped (angular) style, across all three files, keeping the shared shell byte-identical.

**Files:** Modify `site/index.html`, `site/privacy.html`, `site/terms.html`.
**Reference:** `docs/superpowers/mockups/accent-A-angular-hud.html` (`.aurora`, `.grid`, `.cta`, `.b1`, `.b2`).

- [ ] **Step 1: Replace the `.ambient` CSS + markup in `index.html`.** Remove the existing `.ambient` rule and its `<div class="ambient">`. Add the `.aurora` and `.grid` CSS from the mockup verbatim, and place `<div class="aurora"></div><div class="grid"></div>` as the first two elements in `<body>` (where `.ambient` was).
- [ ] **Step 2: Switch the header invite pill + hero/closing buttons to clipped style in `index.html`.** Update the `.invite`/`.pill`/`.pill.alt` rules (or the header CTA + hero CTA rules) to use the `clip-path` polygon treatment from the mockup's `.cta`/`.b1`/`.b2`. Keep the invite URL and label. (Hero buttons themselves are rebuilt in Task 2 — here just make the shared header CTA angular.)
- [ ] **Step 3: Apply the identical `.aurora`+`.grid` swap to `privacy.html` and `terms.html`** (they currently carry `.ambient`). The header CTA change from Step 2 also applies. Keep the shell byte-identical to `index.html`.
- [ ] **Step 4: Verify.** Render all three files. Expected: soft aurora glow at top + faint fading hex grid (no diagonal band anywhere), angular-cornered header CTA. Grep to confirm removal: `grep -c "ambient\|skewY\|bgband\|stripes" site/*.html` should be `0` for each file. Confirm `aurora` and `grid` present in all three.
- [ ] **Step 5: Cross-file consistency.** Confirm the `:root`, aurora/grid CSS, and header markup are byte-identical across the three files (`for f in index privacy terms; do sed -n '/<header>/,/<\/header>/p' site/$f.html | md5sum; done` → all equal). Fix drift to match `index.html`.
- [ ] **Step 6: Commit.** `git add site/ && git commit -m "feat(site): replace ambient/skew backdrop with aurora + hex grid, angular CTAs"`

---

### Task 2: Hero → command console (index.html)

Replace the skewed hero band + animated Discord app-mock with the command-palette hero.

**Files:** Modify `site/index.html`.
**Reference:** `docs/superpowers/mockups/hero-B-command-console.html` (`.wrap`, `.kick`, `h1 .g`, `.sub`, `.palette`, `.pin`, `.caret`, `.result`, `.opt`, `.ic`, `.meta`).

- [ ] **Step 1: Remove the old hero.** Delete the entire `.heroband` block markup (the skew `.bgband`, `.stripes`, and the `.app`/`.side`/`.feed`/`.msg` Discord mock) and its associated CSS rules (`.heroband`, `.bgband`, `.stripes`, `.s1`–`.s4`, `@keyframes stripeIn`, `.app`, `.side`, `.ch`, `.chat`, `.feed`, `.msg`, `.av`, `.mhead`, `.mname`, `.mtag`, `.mtime`, `.mtext`, `.dembed*` **only if** `.dembed` is not reused by feature rows — check first; the feature rows are rebuilt in Task 3, so `.dembed` will also go, but confirm nothing else references it before deleting).
- [ ] **Step 2: Remove the feed-playback JS.** In the `<script>`, delete the `msgs`/`typing`/`play()` playback block (the Discord message animation). KEEP the `reduced` const and the `.rv` IntersectionObserver. The caret blink is pure CSS, so no JS is needed for the new hero.
- [ ] **Step 3: Add the command-console hero CSS** from the mockup (`.hero-wrap`/`.kick`/`.palette`/`.pin`/`.caret`/`@keyframes bl`/`.result`/`.opt`/`.ic`/`.meta`/`.go`), into the existing `<style>`. Use the existing `--p-*` tokens for the three result-row `.ic` gradients (Guardian, Necro, Mesmer). Namespace any generic class names that could collide (e.g. the mockup's `.pin` is the palette input row — RENAME it to `.palette-input` to avoid confusion with the removed teardrop pin; update markup to match).
- [ ] **Step 4: Add the hero markup** after `<main>` (before the stats strip), adapting the mockup's `.wrap`/palette. Headline: keep one Orbitron accent word (the mockup uses `slash`; keep an Orbitron `.accent`/`.g` span). Preserve the `#features` scroll target on the "→ See what it does" CTA and the invite URL on the primary CTA. Wrap `.rv` on the hero pieces so reduced-motion/noscript still reveal them, OR leave the hero always-visible (no `.rv`) — pick always-visible for the hero so it never depends on JS.
- [ ] **Step 5: Reduced-motion.** Ensure the `@media (prefers-reduced-motion:reduce)` block freezes the `.caret` blink (e.g. `.caret{animation:none}`), and that the hero content is visible (it is, if always-visible per Step 4).
- [ ] **Step 6: Verify.** Render `index.html`. Expected: centered kicker, big headline with one Orbitron accent word, a command palette showing typed `/build guardian` + blinking caret + three profession-tinted result rows. No Discord app frame, no skew. Confirm no JS console errors and that `grep -c "class=\"app\"\|feed\|skewY" site/index.html` is `0`.
- [ ] **Step 7: Commit.** `git add site/index.html && git commit -m "feat(site): replace Discord-mock hero with command-console hero"`

---

### Task 3: Feature rows reskin — angular HUD panels + WvW tactical readout (index.html)

Replace the teardrop-pin/doodle mock cards with angular HUD panels; the WvW row uses the tactical readout.

**Files:** Modify `site/index.html`.
**Reference:** `docs/superpowers/mockups/accent-A-angular-hud.html` (`.panel`, `.br`, `.ptop`, `.title`, `.bar`, `.rowl`, `.tagline`) and `docs/superpowers/mockups/wvw-C-tactical-readout.html` (`.hud`, `.mmap`, `.obj`, `.rank`, `.rk`).

- [ ] **Step 1: Remove old mock-card CSS.** Delete `.mockcard`, `.pin` (+ color variants), `.doodle`, and any remaining `.dembed*` rules. Keep `.feature`/`.feature.flip`/`.ftext`/`.tags`/`.tag`/`.fmock`/`.eyebrow`/`.rv` layout rules.
- [ ] **Step 2: Add the `.panel` HUD CSS** (`.panel`, `.br.tl`, `.br.br2`, `.ptop`, `.dot`, `.cmd`, `.title`, `.bar`, `.rowl`, `.tagline`) from the accent mockup, into `<style>`. `.bar` takes a per-row `--acc` set to a `--p-*` token.
- [ ] **Step 3: Add the WvW readout CSS** (`.hud`, `.hud .top`, `.pulse`, `.mmap`, `.mmap .obj`, `.rank`, `.rk`, `.rk.you`) from the WvW mockup, into `<style>`.
- [ ] **Step 4: Rebuild each feature row's `.fmock`.** For rows 1 (Builds/Guardian), 2 (Comps/Mesmer), 4 (Runs-itself/Necro) replace the old `.mockcard` markup with a `.panel` (corner brackets `.br.tl`+`.br.br2`, `.ptop` with the `/command`, `.title`, `.bar` with `--acc` = the row's `--p-*`, `.rowl` fields, `.tagline`). Reuse each row's existing copy. For row 3 (Own the WvW week) use the `.hud` tactical readout markup from the WvW mockup. Keep `.rv`/`.rv.d1` on the rebuilt mocks so they still scroll-reveal.
- [ ] **Step 5: Verify.** Render `index.html` and scroll (or screenshot after scrolling in the browser during Task 5). Expected: four rows, each with an angular corner-bracketed panel (profession-accent bar), and the WvW row showing the mini-map + standings readout. No teardrop pins, no doodles. Grep: `grep -c "doodle\|mockcard\|class=\"pin\"" site/index.html` → `0`.
- [ ] **Step 6: Commit.** `git add site/index.html && git commit -m "feat(site): reskin feature rows as angular HUD panels + WvW tactical readout"`

---

### Task 4: Stats strip, grid, setup, closing band reskin (index.html)

Bring the remaining sections into the angular/aurora language and remove the last skew.

**Files:** Modify `site/index.html`.
**Reference:** accent mockup (clip-path, corner brackets, aurora).

- [ ] **Step 1: Closing band — remove skew.** In `.closeband`, delete `.bgband2` (which uses `skewY`) and its `::after`. Replace the band background with a non-skewed treatment consistent with the aurora language: a horizontal gradient wash + top/bottom hairline (e.g. `background:linear-gradient(180deg,rgba(70,230,255,.06),rgba(199,100,255,.06));border-top:1px solid rgba(70,230,255,.15);border-bottom:1px solid rgba(70,230,255,.15)`), keeping the headline + invite pill (angular `.b1` style). No `skewY`.
- [ ] **Step 2: Grid cards → angular.** Give `.gcard` a subtle clipped corner + hairline consistent with `.panel` (e.g. small `clip-path` polygon or a single cut corner + `border:1px solid rgba(70,230,255,.12)`), so cards match the HUD language rather than plain rounded boxes. Keep copy.
- [ ] **Step 3: Setup command block.** Ensure `.cmd .line` uses the mono/hairline treatment (it already does); align its border color to the HUD cyan hairline for consistency. Minor.
- [ ] **Step 4: Stats strip.** Keep as-is structurally; confirm it reads well over the aurora backdrop (it has its own dark glassy background). Adjust only if it clashes.
- [ ] **Step 5: Verify.** Render `index.html`. Expected: closing band is a flat (non-skewed) neon-washed band; grid cards have the angular/hairline HUD look; whole page cohesive with no `skewY`. Grep whole file: `grep -c "skewY\|bgband\|stripes\|doodle\|mockcard" site/index.html` → `0`.
- [ ] **Step 6: Commit.** `git add site/index.html && git commit -m "feat(site): reskin stats/grid/setup/closing band to angular language, drop last skew"`

---

### Task 5: Polish, cross-page consistency, review, redeploy-ready

**Files:** Modify all three as needed for fixes.

- [ ] **Step 1: Desktop render sweep** of all three files via `mcp__sai__sai_render_html`. Note issues.
- [ ] **Step 2: Real-browser pass (Playwright MCP).** Serve `site/` (`python3 -m http.server` in background) and navigate. Screenshot the hero, scroll to `#features` and screenshot the reskinned rows + WvW readout, and the closing band. Load Playwright tools via ToolSearch. Confirm scroll-reveal still fires, the caret blinks, and there's no horizontal overflow at 390px (hero palette + rows stack).
- [ ] **Step 3: Reduced-motion.** Emulate `prefers-reduced-motion: reduce`; confirm the caret is frozen, `.rv` content visible, no infinite animation.
- [ ] **Step 4: Cross-page shell consistency.** `:root`, aurora/grid CSS, header markup byte-identical across the three files (md5 compare). Fix drift.
- [ ] **Step 5: Aegis-tell audit.** `grep -rc "skewY\|bgband\|stripes\|doodle\|mockcard\|class=\"pin\"\|class=\"app\"" site/*.html` → all `0`. Confirm no leftover aegis motifs.
- [ ] **Step 6: Trailing newline check** on all three files; fix if missing.
- [ ] **Step 7: Deploy dry-run.** `npx wrangler deploy --dry-run 2>&1 | tail -20` — validates, does not deploy.
- [ ] **Step 8: Commit fixes.** `git add site/ && git commit -m "polish(site): responsive, reduced-motion, cross-page consistency for reskin"`

---

## Self-Review

- **Spec coverage:** aurora/hex backdrop + angular CTAs (T1); command-console hero replacing Discord mock + skew (T2); HUD feature panels + WvW readout replacing pins/doodles (T3); de-skewed closing band + angular grid (T4); consistency/responsive/reduced-motion/aegis-audit/deploy-dry-run (T5). All aegis tells have an explicit removal + grep-gate.
- **Placeholder scan:** none; every task cites the exact mockup file + classes and concrete grep gates.
- **Name consistency:** the mockup's `.pin` (palette input row) is explicitly RENAMED to `.palette-input` in T2 Step 3 to avoid collision with the removed teardrop `.pin`. `.bar` uses `--acc`=`--p-*` per row. `.rv` observer and reduced-motion/noscript guards preserved throughout.
