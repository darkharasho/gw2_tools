# Streaming Notifications Design

**Date:** 2026-06-07
**Feature:** YouTube & Twitch subscription notifications

## Overview

Add a `/stream` command group that lets guild admins subscribe to YouTube channels and Twitch streamers. When a subscribed YouTube channel goes live or posts a new video/VOD, or a Twitch streamer goes live, the bot posts a rich embed in a configured Discord channel with an optional role ping.

## Data Model

Each guild stores subscriptions in `guild_<guild_id>/stream_subscriptions.json` as a list of `StreamSubscription` dataclasses:

```python
@dataclass
class StreamSubscription:
    name: str                          # user-defined label, e.g. "arenanet"
    platform: str                      # "twitch" or "youtube"
    channel_id: str                    # Twitch login name or YouTube channel ID (UC...)
    discord_channel_id: int            # where to post notifications
    ping_role_id: Optional[int]        # optional role to ping
    last_vod_id: Optional[str]         # last seen YouTube video ID (tracks both videos and live streams)
    last_live_at: Optional[str]        # ISO8601 timestamp of last seen Twitch live event
    is_live: bool = False              # current live state for both platforms (prevents repeat pings)
```

`is_live` is used for both platforms: Twitch (set when stream starts, cleared when it ends) and YouTube (set when a live broadcast is detected, cleared when it ends). `last_vod_id` tracks the last RSS entry ID for YouTube, preventing re-notification of the same event on restart.

On first `/stream add`, state is primed by fetching current state so no old content is posted immediately.

## Architecture

### New file: `axitools/cogs/streaming.py`

A single cog using one `@tasks.loop(minutes=5)` polling task. The loop iterates every guild's subscriptions and dispatches by platform.

**YouTube polling (RSS-first, API for classification):**
1. Fetch `https://www.youtube.com/feeds/videos.xml?channel_id=<id>` (no quota cost)
2. Compare latest video ID against `last_vod_id`; if unchanged, skip
3. If new entry detected, call `videos.list?part=snippet,liveStreamingDetails&id=<video_id>` (1 quota unit) to classify it:
   - `liveStreamingDetails.actualStartTime` present + `liveBroadcastContent == "live"` → post "gone live" embed, set `is_live = True`
   - `liveStreamingDetails` present but stream ended → post "new VOD" embed (if not already notified), set `is_live = False`
   - No `liveStreamingDetails` → post "new video" embed
4. Update `last_vod_id` and `is_live` in storage

This approach costs ~1 quota unit per new video/stream, making the 10k daily limit a non-issue.

**Twitch polling:**
- Calls `https://api.twitch.tv/helix/streams?user_login=<login>` with bot-level credentials
- Credentials: `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` env vars
- Auth: OAuth2 app access token fetched via `POST /oauth2/token`, cached in memory
- Token refresh: automatic on 401, tokens last ~60 days
- If response has a stream object and `is_live` was `False` → post embed, set `is_live = True`
- If response is empty and `is_live` was `True` → reset `is_live = False` (no notification, just state cleanup)

**YouTube channel resolution (on `/stream add`):**
- Accepts `youtube.com/@handle`, `youtube.com/channel/UC...`, or bare channel ID
- `@handle` URLs resolved via `channels.list?part=id&forHandle=<handle>` (1 quota unit, once on add)
- Canonical `UC...` channel ID stored in `channel_id`

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
- YouTube: accepts `youtube.com/@handle`, `youtube.com/channel/UC...`, or bare channel ID — always stored as canonical `UC...` ID. Handle resolution uses YouTube Data API (1 quota unit, on add only).
- Twitch: accepts login name (`arenanet`) or full URL (`twitch.tv/arenanet`) — normalized to login name before storing.

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

### YouTube "gone live"
- Color: `#FF0000` (YouTube red)
- Title: `🔴 <channel_name> is live on YouTube!`
- Description: stream title
- Fields: Start time (relative)
- Thumbnail: `https://img.youtube.com/vi/<video_id>/maxresdefault.jpg`
- Footer: `YouTube` with YouTube logo icon
- URL: `https://youtube.com/watch?v=<video_id>`
- If ping role set: message content `<@&role_id>` above embed

### YouTube "new video / VOD"
- Color: `#FF0000` (YouTube red)
- Title: `📺 <channel_name> posted a new video` (or `posted a new VOD` for ended streams)
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
| YouTube handle not found | `channels.list` returns empty → error response, subscription not saved |
| Twitch 401 | Attempt token refresh once; if still fails, log and skip this poll cycle |
| YouTube API quota exceeded | Log warning and skip YouTube classification step; RSS entry still marked as seen |
| YouTube feed unavailable | Log and skip, same as RSS cog |
| Discord channel deleted/missing | Log and skip; subscription remains, admin should `/stream remove` |

## Testing

New file: `tests/test_cogs_streaming.py` using `pytest-asyncio` + `aioresponses`.

**YouTube tests:**
- Mocked RSS feed (new regular video) + mocked `videos.list` (no liveStreamingDetails) → "new video" embed posted, `last_vod_id` updated
- Mocked RSS feed (new entry) + mocked `videos.list` (liveBroadcastContent=live) → "gone live" embed posted, `is_live = True`
- Mocked RSS feed (same entry, `is_live = True`) → no duplicate embed; `videos.list` not called
- Feed unavailable → logged, no crash

**Twitch tests:**
- Mocked Helix response (live) → embed posted, `is_live` set to `True`
- Second poll while still live → no duplicate post
- Mocked response (offline after live) → no embed, `is_live` reset to `False`
- 401 response → token refresh triggered

**Command tests:**
- `/stream add` YouTube with `@handle` → resolves to channel ID, subscription saved
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
| `YOUTUBE_API_KEY` | Yes (for YouTube) | YouTube Data API v3 key (for handle resolution and video classification) |
