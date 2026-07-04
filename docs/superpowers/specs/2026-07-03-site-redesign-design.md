# AxiTools Site Redesign — Design

**Date:** 2026-07-03
**Status:** Approved for planning
**Scope:** `site/index.html`, `site/privacy.html`, `site/terms.html`

## Summary

Redesign the AxiTools marketing site (tools.axi.link) using the layout system and
spatial rhythm of the sibling **aegis** site (`../aegis/site/index.html`), while
keeping AxiTools' own visual identity. This is a **hybrid**: aegis's structure,
generous whitespace, ambient gradients, animated Discord hero mock, stats strip,
alternating split feature rows, and reveal-on-scroll motion — rendered in AxiTools'
neon palette with GW2 profession colors used as accents and shapes softened.

The redesign covers all three pages cohesively (landing + privacy + terms). Page
content (the product story) is preserved; the visual system is what changes.

## Goals

- Adopt aegis's "use of space and style": big airy sections, soft rounded cards,
  ambient background glows, scroll-reveal motion, a single striking animated hero.
- Keep AxiTools recognizable: neon cyan/magenta/teal, GW2 profession-color accents,
  Orbitron wordmark signature.
- Consolidate today's many small features into ~4 hero feature rows + a compact
  "everything else" grid, so the page breathes instead of listing.
- Legal pages restyled to match so nothing feels orphaned.

## Non-goals

- No change to product/bot behavior or copy meaning (legal text preserved verbatim).
- No build pipeline for the page itself — stays a self-contained HTML file.
- No live stats endpoint; the stats strip is static capability figures.

## Design language

### Palette (hybrid)
Keep AxiTools' deep base and neon trio; borrow aegis's soft ambient-wash technique.

- Base: `--bg:#06070f`; text `--ink:#eaf6ff`; dim `--muted:#8ea2c4`.
- Signature gradient (replaces aegis's blue→pink): **cyan `#46e6ff` → magenta
  `#c764ff` → teal `#39ffd0`** — used in the skewed hero band, the closing band,
  and logo/accent gradients.
- Ambient: three `position:fixed`, blurred radial glows behind all content —
  cyan top-right, magenta mid-left, teal bottom-right (aegis `.ambient` pattern,
  recolored).
- **Profession colors as accents only** — reuse the existing `--p-*` profession
  variables already in the current site (Guardian blue `#0c8fd6`, Warrior gold
  `#c7892b`, Necro green `#3a9d23`, Mesmer `#b46dff`, etc.). Each feature row gets
  a profession-tinted tag row and a colored "pin" badge on its mock card. Not used
  for large fills.

### Shape softening
- Card radius `14px`; buttons/pills `10–12px`.
- Soft deep shadows: `0 40px 90px rgba(0,0,0,.5)` plus a `1px` hairline ring
  `0 0 0 1px rgba(255,255,255,.06)` (aegis card treatment).

### Typography
Google Fonts: **Orbitron** (600–800), **Chakra Petch** (500–700), **Inter**
(400–600), **JetBrains Mono** (400–500).

- **Orbitron** — wordmark + exactly ONE hero accent word only. Nothing else.
- **Chakra Petch** — display font for all `h1/h2/h3` (semi-condensed, techy but
  softer than Orbitron).
- **Inter** — body text.
- **JetBrains Mono** — slash-command lines and embed field text.

### Motion
- Reveal-on-scroll via IntersectionObserver: `.rv` → `.inview` (fade + rise).
- Hero headline/CTA/mock rise-in on load.
- Animated Discord feed loop in the hero (see below).
- Stats strip is static (no count-up, since figures are fixed).
- All motion wrapped in a `@media (prefers-reduced-motion:reduce)` guard that
  disables animations and shows all revealed/animated content statically.

## Landing page structure (`index.html`)

Top to bottom, mirroring aegis's rhythm:

1. **Floating header** — Orbitron wordmark (left); right: "Commands" link, Discord
   icon button, solid **"+ Add to Discord"** invite pill.
2. **Hero band** — skewed cyan→magenta→teal gradient with animated stripes.
   Left column: headline (one Orbitron accent word) + two CTAs
   (primary "+ Add to Discord", secondary "→ See what it does"). Right column:
   animated Discord mock running `/build` (see next section).
3. **Stats strip** — glassy band, 4 static items + a small note pill.
4. **Feature row 1 — Builds, themed to your professions** (the star; profession
   embed mock card, Guardian-blue pin).
5. **Feature row 2 — Squad comps that fill themselves** (flipped layout).
6. **Feature row 3 — Own the WvW week** (live matchup + rosters mock).
7. **Feature row 4 — Runs itself** (patch notes, streaming go-live alerts,
   auto guild-roles, feeds; flipped layout).
8. **"Everything else" grid** — 3 compact cards: API-key management, isolated
   per-guild setup, audit logging/query.
9. **Setup command block** — `/axi setup`-style one-liner (aegis `.cmd` pattern).
10. **Closing band** — recolored signature gradient, big headline + invite CTA.
11. **Footer** — matching links (Commands · Privacy · Terms · Support server ·
    Invite), extensionless internal hrefs.

Each feature row: a headline (Chakra Petch), a short paragraph with bolded key
phrases, a profession-tinted tag row, and a mock card on the other side with a
faint outline "doodle" behind it (aegis `.fmock`/`.mockcard`/`.doodle` pattern).

## Hero mock — the `/build` exchange

A Discord frame: channel sidebar (collapses on mobile) + chat feed. On loop
(reduced-motion → shown statically):

1. A member posts `/build guardian wvw` as a slash-command chip.
2. AxiTools shows a "thinking…" typing indicator.
3. AxiTools replies with a **Guardian-blue themed embed**: build title, a themed
   left color bar, skill/trait rows in JetBrains Mono, a gear line, and a footer
   like "themed to your professions."

Reuses aegis's feed-playback script structure (show/typing/gone classes, timed
sequence, `play()` loop).

