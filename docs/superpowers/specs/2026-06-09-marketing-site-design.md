# AxiTools Marketing Site — Design

**Date:** 2026-06-09
**Status:** Approved (visual direction locked via in-app render iteration)

## Goal

A single-page marketing site for AxiTools, the multi-guild Guild Wars 2 Discord
bot. It must showcase all major functionality and visualize each feature with
**fake Discord embeds rendered in HTML/CSS** (no screenshots), so the rendering
stays crisp and editable.

## Direction (locked)

**Holographic "Mists HUD".** A Guild-Wars-2-flavored sci-fantasy aesthetic
(the Mists / asura holo-tech):

- Frosted-glass holo-panels with iridescent conic-gradient borders (cyan → magenta → teal).
- Dark void background with a perspective grid floor and subtle scanline overlay.
- Neon glow on brand, headlines, embed accents.
- GW2 identity carried by the nine profession colors and the WvW RED/BLUE/GREEN world colors.
- Subtle CSS animation (pulsing glow, scanning line, light-sweep glint) — tasteful, not busy.
- **Per-feature accent colors** layered over the cyan/magenta base
  (e.g. WvW = red/blue/green, streaming = Twitch-purple, update notes = amber).

Rejected alternatives explored: dark SaaS + Discord-native; dark Tyrian gold
filigree; light parchment codex; dark codex.

## Delivery

- Single self-contained `docs/index.html` (inline CSS/JS, Google Fonts allowed).
- `docs/.nojekyll` so GitHub Pages serves the file as-is.
- Hosted via GitHub Pages from `/docs` on `main`.

## Sections (single-page scroll)

1. **Hero** — headline, CTA, profession swatch rail, flagship build embed.
2. **Builds** (`/builds`) — build embed with profession color/icon, chat code, forum-thread note.
3. **Compositions** (`/comp`) — squad embed with per-profession fields + live dropdown roster & headcounts.
4. **WvW Alliance** (`/alliance`) — matchup scoreboard embed (RED/BLUE/GREEN) + server-relink announcement.
5. **WvW reset timer** (`/reset`) — countdown embed.
6. **Guild roles** (`/guildroles`) — "role granted" embed tied to GW2 guild membership.
7. **RSS + Game update notes** (`/rss`) — patch-notes embed.
8. **Streaming** (`/streaming`) — YouTube/Twitch go-live alert embed.
9. **Accounts / API keys** (`/account`) — DM-style key management panel.
10. **Per-guild config** (`/config`) — settings panel with isolated storage note.
11. **Command index** — full slash-command reference.
12. **Footer** — Add to Discord CTA + links.

## Non-goals (YAGNI)

- No backend, no build step, no framework.
- No real Discord OAuth / "Add to Discord" wiring (buttons are placeholders/links).
- No multi-page routing.
