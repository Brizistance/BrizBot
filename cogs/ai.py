import discord
from discord import app_commands
from discord.ext import commands

import config
import gpt

NO_PINGS = discord.AllowedMentions.none()

ACCENT = discord.Color.from_rgb(116, 170, 156)  # OpenAI-ish teal
ERROR = discord.Color.from_rgb(237, 66, 69)
ERRORS = {gpt.MSG_BAD_KEY, gpt.MSG_NO_CREDIT, gpt.MSG_FAILED, gpt.MSG_EMPTY}
EMBED_BODY_LIMIT = 3500  # embed descriptions allow 4096; leave room for the header


def build_embeds(
    user: discord.abc.User, title: str, header: str, text: str
) -> list[discord.Embed]:
    """Embed(s) with `header` (e.g. the quoted question) above the answer.

    Long answers continue in extra embeds; only the first carries the header.
    """
    color = ERROR if text in ERRORS else ACCENT
    parts = gpt.chunk(text, EMBED_BODY_LIMIT)
    embeds = []
    for i, part in enumerate(parts):
        body = f"{header}\n\n{part}" if i == 0 and header else part
        embed = discord.Embed(description=body, color=color)
        if i == 0:
            embed.set_author(name=title, icon_url=user.display_avatar.url)
        embed.set_footer(
            text=f"{config.LLM_MODEL}" + (f" • {i + 1}/{len(parts)}" if len(parts) > 1 else "")
        )
        if i == len(parts) - 1:
            embed.timestamp = discord.utils.utcnow()
        embeds.append(embed)
    return embeds


async def require_key(interaction: discord.Interaction) -> str | None:
    """Returns the user's key, or tells them to set one and returns None."""
    key = await gpt.get_key(interaction.user.id)
    if key is None:
        await interaction.response.send_message(
            "Run `/setkey` first to add your OpenAI API key.", ephemeral=True
        )
    return key


class AI(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _send(
        self, interaction: discord.Interaction, title: str, header: str, text: str
    ) -> None:
        for embed in build_embeds(interaction.user, title, header, text):
            await interaction.followup.send(embed=embed, allowed_mentions=NO_PINGS)

    @app_commands.command(name="ask", description="Ask the AI a question")
    @app_commands.checks.cooldown(1, config.COOLDOWN_SECONDS)
    async def ask(self, interaction: discord.Interaction, question: str) -> None:
        api_key = await require_key(interaction)
        if api_key is None:
            return
        if len(question) > config.MAX_QUESTION_CHARS:
            await interaction.response.send_message("Question is too long.", ephemeral=True)
            return
        await interaction.response.defer()  # Discord's 3s limit
        text = await gpt.safe_complete(question, api_key, tag="ask")
        quoted = "\n".join(f"> {line}" for line in question.splitlines())
        await self._send(
            interaction,
            f"{interaction.user.display_name} asked",
            f"**Question**\n{quoted}\n\n**Answer**",
            text,
        )

    @app_commands.command(name="tldr", description="Summarize recent messages in this channel")
    @app_commands.describe(count="How many recent messages to summarize")
    @app_commands.checks.cooldown(1, config.COOLDOWN_SECONDS)
    async def tldr(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, config.TLDR_MESSAGE_LIMIT] = config.TLDR_MESSAGE_LIMIT,
    ) -> None:
        api_key = await require_key(interaction)
        if api_key is None:
            return
        channel = interaction.channel
        if channel is None or not hasattr(channel, "history"):
            await interaction.response.send_message(
                "I can't read history here.", ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            msgs = [
                m async for m in channel.history(limit=count)
                if not m.author.bot and m.content
            ]
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to read this channel.")
            return
        transcript = gpt.build_transcript(
            [(m.author.display_name, m.clean_content) for m in reversed(msgs)]
        )
        if not transcript:
            await interaction.followup.send("Nothing to summarize.")
            return
        text = await gpt.safe_complete(transcript, api_key, gpt.TLDR_INSTRUCTIONS, "tldr")
        await self._send(
            interaction,
            "Channel recap",
            f"**TL;DR** of the last {len(msgs)} message{'s' if len(msgs) != 1 else ''} "
            f"in {interaction.channel.mention}\n",
            text,
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            msg = f"Slow down. Try again in {error.retry_after:.0f}s."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AI(bot))
