# `!remind` command sketch

Assumes Python with discord.py 2.x and SQLite. The design carries over to other stacks; only the code skeleton changes.

## Goal

A user runs `!remind when:2h30m message:push the PR` and the bot pings them with that message when the time is up, even if the bot restarted in between.

## Command spec

| Command | Arguments | Behavior |
| --- | --- | --- |
| `!remind` | `when` (string), `message` (string, 1-500 chars) | Stores a reminder, replies with a confirmation |
| `!reminders` (later) | none | Lists the caller's pending reminders |
| `!unremind` (later) | `id` | Deletes one of the caller's reminders |

`when` accepts relative durations built from `s`, `m`, `h`, `d`, `w`: `10m`, `2h30m`, `1d`. Absolute times ("tomorrow 9am") need per-user time zones, so leave them for a later version.

## Design decisions

1. **Slash command, not a prefix command.** Discord validates arguments for you and no message content intent is needed.
2. **Persist to SQLite.** In-memory timers (`asyncio.sleep`) lose every reminder on restart. A table survives restarts and deploys.
3. **Poll for due reminders every 30 seconds.** One background loop queries for rows where `due_at <= now`. Simpler than one timer per reminder, and 30 seconds of slack is fine for reminders.
4. **Store UTC Unix timestamps.** No time zone math in the bot. Discord's `<t:UNIX:R>` markup renders the time in each user's local zone.
5. **Deliver in the channel where it was set**, falling back to a DM if the channel is gone or the bot can't post there.

## Data model

```sql
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message    TEXT    NOT NULL,
    due_at     INTEGER NOT NULL   -- Unix seconds, UTC
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (due_at);
```

## Flow

1. User runs `!remind`.
2. Bot parses `when` into seconds. Invalid or out of range: ephemeral error, stop.
3. Bot inserts the row and replies "I'll remind you <relative time>".
4. Background loop wakes every 30 seconds, selects due rows.
5. For each row: send the ping, then delete the row.

## Code skeleton

`cogs!reminders.py`

```python
import re
import time

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

DURATION_RE = re.compile(r"(\d+)\s*([smhdw])")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
MIN_SECONDS = 30
MAX_SECONDS = 365 * 86400
MAX_PER_USER = 25


def parse_duration(text: str) -> int | None:
    """'2h30m' -> 9000. Returns None if anything in the string isn't a duration."""
    text = text.strip().lower()
    matches = DURATION_RE.findall(text)
    if not matches or DURATION_RE.sub("", text).strip():
        return None
    return sum(int(n) * UNITS[u] for n, u in matches)


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot, db: aiosqlite.Connection):
        self.bot = bot
        self.db = db

    async def cog_load(self):
        self.check_due.start()

    async def cog_unload(self):
        self.check_due.cancel()

    @app_commands.command(name="remind", description="Set a reminder")
    @app_commands.describe(when="e.g. 10m, 2h30m, 1d", message="What to remind you about")
    async def remind(
        self,
        interaction: discord.Interaction,
        when: str,
        message: app_commands.Range[str, 1, 500],
    ):
        seconds = parse_duration(when)
        if seconds is None or not (MIN_SECONDS <= seconds <= MAX_SECONDS):
            await interaction.response.send_message(
                "I couldn't read that time. Try something like `10m`, `2h30m`, or `1d`.",
                ephemeral=True,
            )
            return

        async with self.db.execute(
            "SELECT COUNT(*) FROM reminders WHERE user_id = ?", (interaction.user.id,)
        ) as cur:
            (count,) = await cur.fetchone()
        if count >= MAX_PER_USER:
            await interaction.response.send_message(
                f"You already have {MAX_PER_USER} reminders pending.", ephemeral=True
            )
            return

        due_at = int(time.time()) + seconds
        await self.db.execute(
            "INSERT INTO reminders (user_id, channel_id, message, due_at) VALUES (?, ?, ?, ?)",
            (interaction.user.id, interaction.channel_id, message, due_at),
        )
        await self.db.commit()
        await interaction.response.send_message(
            f"Got it, I'll remind you <t:{due_at}:R>.", ephemeral=True
        )

    @tasks.loop(seconds=30)
    async def check_due(self):
        now = int(time.time())
        async with self.db.execute(
            "SELECT id, user_id, channel_id, message FROM reminders WHERE due_at <= ?",
            (now,),
        ) as cur:
            rows = await cur.fetchall()

        for rid, user_id, channel_id, message in rows:
            await self._deliver(user_id, channel_id, message)
            await self.db.execute("DELETE FROM reminders WHERE id = ?", (rid,))
        await self.db.commit()

    @check_due.before_loop
    async def before_check_due(self):
        await self.bot.wait_until_ready()

    async def _deliver(self, user_id: int, channel_id: int, message: str):
        text = f"<@{user_id}> reminder: {message}"
        mentions = discord.AllowedMentions(users=True, everyone=False, roles=False)
        channel = self.bot.get_channel(channel_id)
        try:
            if channel is not None:
                await channel.send(text, allowed_mentions=mentions)
                return
        except discord.HTTPException:
            pass
        try:  # fallback: DM
            user = await self.bot.fetch_user(user_id)
            await user.send(f"Reminder: {message}")
        except discord.HTTPException:
            pass  # user has DMs closed; drop it
```

Wiring it up in the bot's `setup_hook`:

```python
async def setup_hook(self):
    self.db = await aiosqlite.connect("bot.db")
    await self.db.executescript(SCHEMA)          # the SQL above
    await self.add_cog(Reminders(self, self.db))
    await self.tree.sync()                       # sync to one guild while developing; global sync is slow
```

## Edge cases

- **Mention abuse.** A reminder message containing `@everyone` would ping the server when the bot repeats it. The `AllowedMentions` in `_deliver` blocks that; keep it.
- **Bot was offline at the due time.** The `due_at <= now` query picks up overdue rows on the next loop, so late reminders still fire.
- **Channel deleted or permissions lost.** Handled by the DM fallback.
- **Spam.** Per-user cap (`MAX_PER_USER`) and a minimum duration.
- **Garbage input.** `parse_duration` rejects strings with leftover characters, so `10 minutes-ish` errors instead of silently becoming 10m.
- **Loop crash.** An unhandled exception stops a `tasks.loop` for good. Catch broadly inside the per-row work, or add an `@check_due.error` handler that logs and restarts.

## Test checklist

- [ ] `parse_duration`: `10m`, `2h30m`, `1d`, `1w2d`, empty string, `abc`, `10x`, `0s`
- [ ] Set a 30s reminder, confirm it fires in the same channel
- [ ] Set a reminder, restart the bot, confirm it still fires
- [ ] Set a reminder, stop the bot past the due time, start it, confirm it fires late
- [ ] Message containing `@everyone` does not ping everyone
- [ ] 26th reminder is rejected

## Later

- `!reminders` and `!unremind`
- Absolute times with a per-user time zone setting
- Recurring reminders (`every 1d`)
- Snooze button on the reminder message
