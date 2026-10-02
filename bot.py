import asyncio
import logging
import os
import ssl
from pathlib import Path

import aiohttp
import certifi
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# macOS python.org builds lack root certs; use certifi's bundle for TLS
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("brizbot")


class BrizBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # enable in the Developer Portal too
        super().__init__(
            command_prefix=os.getenv("COMMAND_PREFIX", "!"),
            intents=intents,
            connector=aiohttp.TCPConnector(ssl=SSL_CONTEXT),
        )

    async def setup_hook(self) -> None:
        for path in sorted(Path(__file__).parent.joinpath("cogs").glob("*.py")):
            if path.stem.startswith("_"):
                continue
            await self.load_extension(f"cogs.{path.stem}")
            log.info("Loaded cog: %s", path.stem)

        # Sync slash commands (to a test guild instantly if GUILD_ID is set)
        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        log.info("Synced slash commands: %s", [c.name for c in synced])

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)


async def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    async with BrizBot() as bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
