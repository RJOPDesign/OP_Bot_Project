"""
Main entry for OP Bot — menu-only moderation suite.

Loads cogs, wires Mongo (optional), registers persistent views, and ensures a
main control panel is posted in each guild where the bot can speak.

Environment variables (.env):
- DISCORD_BOT_TOKEN: required
- MONGO_URI: optional (enables persistence for configs, tickets, giveaways)
- BOT_PREFIX: optional (unused for menus but required by commands.Bot)
- LOG_LEVEL: optional (DEBUG/INFO/WARNING/ERROR) — default INFO
"""
from __future__ import annotations

import os
import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv

try:
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
except Exception:  # motor optional
    AsyncIOMotorClient = None  # type: ignore

# ----------------- Logging -----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("opbot")

# ----------------- Intents -----------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = False  # keep disabled unless absolutely needed
intents.reactions = True


class OPBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix=os.getenv("BOT_PREFIX", "!"), intents=intents)
        self.db = None  # type: ignore
        self.mongo_client: Optional[AsyncIOMotorClient] = None  # type: ignore
        self._start_time = None
        self.initial_extensions = [
            "cogs.menu_cog",
            "cogs.moderation_cog",
            "cogs.logging_cog",
            "cogs.ticket_cog",
            "cogs.essentials_cog",
            "cogs.giveaways_cog",
            "cogs.purge_cog",
            "cogs.music_cog",
        ]

    async def setup_hook(self) -> None:
        # Start time for uptime
        import time as _time
        self._start_time = _time.time()

        # Wire DB if available
        mongo_uri = os.getenv("MONGO_URI")
        if mongo_uri and AsyncIOMotorClient is not None:
            try:
                self.mongo_client = AsyncIOMotorClient(mongo_uri)
                self.db = self.mongo_client["opbot"]
                logger.info("Mongo connected (opbot DB).")
            except Exception:
                logger.exception("Failed to connect to Mongo; continuing without persistence.")
        elif mongo_uri and AsyncIOMotorClient is None:
            logger.warning("MONGO_URI set but 'motor' not installed. Add 'motor' to requirements.txt.")

        # Load extensions
        for ext in self.initial_extensions:
            try:
                await self.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception:
                logger.exception(f"Failed to load extension: {ext}")

        # Register persistent views proactively (cogs also register theirs)
        try:
            from cogs.menu_cog import MainMenuView
            self.add_view(MainMenuView(self))
        except Exception:
            pass

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user and self.user.id})")
        await self.ensure_guild_panels()

    # ----------------- Panel helpers -----------------
    async def ensure_guild_panels(self):
        """Post/refresh a single control panel message per guild in a sensible channel."""
        from utils import ensure_guild_config
        from cogs.menu_cog import build_main_embed, MainMenuView

        for guild in list(self.guilds):
            try:
                await ensure_guild_config(self, guild.id)
            except Exception:
                logger.exception("ensure_guild_config failed")

            channel = self._pick_announce_channel(guild)
            if not channel:
                logger.warning(f"No writable channel found for guild {guild.id}")
                continue

            try:
                # Try to find an existing panel in recent history and refresh it
                found = None
                async for msg in channel.history(limit=100):
                    if msg.author.id == self.user.id and msg.embeds:
                        emb = msg.embeds[0]
                        if emb.footer and emb.footer.text == "OP Control Panel":
                            found = msg
                            break
                if found:
                    await found.edit(embed=build_main_embed(guild), view=MainMenuView(self))
                else:
                    await channel.send(embed=build_main_embed(guild), view=MainMenuView(self))
            except discord.Forbidden:
                logger.warning(f"Missing permissions to send/edit messages in {guild.id}#{channel.id}")
            except Exception:
                logger.exception("Failed to ensure control panel")

    def _pick_announce_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        # Prefer system channel if send allowed
        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:  # type: ignore
            return guild.system_channel  # type: ignore
        # Otherwise pick first text channel where bot can speak
        for ch in guild.text_channels:
            try:
                perms = ch.permissions_for(guild.me)
                if perms.send_messages and perms.read_messages:
                    return ch
            except Exception:
                continue
        return None


async def main():
    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN missing in environment. Create a .env with it or set env var.")

    bot = OPBot()
    async with bot:
        try:
            await bot.start(token)
        finally:
            # Graceful close of Mongo client
            if getattr(bot, "mongo_client", None):
                try:
                    bot.mongo_client.close()
                except Exception:
                    pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down…")
