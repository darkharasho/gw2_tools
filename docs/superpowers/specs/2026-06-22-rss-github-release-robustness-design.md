# Robust GitHub Release Announcements (RSS) — Design

**Date:** 2026-06-22
**Status:** Approved (design) — pending spec review
**Author:** darkharasho + Claude

## Problem

The RSS cog (`axitools/cogs/rss.py`) announces GitHub releases by polling
`github.com/{owner}/{repo}/releases.atom` every 10 minutes and deduping by a
single boundary entry ID. This is fragile for GitHub releases whose CI attaches
artifacts and changelogs **after** the release is published:

1. **Premature / empty announcements.** A release is published, then a workflow
   attaches assets and fills in notes. The poller can catch the release in that
   gap and post an embed with an empty/partial body. Because the atom entry ID
   (`tag:github.com,2008:Repository/{id}/{tag}`) is **stable per tag**, the post
   is never corrected — edits do not re-announce.
2. **Re-announce storm risk.** `_resolve_new_entries` stops at a single
   `last_entry_id`. If that ID scrolls off the ~10-entry atom window (several
   releases in a row, or an old release re-published and reordered), the boundary
   is never hit and every entry in the window can be re-announced.

The root insight: **we control our own apps**, so their releases can be made to
surface to the feed only when complete. For repos we don't control, the
**consumer** must be resilient.

## Goals

- Our own axi apps surface only complete releases (artifacts + notes) to the feed.
- The bot never posts an empty/premature GitHub release embed; if a release is
  edited shortly after first appearing, the **same** Discord message is updated
  in place rather than re-posted.
- Eliminate the boundary-dedup re-announce storm.
- No regression for non-GitHub feeds (e.g. the GW2 official news feed).

## Non-goals

- Re-announcing every minor edit to a release indefinitely (only within a grace
  window).
- Switching non-GitHub feeds off feedparser/atom.
- Requiring a GitHub token (it is optional, used only as rate-limit headroom).

---

## Part A — Producer: golden release workflow for our apps

`axipulse` and `axibridge` already use the golden pattern in
`.github/workflows/release.yml`:

```
test → prepare draft release → build & upload artifacts to the DRAFT
     → set notes from RELEASE_NOTES.md (fail loudly if the section is missing)
     → flip --draft=false  (atomic publish)
     → post to Discord webhook
```

Because **draft releases do not appear in `releases.atom`**, the release only
surfaces to the feed once it is fully built and noted. This is the fix for our
own repos — no consumer heuristics needed for them.

### A1. Extract a reusable workflow

Create a callable workflow (e.g. in a shared location or copied per repo) that
encodes the golden pattern, parameterized by:
- build matrix / `electron-builder` args (or "no build" for non-Electron repos),
- app display name, icon URL, and Discord embed colour,
- `RELEASE_NOTES.md` path/format.

Decision: start by **standardizing the file** (same `release.yml` shape across
repos) rather than a `workflow_call` reusable workflow, because the apps differ
in build steps. Revisit `workflow_call` if drift becomes a maintenance problem.

### A2. Roll out to apps missing the pattern

Audit `darkharasho` apps tracked by any guild feed and add/repair `release.yml`:
- **TopStatsAIO** — currently only `test.yml`; publishes immediately → trips the
  feed. Add the golden workflow. **(primary culprit)**
- **gw2_arc_log_uploader** — audit and add if missing.
- Any other darkharasho app in the tracked set (see
  `axitools/data/guild_*/rss_feeds.json`).

Each rollout is independent and verifiable by cutting a test tag and confirming
the release only appears (in atom + the bot) once complete.

### A3. Acceptance (producer)

For a patched app: pushing a `v*` tag results in exactly one release that
appears in `releases.atom` only after artifacts and notes are present. The bot
posts exactly one complete embed.

---

## Part B — Consumer: GitHub-aware, edit-in-place RSS path

For repos we don't control (EI parser, healing-stats, food-reminder, etc.),
harden `rss.py`. Non-GitHub feeds keep the existing feedparser path unchanged.

### B1. Detect GitHub release feeds

A feed is "GitHub release" if its URL matches
`https?://github.com/{owner}/{repo}/releases(\.atom)?$`. Parse out `owner/repo`.
Anything else → existing generic path.

### B2. Hybrid fetch (atom for discovery, API for truth)

- Keep polling the **atom** feed for discovery (cheap, unauthenticated, no rate
  limit). This yields candidate release tags/IDs.
- When a candidate ID is **not already finalized** in our state, fetch that one
  release from the **Releases REST API**:
  `GET /repos/{owner}/{repo}/releases/tags/{tag}` (or list + match), reading
  `draft`, `prerelease`, `assets[]`, `body`, `published_at`, `updated_at`,
  `html_url`, `name`.
- API is called **only for new/unfinalized releases**, not every poll, so the
  unauthenticated 60 req/hr ceiling is realistically sufficient. If
  `AXITOOLS_GITHUB_TOKEN` is set, send it as a bearer token for 5000 req/hr
  headroom. Honor `prerelease` per existing behaviour (announce unless we decide
  to filter — keep current behaviour: announce prereleases too).

