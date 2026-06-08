# Streaming Notifications Design

**Date:** 2026-06-07
**Feature:** YouTube & Twitch subscription notifications

## Overview

Add a `/stream` command group that lets guild admins subscribe to YouTube channels and Twitch streamers. When a subscribed YouTube channel posts a new video (or VOD), or a Twitch streamer goes live, the bot posts a rich embed in a configured Discord channel with an optional role ping.

## Data Model

Each guild stores subscriptions in `guild_<guild_id>/stream_subscriptions.json` as a list of `StreamSubscription` dataclasses:

```python
@dataclass
class StreamSubscription:
    name: str                          # user-defined label, e.g. "arenanet"
    platform: str                      # "twitch" or "youtube"
    channel_id: str                    # Twitch login name or YouTube channel ID
    discord_channel_id: int            # where to post notifications
    ping_role_id: Optional[int]        # optional role to ping
    last_vod_id: Optional[str]         # last seen YouTube video ID
    last_live_at: Optional[str]        # ISO8601 timestamp of last seen Twitch live event
    is_live: bool = False              # current Twitch live state (prevents repeat pings)
```

State tracking mirrors the RSS cog: `last_vod_id` for YouTube (same as `last_entry_id`), `is_live` + `last_live_at` for Twitch. On first add, state is primed by fetching current state so no old content is immediately posted.

## Architecture

### New file: `axitools/cogs/streaming.py`

A single cog using one `@tasks.loop(minutes=5)` polling task. The loop iterates every guild's subscriptions and dispatches by platform.

**YouTube polling:**
- Fetches `https://www.youtube.com/feeds/videos.xml?channel_id=<id>` (public, no API key)
- Uses the same XML/feed parsing as the RSS cog
- Compares latest video ID against `last_vod_id`; if new, posts embed and updates stored ID

**Twitch polling:**
- Calls `https://api.twitch.tv/helix/streams?user_login=<login>` with bot-level credentials
- Credentials: `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` env vars
- Auth: OAuth2 app access token fetched via `POST /oauth2/token`, cached in memory
- Token refresh: automatic on 401, tokens last ~60 days
- If response has a stream object and `is_live` was `False` → post embed, set `is_live = True`
- If response is empty and `is_live` was `True` → reset `is_live = False` (no notification, just state cleanup)

### Storage additions

Two new methods on `StorageManager` in `storage.py`:
- `get_stream_subscriptions(guild_id) -> list[StreamSubscription]`
- `save_stream_subscriptions(guild_id, subs: list[StreamSubscription]) -> None`

Follows the exact pattern of `get_rss_feeds` / `save_rss_feeds`.

## Commands

All commands are under the `/stream` app command group, auth-gated via `ensure_authorised`.

| Command | Args | Description |
|---|---|---|
| `/stream add` | `name`, `platform` (twitch/youtube), `channel`, `discord_channel` | Add a subscription. Validates channel exists via test fetch. Primes state to avoid posting old content. |
| `/stream list` | — | Embed listing all guild subscriptions: name, platform, Discord channel, ping role. |
| `/stream remove` | `name` | Remove a subscription by name. |
| `/stream update` | `name`, optional: `discord_channel`, `ping_role` | Update the target Discord channel or ping role. Does not reset seen state. |

**Channel input resolution:**
- YouTube: accepts `youtube.com/channel/UC...` or bare channel ID (`UC...`) — stored directly. `@handle` URLs are not supported on add (the YouTube RSS feed URL requires the `UC...` channel ID, and resolving handles to IDs without the Data API is unreliable). Users should provide the channel ID from the channel's "About" page or URL.
- Twitch: accepts login name (`arenanet`) or full URL (`twitch.tv/arenanet`) — normalized to login name before storing

## Rich Embeds

### Twitch "gone live"
- Color: `#9146FF` (Twitch purple)
- Title: `🔴 <display_name> is live on Twitch!`
- Description: stream title
- Fields: Game/category, viewer count
- Thumbnail: stream thumbnail URL (from Helix response)
- Footer: `Twitch` with Twitch logo icon
- URL: `https://twitch.tv/<login>`
- If ping role set: message content `<@&role_id>` above embed

### YouTube "new video"
- Color: `#FF0000` (YouTube red)
- Title: `📺 <channel_name> posted a new video`
- Description: video title (linked to video URL)
- Fields: Publication time (relative)
- Thumbnail: `https://img.youtube.com/vi/<video_id>/maxresdefault.jpg`
- Footer: `YouTube` with YouTube logo icon
- URL: `https://youtube.com/watch?v=<video_id>`
- If ping role set: message content `<@&role_id>` above embed

## Error Handling

Follows the existing cog pattern — exceptions are caught per-guild and logged; one broken subscription never kills other guilds or subscriptions.

| Scenario | Handling |
|---|---|
| Invalid channel on `/stream add` | Test fetch fails → error response, subscription not saved |
| Twitch 401 | Attempt token refresh once; if still fails, log and skip this poll cycle |
| YouTube feed unavailable | Log and skip, same as RSS cog |
| Discord channel deleted/missing | Log and skip; subscription remains, admin should `/stream remove` |

## Testing

New file: `tests/test_cogs_streaming.py` using `pytest-asyncio` + `aioresponses`.

**YouTube tests:**
- Mocked feed response → correct embed posted, `last_vod_id` updated
- Second poll with same feed → no duplicate post
- Feed unavailable → logged, no crash

**Twitch tests:**
- Mocked Helix response (live) → embed posted, `is_live` set to `True`
- Second poll while still live → no duplicate post
- Mocked response (offline after live) → no embed, `is_live` reset to `False`
- 401 response → token refresh triggered

**Command tests:**
- `/stream add` with bad channel → error response, not saved
- `/stream add` with valid channel → subscription saved, primed correctly
- `/stream update` → updates fields without resetting seen state

**Storage tests** (additions to `test_storage.py`):
- `get_stream_subscriptions` / `save_stream_subscriptions` round-trip

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TWITCH_CLIENT_ID` | Yes (for Twitch) | Twitch Developer app client ID |
| `TWITCH_CLIENT_SECRET` | Yes (for Twitch) | Twitch Developer app client secret |

YouTube requires no additional credentials.
