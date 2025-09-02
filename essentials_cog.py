"""
Essentials cog — menu-only utility actions (no text commands).

Keeps core non-moderation utilities available via buttons to avoid
"interaction failed" issues and missing permissions edge cases.

Features:
- Post/refresh the main control panel in any channel
- Ping (latency) & Uptime
- Reload persistent views (useful after restarts/deploys)
- Admin-gated; all responses ephemeral where appropriate

Integrates with:
- utils.mk_embed, utils.admin_only
- cogs.menu_cog.MainMenuView, build_main_embed
"""
from __future__ import annotations

import time
import datetime as dt
from typing import Optional

import discord
from discord.ext import commands

from utils import mk_embed, admin_only


def build_essentials_embed(guild: discord.Guild, *, bot: commands.Bot) -> discord.Embed:
    # Uptime readout
    start = getattr(bot, "_start_time", None)
    uptime_text = "Unknown"
    if isinstance(start, float):
        delta = dt.timedelta(seconds=int(time.time() - start))
        uptime_text = str(delta)

    desc = (
        "Quick utilities for server admins.\n\n"
        "• Post/refresh the main control panel in this channel.\n"
        "• Check bot ping and uptime.\n"
        "• Reload persistent views (helpful after a restart).\n\n"
        f"**Uptime:** {uptime_text}"
    )
    e = mk_embed("Essentials", desc, discord.Color.blurple())
    return e


class EssentialsView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    # --- Post main control panel ---
    @discord.ui.button(label="Post Control Panel", style=discord.ButtonStyle.primary, custom_id="op:ess:post_panel")
    async def post_panel(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await self._safe_ephemeral(interaction, "Admins only.")
        from cogs.menu_cog import MainMenuView, build_main_embed
        try:
            await interaction.channel.send(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
            await self._safe_ephemeral(interaction, "Panel posted to this channel.")
        except discord.Forbidden:
            await self._safe_ephemeral(interaction, "I lack permission to send messages here.")
        except Exception:
            await self._safe_ephemeral(interaction, "Failed to post panel.")

    # --- Refresh this message to the Essentials embed ---
    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, custom_id="op:ess:refresh")
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await self._safe_ephemeral(interaction, "Admins only.")
        await self._edit_same_message(interaction)

    # --- Ping ---
    @discord.ui.button(label="Ping", style=discord.ButtonStyle.secondary, custom_id="op:ess:ping")
    async def ping(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await self._safe_ephemeral(interaction, "Admins only.")
        latency_ms = int(self.bot.latency * 1000)
        await self._safe_ephemeral(interaction, f"Pong! {latency_ms} ms")

    # --- Uptime ---
    @discord.ui.button(label="Uptime", style=discord.ButtonStyle.secondary, custom_id="op:ess:uptime")
    async def uptime(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await self._safe_ephemeral(interaction, "Admins only.")
        start = getattr(self.bot, "_start_time", None)
        if not isinstance(start, float):
            return await self._safe_ephemeral(interaction, "Uptime unknown.")
        delta = dt.timedelta(seconds=int(time.time() - start))
        await self._safe_ephemeral(interaction, f"Uptime: {delta}")

    # --- Reload Views (persists buttons after restart) ---
    @discord.ui.button(label="Reload Views", style=discord.ButtonStyle.secondary, custom_id="op:ess:reload_views")
    async def reload_views(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await self._safe_ephemeral(interaction, "Admins only.")
        try:
            # Import and add all persistent views again.
            from cogs.menu_cog import MainMenuView, ModerationParentView, LoggingSettingsView, TicketSettingsView, AntiRaidSettingsView
            from cogs.ticket_cog import TicketPanelView, TicketActionsView
            self.bot.add_view(MainMenuView(self.bot))
            self.bot.add_view(ModerationParentView(self.bot))
            self.bot.add_view(LoggingSettingsView(self.bot))
            self.bot.add_view(TicketSettingsView(self.bot))
            self.bot.add_view(AntiRaidSettingsView(self.bot))
            self.bot.add_view(TicketPanelView(self.bot))
            self.bot.add_view(TicketActionsView(self.bot))
            await self._safe_ephemeral(interaction, "Views reloaded.")
        except Exception:
            await self._safe_ephemeral(interaction, "Failed to reload views.")

    # --- Back ---
    @discord.ui.button(label="Back", style=discord.ButtonStyle.danger, custom_id="op:ess:back")
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button):
        from cogs.menu_cog import MainMenuView, build_main_embed
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
            else:
                await interaction.response.edit_message(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
        except Exception:
            pass

    # ---------- helpers ----------
    async def _edit_same_message(self, interaction: discord.Interaction):
        e = build_essentials_embed(interaction.guild, bot=self.bot)
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=e, view=self)
            else:
                await interaction.response.edit_message(embed=e, view=self)
        except Exception:
            pass

    async def _safe_ephemeral(self, interaction: discord.Interaction, content: str):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except Exception:
            pass


class EssentialsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Start time used for uptime calculations
        if not hasattr(self.bot, "_start_time"):
            self.bot._start_time = time.time()
        # Ensure persistent view survives restarts
        try:
            self.bot.add_view(EssentialsView(self.bot))
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(EssentialsCog(bot))
    try:
        bot.add_view(EssentialsView(bot))
    except Exception:
        pass
