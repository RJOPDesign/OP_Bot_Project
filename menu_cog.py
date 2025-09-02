"""
Main menu cog — central hub for all panels.

Features
- A single persistent main control panel message (per channel) with buttons to sub-panels:
  • Moderation
  • Logging
  • Tickets (Public + Settings)
  • Giveaways
  • Purge Tools
  • Music
  • Essentials (ping/uptime, post panel, reload views)
- Admin-gated entry; buttons swap the same message to the selected panel embed+view
- Stable custom_ids so views persist across restarts
- Helper `build_main_embed(guild)` used by other cogs when returning to menu
"""
from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands

from utils import mk_embed, admin_only


# ----------------- Main embed -----------------

def build_main_embed(guild: discord.Guild) -> discord.Embed:
    desc = (
        "Use the buttons below to open a panel.\n\n"
        "• Moderation — timeout, kick, ban, unban, purge\n"
        "• Logging — configure log channel & events\n"
        "• Tickets — open/close tickets and settings\n"
        "• Giveaways — create and manage giveaways\n"
        "• Purge Tools — advanced message cleanup\n"
        "• Music — YouTube playback (modal + queue)\n"
        "• Essentials — uptime, ping, post panel, reload views"
    )
    return mk_embed(f"OP Bot • {guild.name}", desc)


# ----------------- View -----------------

class MainMenuView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    # Moderation
    @discord.ui.button(label="Moderation", style=discord.ButtonStyle.primary, custom_id="op:menu:moderation")
    async def moderation(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        from cogs.moderation_cog import ModerationParentView, build_moderation_embed
        await _swap(interaction, build_moderation_embed(interaction.guild), ModerationParentView(self.bot))

    # Logging
    @discord.ui.button(label="Logging", style=discord.ButtonStyle.secondary, custom_id="op:menu:logging")
    async def logging(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        from cogs.logging_cog import LoggingSettingsView, build_logging_embed
        await _swap(interaction, build_logging_embed(interaction.guild), LoggingSettingsView(self.bot))

    # Tickets
    @discord.ui.button(label="Tickets", style=discord.ButtonStyle.secondary, custom_id="op:menu:tickets")
    async def tickets(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        from cogs.ticket_cog import TicketSettingsView, build_ticket_embed
        await _swap(interaction, build_ticket_embed(interaction.guild), TicketSettingsView(self.bot))

    # Giveaways
    @discord.ui.button(label="Giveaways", style=discord.ButtonStyle.secondary, custom_id="op:menu:giveaways")
    async def giveaways(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        from cogs.giveaways_cog import GiveawaysPanelView, build_gw_panel
        await _swap(interaction, build_gw_panel(interaction.guild), GiveawaysPanelView(self.bot))

    # Purge Tools
    @discord.ui.button(label="Purge Tools", style=discord.ButtonStyle.secondary, custom_id="op:menu:purge")
    async def purge_tools(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        from cogs.purge_cog import PurgePanelView, build_purge_embed
        await _swap(interaction, build_purge_embed(interaction.guild), PurgePanelView(self.bot))

    # Music
    @discord.ui.button(label="Music", style=discord.ButtonStyle.secondary, custom_id="op:menu:music")
    async def music(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        from cogs.music_cog import MusicPanelView, build_music_embed
        # live embed pulls now/queue when the view refreshes; this one is static
        await _swap(interaction, build_music_embed(interaction.guild), MusicPanelView(self.bot))

    # Essentials
    @discord.ui.button(label="Essentials", style=discord.ButtonStyle.secondary, custom_id="op:menu:essentials")
    async def essentials(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        from cogs.essentials_cog import EssentialsView, build_essentials_embed
        await _swap(interaction, build_essentials_embed(interaction.guild, bot=self.bot), EssentialsView(self.bot))


# ----------------- helpers -----------------

async def _swap(interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View):
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
    except Exception:
        pass


async def _safe_ephemeral(interaction: discord.Interaction, content: str):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        pass


# ----------------- Cog -----------------

class MenuCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        # Register persistent view so the main menu buttons survive restarts
        try:
            self.bot.add_view(MainMenuView(self.bot))
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(MenuCog(bot))
    try:
        bot.add_view(MainMenuView(bot))
    except Exception:
        pass
