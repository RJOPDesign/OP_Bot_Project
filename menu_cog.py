import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
import logging

# Import from the utils cog for consistency
from .utils import BaseSettingsView, create_embed, STRINGS
# Import views from other cogs for navigation
from .music_cog import MusicPlayerView

logger = logging.getLogger(__name__)

# --- Main Navigation Views ---

class StaffMenuView(BaseSettingsView):
    """The main menu for core staff features."""
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        
        back_button = Button(label="◀️ Back to Main Menu", style=discord.ButtonStyle.primary, custom_id="admin:nav_main")
        back_button.callback = self.go_to_main_menu
        self.add_item(back_button)

        essentials_button = Button(label="Essentials", emoji="⭐", custom_id="admin:nav_essentials")
        essentials_button.callback = self.go_to_essentials
        self.add_item(essentials_button)

        moderation_button = Button(label="Moderation", emoji="🛡️", custom_id="admin:nav_moderation")
        moderation_button.callback = self.go_to_moderation
        self.add_item(moderation_button)

        tickets_button = Button(label="Tickets", emoji="🎫", custom_id="admin:nav_tickets")
        tickets_button.callback = self.go_to_tickets
        self.add_item(tickets_button)

        logging_button = Button(label="Logging", emoji="📜", custom_id="admin:nav_logging")
        logging_button.callback = self.go_to_logging
        self.add_item(logging_button)

        purge_button = Button(label="Purge", emoji="🗑️", custom_id="admin:nav_purge")
        purge_button.callback = self.go_to_purge
        self.add_item(purge_button)

        giveaways_button = Button(label="Giveaways", emoji="🎉", custom_id="admin:nav_giveaways")
        giveaways_button.callback = self.go_to_giveaways
        self.add_item(giveaways_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("❌ You need the **Manage Server** or **Administrator** permission to use this menu.", ephemeral=True)
            return False
        return True

    async def go_to_main_menu(self, interaction: discord.Interaction):
        embed = create_embed("👋 Welcome!", "Please choose a menu to continue.", discord.Color.blurple())
        view = MainMenuSelectionView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)

    async def go_to_moderation(self, interaction: discord.Interaction) -> None:
        from .moderation_cog import ModerationParentView
        embed = create_embed("🛡️ Moderation Settings", "Configure AI-powered auto-moderation and anti-raid protection.", discord.Color.blue())
        view = ModerationParentView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def go_to_tickets(self, interaction: discord.Interaction) -> None:
        from .ticket_cog import TicketSettingsView
        embed = create_embed("🎫 Ticket Settings", "Manage ticket panels and settings.", discord.Color.green())
        view = TicketSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def go_to_logging(self, interaction: discord.Interaction) -> None:
        from .logging_cog import LoggingSettingsView
        embed = create_embed("📜 Logging Settings", "Choose which server events to log.", discord.Color.orange())
        config = await self.bot.get_guild_config(interaction.guild.id)
        view = LoggingSettingsView(self.bot, interaction.guild.id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        
    async def go_to_essentials(self, interaction: discord.Interaction) -> None:
        from .essentials_cog import EssentialsSettingsView
        embed = create_embed("⭐ Essentials", "Configure welcome messages, reaction roles, and other essential features.", discord.Color.gold())
        view = EssentialsSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        
    async def go_to_purge(self, interaction: discord.Interaction) -> None:
        from .purge_cog import PurgeSettingsView
        embed = create_embed("🗑️ Purge Messages", "Select a purge method.", discord.Color.dark_grey())
        view = PurgeSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        
    async def go_to_giveaways(self, interaction: discord.Interaction) -> None:
        from .giveaways_cog import GiveawaysSettingsView
        embed = create_embed("🎉 Giveaways", "Create and manage server giveaways.", discord.Color.from_rgb(255, 105, 180))
        view = GiveawaysSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

class MainMenuSelectionView(BaseSettingsView):
    """The initial view for the /menu command, allowing users to select Music or Staff menu."""
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

    @discord.ui.button(label="Music", style=discord.ButtonStyle.secondary, emoji="🎶")
    async def music_menu(self, interaction: discord.Interaction, button: Button):
        logger.info(f"User {interaction.user} accessed Music Menu in guild {interaction.guild.id}")
        embed = create_embed("🎶 Music Player", "Use the buttons below to control the music.", discord.Color.purple())
        view = MusicPlayerView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    @discord.ui.button(label="Staff Menu", style=discord.ButtonStyle.primary, emoji="🛡️")
    async def staff_menu(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need the **Manage Server** permission to use this menu.", ephemeral=True)
            return
            
        logger.info(f"User {interaction.user} accessed Staff Menu in guild {interaction.guild.id}")
        embed = create_embed("🛡️ Staff Menu", "Select a category to configure.", discord.Color.blue())
        view = StaffMenuView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

# --- Cog Loader ---
class MenuCog(commands.Cog):
    """A cog for handling the main /menu command and initial navigation."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="menu", description="Access bot features and settings.")
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id, i.user.id))
    async def menu(self, interaction: discord.Interaction):
        """Displays the main menu selection."""
        embed = create_embed("👋 Welcome!", "Please choose a menu to continue.", discord.Color.blurple())
        view = MainMenuSelectionView(self.bot)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @menu.error
    async def menu_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(f"❌ Please wait {error.retry_after:.1f} seconds before using this command again.", ephemeral=True)
        else:
            logger.error(f"An error occurred in the menu command: {error}")
            await interaction.response.send_message("An unexpected error occurred.", ephemeral=True)

async def setup(bot: commands.Bot):
    """Adds the MenuCog to the bot."""
    await bot.add_cog(MenuCog(bot))
