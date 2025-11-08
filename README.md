# GW2 Tools Discord Bot

GW2 Tools is a multi-guild Discord bot that helps Guild Wars 2 communities organise and share build information while respecting each server's privacy requirements. The bot is composed of modular cogs so you can easily extend or maintain specific slash-command behaviours.

## Features

- **Per-guild configuration** – `/config` lets server administrators choose which roles can interact with the bot and which channel or forum should receive build posts. Settings can be delivered in a DM or as an ephemeral popup and persist independently for every guild.
- **Build management workflows** – `/builds` supports adding, editing, and deleting Guild Wars 2 builds. Each record stores the profession or elite specialisation, URLs, chat codes, optional descriptions, and audit metadata about who made the latest changes.
- **RSS announcements** – `/rss set` subscribes the guild to an RSS or Atom feed and posts new entries into the channel you specify. `/rss list` shows paginated embeds of the configured feeds, and `/rss delete` opens a dropdown so you can pick which subscription to remove without touching other guilds.
- **Scheduled compositions** – `/comp manage` lets moderators schedule recurring composition posts, choose the destination channel, maintain dropdown signups that keep headcounts per profession in sync with reactions, and swap between saved presets for different activities.
- **Rich embeds and forum integration** – Build posts automatically use the profession's colour palette and icon from `media/gw2classicons`, include the chat code in a code block, and update or create forum threads when the configured channel is a forum.
- **Isolated storage** – Configuration and build data are written to `gw2_tools_bot/data/guild_<guild_id>/` so that each Discord server's information is kept separate.

## Development setup

1. **Install Python** – Use Python 3.10 or later.
2. **Create a virtual environment (recommended):**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows use `.venv\\Scripts\\activate`
   ```
3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
4. **Configure environment variables:**
   - Duplicate `.env.example` to `.env` and set the values inside:
     ```bash
     cp .env.example .env
     # Edit .env and add your real token
     ```
   - `DISCORD_TOKEN` – Your Discord bot token. You can create one through the [Discord Developer Portal](https://discord.com/developers/applications).

## Running the bot locally

1. Ensure the virtual environment is activated and `DISCORD_TOKEN` is set (either exported in your shell or defined in `.env`).
2. Start the bot:
   ```bash
   python -m gw2_tools_bot
   ```
3. Invite the bot to your Discord server with the necessary permissions (application commands, manage messages/threads for forum posting, etc.). Once guild commands finish syncing, use `/config` to set moderator roles and the build posting channel.

## Project structure

```
gw2_tools_bot/
├── bot.py          # Bot bootstrapper and shared helpers
├── cogs/           # Slash-command implementations (config, builds, RSS, ArcDPS)
├── constants.py    # Profession/specialisation metadata and icon paths
├── storage.py      # JSON-backed storage with per-guild isolation
└── utils.py        # Utility helpers shared across cogs
media/
└── gw2classicons/  # Profession and elite spec icons used for embeds
```

Persistent data (configurations and builds) will appear under `gw2_tools_bot/data/` after you run the bot locally.

## Managing RSS feed subscriptions

Guild moderators can use the `/rss` command group to mirror updates from community news sites, patch notes, or other feeds into a Discord channel:

1. Run `/rss set` with a unique name, the feed URL, and the channel where updates should be posted. The bot validates the feed and stores the most recent entry so it does not repost historical content.
2. Use `/rss list` to review the configured feeds for the current guild. Each subscription is isolated, so feeds configured in one server never appear in another.
3. Run `/rss test` in non-production environments to open a dropdown of configured feeds and post the most recent entry into the feed's channel when you want to verify permissions or preview the announcement formatting.
4. Remove a subscription with `/rss delete`, which opens a dropdown of the configured feeds so you can select the one to delete. The bot stops polling and deletes the stored metadata for that feed.

The RSS poller wakes up every 10 minutes. When it finds new entries it posts a rich embed containing the headline, summary, publication time, and link to the original article.

## Scheduling guild compositions

Guild moderators can organise strike, raid, or WvW squad signups through the `/comp` command group. The workflow is designed so admins can configure everything in a single flow and players can register themselves without additional permissions.

1. Run `/comp manage` to open the configuration view. Pick the channel where future posts should appear, set the day or comma-separated days and time the signup should be published, and choose whether the schedule repeats weekly or is a one-off announcement. The timezone field accepts full IANA names such as `America/Los_Angeles` as well as common abbreviations like `PST` or `EST`.
2. Click **Edit composition** to open the class editor modal. Add each profession or elite specialisation you want in the squad, optionally specifying the number of slots available. Class icons are sourced from `media/gw2wikiicons`, and the tool builds a grid of icons for the embed automatically.
3. Use **Post preview** to review the generated embed before saving. When you confirm the settings the bot stores them in the guild configuration and schedules the next post.
4. When the scheduled time arrives the bot publishes an embed containing each configured class as an inline field. A dropdown menu appears under the message so members can select the class they want to play; the bot tracks selections, prevents users from taking multiple slots, and updates the counts instantly.
5. Save lineups you want to reuse with **Save as preset**. Presets capture the configured classes, schedule, and overview without the live signup state, letting you prepare different rosters for strikes, raids, or WvW.
6. Switch between stored presets from the dropdown at the top of the management view. Loading a preset immediately updates the guild configuration and marks it as the active preset so the next scheduled post uses the selected lineup.
7. If you need to adjust the roster or schedule, rerun `/comp manage` to edit the configuration. Existing posts will update when members change their dropdown choice, and the next scheduled announcement will use your latest settings. Updating the configuration clears the active preset so you can resave it when you're happy with the changes.

Presets can be renamed, overwritten, or deleted through the same management view, and each guild's presets are stored separately under `gw2_tools_bot/data/guild_<guild_id>/comp_presets.json` so servers only see their own rosters.

## Running the bot with PM2

If you want the bot to stay online after reboots, PM2 can run the same module entry point that you use in development. Two key tips:

* Point PM2 at the actual Python binary (e.g. the virtualenv's `bin/python`). Tools such as pyenv create shims under `~/.pyenv/shims` that are shell scripts; PM2 will try to parse them as JavaScript unless you bypass the shim with `pyenv which python`.
* Pass `--interpreter none` so PM2 executes the binary directly instead of wrapping it in Node.js.

From the repository root, the following command starts the bot under PM2 with your virtualenv interpreter:

```bash
pm2 start $(pyenv which python) \
  --name gw2-tools-bot \
  --cwd $(pwd) \
  --interpreter none \
  -- -m gw2_tools_bot
```

The command after `--` is forwarded to Python, so the module executes exactly as `python -m gw2_tools_bot` would. Once you verify the process stays online you can run `pm2 save` so it restarts on boot.

If you prefer an ecosystem configuration file, copy `ecosystem.config.js.example`, update the `script`, `cwd`, and environment variables, and then start it with `pm2 start ecosystem.config.js`.