## Stats strip (static, editable)

Four items + a note pill:

- **9 professions**, individually themed
- **Multi-guild** — every server fully isolated
- **Encrypted** API-key vault (SQLCipher at rest)
- **One-command** setup

Note pill: **"Your guild's colors, everywhere."**

(Copy is placeholder-quality and may be tuned during implementation; no numbers
depend on a live endpoint.)

## Legal pages (`privacy.html`, `terms.html`)

Rebuilt on the same shell: same floating header, ambient background, Chakra Petch
headings, Inter body, matching footer. Single centered readable column
(`max-width ~760px`). **Existing legal copy preserved verbatim** — only markup and
styling change. Internal links stay extensionless (per recent commits
`79a26e5` / `8b89eab`).

## Technical notes

- Each page is a single self-contained HTML file with one `<style>` block and (for
  the landing) one `<script>` — matching aegis and the current site. No bundler for
  the page.
- Shared design tokens (CSS custom properties) and the header/footer/ambient markup
  are duplicated across the three files (consistent with the current no-shared-CSS
  approach); keep them byte-identical so they stay in sync.
- Deployed via the existing Cloudflare Workers flow (`npm run deploy:site`).
- Assets: reuse `site/assets/` (logo SVG/PNG, profession icons in `assets/prof/`,
  `twitch.png`, `youtube.png`, `favicon.svg`). Audit which profession icons exist
  before referencing them in the hero/feature mocks; fall back to CSS-drawn badges
  if an icon is missing.
- New external dependency: one Google Fonts stylesheet link (Orbitron + Chakra
  Petch + Inter + JetBrains Mono) with `preconnect`.
- End files with a trailing newline (per repo convention, commit `8b89eab`).

## Success criteria

- Landing page reads as spacious and polished in the aegis mold, but unmistakably
  AxiTools (neon palette, profession accents, Orbitron wordmark).
- Hero `/build` mock animates and degrades gracefully under reduced-motion and on
  mobile.
- All four feature rows + the "everything else" grid render responsively
  (single-column under ~960px).
- Privacy and Terms match the new shell with copy unchanged.
- No console errors; passes a basic mobile/desktop visual check in-app.
