import discord
from discord.ext import commands
from discord.ui import View
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# --- Centralized Strings & Utils ---
STRINGS = {
    "db_error": "❌ A database error occurred. Please try again later.",
    "permission_error": "❌ You do not have the required permissions to use this menu.",
    "view_timeout": "❌ This settings menu has timed out. Please use `/menu` to reopen it.",
}

def create_embed(title: str, description: str, color: discord.Color) -> discord.Embed:
    """Creates a standardized embed for the settings menu."""
    return discord.Embed(title=title, description=description, color=color)

# --- Base View ---
class BaseSettingsView(View):
    """A base view with timeout handling and a message attribute for editing."""
    def __init__(self, bot: commands.Bot, timeout: Optional[float] = 180.0):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.message: Optional[discord.Message] = None

    async def on_timeout(self) -> None:
        if self.message:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(content=STRINGS["view_timeout"], view=self)
            except discord.NotFound:
                pass # Message was deleted or interaction expired
    
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(f"An error occurred in view {self.__class__.__name__} for item {item.custom_id}: {error}", exc_info=True)
        if interaction.response.is_done():
            await interaction.followup.send(STRINGS["db_error"], ephemeral=True)
        else:
            await interaction.response.send_message(STRINGS["db_error"], ephemeral=True)