### B3. Completeness gate

Only **post** a GitHub release when it looks done:
- `draft == false`, AND
- (`len(assets) >= 1` OR non-empty `body`).

If the gate fails, leave the release as "seen but unposted" and re-evaluate on
the next poll (it will usually complete within a cycle or two).

### B4. Edit-in-place on later changes

Chosen behaviour: **post once, then edit the same message** within a grace
window.

- On first successful post, store the Discord `message_id` and a `content_hash`
  of the announced material (title + body + sorted asset names).
- On a later poll, if the release's `updated_at` is within a **grace window**
  (default **2 hours** from first post) AND the recomputed `content_hash`
  differs, **edit** the stored message with `channel.fetch_message(id).edit(...)`.
- After the grace window, stop tracking the release for edits (finalize it).
- If the stored message was deleted (`discord.NotFound`), do not repost; just
  finalize.

This yields one clean message that fills itself in, with no spam.

### B5. Fix boundary dedup (storm prevention)

Replace single-boundary dedup with a **bounded set of finalized entry IDs** per
feed. An entry is announced only if its ID is not in that set. The set is capped
(e.g. last 50 IDs, FIFO) so it can't grow unbounded and can't be defeated by the
atom window scrolling. The legacy `last_entry_id` is retained for non-GitHub
feeds / migration but the GitHub path keys on the seen-set.

### B6. Storage schema changes

Extend `RssFeedConfig` (in `axitools/storage.py`) with backward-compatible
optional fields (defaults keep old JSON loadable; note `get_rss_feeds` drops a
feed on `TypeError`, so all new fields MUST have defaults):

```python
@dataclass
class RssFeedConfig:
    name: str
    url: str
    channel_id: int
    last_entry_id: Optional[str] = None
    last_entry_published_at: Optional[str] = None
    # New (GitHub-aware path):
    seen_entry_ids: List[str] = field(default_factory=list)  # capped FIFO
    tracked_releases: Dict[str, "TrackedRelease"] = field(default_factory=dict)
```

`TrackedRelease` (new dataclass, all fields serializable to JSON):
```python
@dataclass
class TrackedRelease:
    entry_id: str
    message_id: Optional[int] = None
    content_hash: Optional[str] = None
    first_posted_at: Optional[str] = None  # ISO8601, for grace-window math
    finalized: bool = False
```

`asdict`/`RssFeedConfig(**item)` round-trip must handle the nested dataclass and
the dict-of-dataclass. Use a small (de)serialize helper in storage so old files
(without the new keys) load cleanly and new files round-trip.

### B7. Time source

Grace-window math needs "now". Use `datetime.now(timezone.utc)` inside the cog
(not in pure helpers), keeping the parsing helpers pure and unit-testable by
passing timestamps in.

---

## Data flow (consumer, GitHub feed)

```
poll → fetch atom → candidate IDs
  for each candidate not in seen_entry_ids and not finalized:
     fetch release via REST API
     if not complete (gate B3): mark seen-but-unposted, skip
     if complete and no tracked message: post embed, store TrackedRelease + message_id + hash, add to seen
     if complete and tracked + within grace window + hash changed: edit message, update hash
     if past grace window: finalized = true
  persist feed state
```

## Error handling

- API fetch failure (network, 404, rate-limited 403): log, leave release
  unposted/untracked, retry next poll. Never crash the poll loop (existing
  per-feed try/except stays).
- Rate-limit (403 with `X-RateLimit-Remaining: 0`): back off that host for the
  remainder of the cycle; log once.
- Discord post failure: existing behaviour (log, don't advance state).
- Discord edit on deleted message (`NotFound`): finalize, don't repost.
- Malformed/partial API JSON: treat as "not complete", skip.

## Testing

- **Pure helpers (unit):** GitHub-feed URL detection; completeness gate over
  fixtures (draft, no-assets+empty-body, complete, prerelease); content-hash
  stability; seen-set FIFO cap; grace-window decision given injected timestamps.
- **Storage round-trip:** old JSON (no new keys) loads with defaults; new JSON
  with `tracked_releases`/`seen_entry_ids` round-trips through
  `save`/`get_rss_feeds`.
- **Consumer flow (with stubbed API + Discord):** premature release → no post;
  becomes complete → one post; later edit within window → message edited, not
  reposted; edit after window → ignored; boundary scroll-off → no storm.
- **Regression:** non-GitHub feed (GW2 news) still posts via the generic path.
- Run vitest/pytest at ≤2 workers per machine limits. (This repo is Python →
  pytest; honor parallelism limits via `-n 2` if xdist is configured.)

## Rollout / sequencing

1. Consumer hardening (Part B) — fixes all repos, including third-party, first.
2. Producer golden workflow rollout (Part A) — eliminates the problem at the
   source for our apps; can proceed in parallel, per-repo.

## Open items / future

- Optional `workflow_call` reusable workflow if per-repo drift becomes painful.
- Optional prerelease filtering toggle per feed (out of scope now).
