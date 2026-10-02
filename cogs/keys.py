import discord
from discord import app_commands
from discord.ext import commands

import gpt


class KeyModal(discord.ui.Modal, title="Set your OpenAI API key"):
    key = discord.ui.TextInput(
        label="API key", placeholder="sk-...", min_length=20, max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        api_key = self.key.value.strip()
        try:
            valid = await gpt.validate_key(api_key)
        except Exception:
            await interaction.followup.send(
                "Couldn't reach OpenAI to check that key. Try again later.", ephemeral=True
            )
            return
        if not valid:
            await interaction.followup.send(
                "That key didn't work. Check it and try again.", ephemeral=True
            )
            return
        await gpt.set_key(interaction.user.id, api_key)
        await interaction.followup.send(
            "Key saved. AI commands are unlocked, billed to your OpenAI account.\n"
            "Note: the bot operator's server stores your key encrypted. Use a dedicated key "
            "with a low spend limit, and revoke it any time on OpenAI's dashboard or with "
            "`/removekey`.",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # Default handler logs the traceback, which could include the submitted key.
        msg = "Something went wrong saving your key."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


class Keys(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="setkey", description="Add your OpenAI API key")
    async def setkey(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(KeyModal())

    @app_commands.command(name="removekey", description="Delete your stored API key")
    async def removekey(self, interaction: discord.Interaction) -> None:
        removed = await gpt.delete_key(interaction.user.id)
        msg = "Key deleted." if removed else "You don't have a key saved."
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="keystatus", description="Check if you have a key saved")
    async def keystatus(self, interaction: discord.Interaction) -> None:
        key = await gpt.get_key(interaction.user.id)
        msg = f"Key saved, ending in `{key[-4:]}`." if key else "No key saved. Run `/setkey`."
        await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await gpt.init_db()
    await bot.add_cog(Keys(bot))
