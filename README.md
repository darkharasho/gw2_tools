# GW2 Tools Discord Bot

GW2 Tools is a multi-guild Discord bot that helps Guild Wars 2 communities organise and share build information while respecting each server's privacy requirements. The bot is composed of modular cogs so you can easily extend or maintain specific slash-command behaviours.

## Features

- **Per-guild configuration** – `/config` lets server administrators choose which roles can interact with the bot and which channel or forum should receive build posts. Settings can be delivered in a DM or as an ephemeral popup and persist independently for every guild.
- **Build management workflows** – `/builds` supports adding, editing, and deleting Guild Wars 2 builds. Each record stores the profession or elite specialisation, URLs, chat codes, optional descriptions, and audit metadata about who made the latest changes.
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
├── cogs/           # Slash-command implementations (config, builds)
├── constants.py    # Profession/specialisation metadata and icon paths
├── storage.py      # JSON-backed storage with per-guild isolation
└── utils.py        # Utility helpers shared across cogs
media/
└── gw2classicons/  # Profession and elite spec icons used for embeds
```

Persistent data (configurations and builds) will appear under `gw2_tools_bot/data/` after you run the bot locally.

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
