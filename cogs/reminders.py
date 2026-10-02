import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from remind import (
    MAX_MESSAGE_LEN,
    MAX_PER_USER,
    Reminder,
    ReminderStore,
    parse_duration,
    validate_seconds,
)

import gpt

log = logging.getLogger("brizbot.reminders")


class ConfirmView(discord.ui.View):
    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Save", style=discord.ButtonStyle.green)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()

NO_PINGS_BUT_USER = discord.AllowedMentions(users=True, everyone=False, roles=False)


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store: ReminderStore | None = None

    async def cog_load(self) -> None:
        self.store = await ReminderStore.open()
        self.check_due.start()

    async def cog_unload(self) -> None:
        self.check_due.cancel()
        if self.store:
            await self.store.close()

    @app_commands.command(name="remind", description="Set a reminder")
    @app_commands.describe(
        when="e.g. 10m, 2h30m, 1d (or plain English if you've run /setkey)",
        message="What to remind you about",
    )
    async def remind(
        self,
        interaction: discord.Interaction,
        when: str,
        message: app_commands.Range[str, 1, MAX_MESSAGE_LEN],
    ) -> None:
        seconds = parse_duration(when)
        if not validate_seconds(seconds):
            if await self._remind_natural(interaction, f"{when} {message}"):
                return
            await interaction.response.send_message(
                "I couldn't read that time. Try something like `10m`, `2h30m`, or `1d` "
                "(between 30 seconds and 1 year).",
                ephemeral=True,
            )
            return

        due_at = await self.store.add(
            interaction.user.id, interaction.channel_id, message, seconds
        )
        if due_at is None:
            await interaction.response.send_message(
                f"You already have {MAX_PER_USER} reminders pending.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Got it, I'll remind you <t:{due_at}:R>.", ephemeral=True
        )

    async def _remind_natural(self, interaction: discord.Interaction, text: str) -> bool:
        """Try the model for free-text times. Returns True if the interaction was handled."""
        api_key = await gpt.get_key(interaction.user.id)
        if api_key is None:
            return False
        await interaction.response.defer(ephemeral=True)
        parsed = await gpt.parse_reminder(text, api_key)
        if parsed is None:
            await interaction.followup.send(
                "I couldn't understand that time. Try `10m`, `2h30m`, or `1d`.",
                ephemeral=True,
            )
            return True
        task, due = parsed.task, parsed.due_at

        view = ConfirmView(interaction.user.id)
        msg = await interaction.followup.send(
            f"Remind you <t:{due}:F> (<t:{due}:R>): {task}?",
            view=view, ephemeral=True, wait=True,
        )
        await view.wait()
        if not view.confirmed:
            await msg.edit(content="Cancelled.", view=None)
            return True
        # Recompute: the confirm click may have taken a while.
        seconds = max(int(due - time.time()), 30)
        saved = await self.store.add(
            interaction.user.id, interaction.channel_id, task, seconds
        )
        await msg.edit(
            content=(
                f"Got it, I'll remind you <t:{saved}:R>."
                if saved
                else f"You already have {MAX_PER_USER} reminders pending."
            ),
            view=None,
        )
        return True

    @app_commands.command(name="reminders", description="List your pending reminders")
    async def reminders(self, interaction: discord.Interaction) -> None:
        items = await self.store.list_for_user(interaction.user.id)
        if not items:
            await interaction.response.send_message(
                "You have no pending reminders.", ephemeral=True
            )
            return
        lines = [f"`{r.id}` <t:{r.due_at}:R>: {r.message[:80]}" for r in items]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="unremind", description="Delete one of your reminders")
    @app_commands.describe(id="The reminder ID shown by /reminders")
    async def unremind(self, interaction: discord.Interaction, id: int) -> None:
        removed = await self.store.delete_for_user(interaction.user.id, id)
        await interaction.response.send_message(
            "Reminder deleted." if removed else "No reminder with that ID is yours.",
            ephemeral=True,
        )

    @tasks.loop(seconds=30)
    async def check_due(self) -> None:
        for reminder in await self.store.due():
            try:
                await self._deliver(reminder)
            except Exception:
                # Keep the row so it retries next tick; don't kill the loop.
                log.exception("Failed delivering reminder %s", reminder.id)
                continue
            await self.store.delete(reminder.id)

    @check_due.before_loop
    async def before_check_due(self) -> None:
        await self.bot.wait_until_ready()

    @check_due.error
    async def check_due_error(self, error: BaseException) -> None:
        log.exception("check_due crashed, restarting", exc_info=error)
        if not self.check_due.is_running():
            self.check_due.restart()

    async def _deliver(self, r: Reminder) -> None:
        """Post in the original channel, falling back to a DM.

        Returns normally when delivered or when the user can't be reached at all
        (DMs closed), so such reminders are dropped rather than retried forever.
        """
        channel = self.bot.get_channel(r.channel_id)
        if channel is not None:
            try:
                await channel.send(
                    f"<@{r.user_id}> reminder: {r.message}",
                    allowed_mentions=NO_PINGS_BUT_USER,
                )
                return
            except discord.Forbidden:
                pass  # lost permission; fall back to DM
            except discord.NotFound:
                pass  # channel deleted
        try:
            user = await self.bot.fetch_user(r.user_id)
            await user.send(f"Reminder: {r.message}")
        except (discord.Forbidden, discord.NotFound):
            log.info("Dropping reminder %s: user unreachable", r.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reminders(bot))
