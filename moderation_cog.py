"""
Moderation cog — menu-only actions with persistent UI.

Features
- Choose target with a UserSelect.
- Timeout (minutes + optional reason).
- Kick / Ban (optional reason) and Unban (by user ID or name#discriminator fallback).
- Purge last N messages (1–100) in the current channel.
- Admin-gated; safe ephemeral responses to avoid "Interaction Failed".
- Stable custom_ids so buttons persist across restarts.

Integrations
- utils.mk_embed, utils.admin_only
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Optional

import discord
from discord.ext import commands

from utils import mk_embed, admin_only


# ----------------- Embeds -----------------
def build_moderation_embed(guild: discord.Guild) -> discord.Embed:
    desc = (
        "Moderation panel.\n\n"
        "1) Select a user.\n"
        "2) Use buttons to Timeout / Kick / Ban / Unban / Purge.\n\n"
        "Timeout uses minutes and an optional reason. Purge deletes the most recent N messages in this channel."
    )
    return mk_embed("Moderation", desc, discord.Color.red())


# ----------------- Modals -----------------
class TimeoutModal(discord.ui.Modal, title="Timeout User"):
    minutes = discord.ui.TextInput(label="Minutes", placeholder="10", required=True, max_length=6)
    reason = discord.ui.TextInput(label="Reason (optional)", placeholder="Rule 1: Be respectful", required=False, max_length=200)

    def __init__(self, target: discord.Member):
        super().__init__(timeout=None)
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        try:
            mins = max(1, int(str(self.minutes)))
        except ValueError:
            return await _safe_ephemeral(interaction, "Enter a whole number of minutes.")
        delta = dt.timedelta(minutes=mins)
        try:
            await self.target.timeout(delta, reason=str(self.reason) or f"By {interaction.user}")
            await _safe_ephemeral(interaction, f"Timed out {self.target.mention} for {mins} minute(s).")
        except discord.Forbidden:
            await _safe_ephemeral(interaction, "I lack permission to timeout that user.")
        except Exception:
            await _safe_ephemeral(interaction, "Failed to apply timeout.")


class PurgeModal(discord.ui.Modal, title="Purge Messages"):
    amount = discord.ui.TextInput(label="How many? (1–100)", placeholder="10", required=True, max_length=3)

    async def on_submit(self, interaction: discord.Interaction):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        try:
            n = max(1, min(100, int(str(self.amount))))
        except ValueError:
            return await _safe_ephemeral(interaction, "Enter an integer between 1 and 100.")
        try:
            deleted = await interaction.channel.purge(limit=n)
            await _safe_ephemeral(interaction, f"Deleted {len(deleted)} message(s).")
        except discord.Forbidden:
            await _safe_ephemeral(interaction, "I lack permission to manage messages here.")
        except Exception:
            await _safe_ephemeral(interaction, "Failed to purge messages.")


class UnbanModal(discord.ui.Modal, title="Unban User"):
    user_id = discord.ui.TextInput(label="User ID or name#1234", placeholder="123456789012345678 or Name#0001", required=True, max_length=40)
    reason = discord.ui.TextInput(label="Reason (optional)", required=False, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        guild = interaction.guild
        if not guild:
            return await _safe_ephemeral(interaction, "Guild-only action.")

        query = str(self.user_id).strip()
        target: Optional[discord.User] = None

        # Try snowflake first
        if query.isdigit():
            try:
                target = await interaction.client.fetch_user(int(query))
            except Exception:
                target = None
        # Fallback to name#discriminator
        if not target and "#" in query:
            name, _, disc = query.partition("#")
            try:
                bans = [entry async for entry in guild.bans(limit=200)]
                for entry in bans:
                    u = entry.user
                    if (u.name == name and str(u.discriminator) == disc):
                        target = u
                        break
            except Exception:
                pass

        if not target:
            return await _safe_ephemeral(interaction, "User not found in ban list.")

        try:
            await guild.unban(target, reason=str(self.reason) or f"By {interaction.user}")
            await _safe_ephemeral(interaction, f"Unbanned {target}.")
        except discord.Forbidden:
            await _safe_ephemeral(interaction, "I lack permission to unban that user.")
        except Exception:
            await _safe_ephemeral(interaction, "Failed to unban user.")


# ----------------- View -----------------
class ModerationParentView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.selected_user: Optional[discord.Member] = None

    @discord.ui.user_select(placeholder="Select a user…", custom_id="op:mod:selectuser")
    async def select_user(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        member = interaction.guild.get_member(select.values[0].id)
        if not isinstance(member, discord.Member):
            return await _safe_ephemeral(interaction, "Could not resolve that member.")
        self.selected_user = member
        await _safe_ephemeral(interaction, f"Selected {member.mention}")

    @discord.ui.button(label="Timeout", style=discord.ButtonStyle.primary, custom_id="op:mod:timeout")
    async def btn_timeout(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        if not self.selected_user:
            return await _safe_ephemeral(interaction, "Select a user first.")
        await interaction.response.send_modal(TimeoutModal(self.selected_user))

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.secondary, custom_id="op:mod:kick")
    async def btn_kick(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        if not self.selected_user:
            return await _safe_ephemeral(interaction, "Select a user first.")
        try:
            await self.selected_user.kick(reason=f"By {interaction.user}")
            await _safe_ephemeral(interaction, f"Kicked {self.selected_user}.")
        except discord.Forbidden:
            await _safe_ephemeral(interaction, "I lack permission to kick that user.")
        except Exception:
            await _safe_ephemeral(interaction, "Failed to kick user.")

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger, custom_id="op:mod:ban")
    async def btn_ban(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        if not self.selected_user:
            return await _safe_ephemeral(interaction, "Select a user first.")
        try:
            await interaction.guild.ban(self.selected_user, reason=f"By {interaction.user}")
            await _safe_ephemeral(interaction, f"Banned {self.selected_user}.")
        except discord.Forbidden:
            await _safe_ephemeral(interaction, "I lack permission to ban that user.")
        except Exception:
            await _safe_ephemeral(interaction, "Failed to ban user.")

    @discord.ui.button(label="Unban", style=discord.ButtonStyle.secondary, custom_id="op:mod:unban")
    async def btn_unban(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        await interaction.response.send_modal(UnbanModal())

    @discord.ui.button(label="Purge", style=discord.ButtonStyle.secondary, custom_id="op:mod:purge")
    async def btn_purge(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        await interaction.response.send_modal(PurgeModal())

    @discord.ui.button(label="Back", style=discord.ButtonStyle.danger, custom_id="op:mod:back")
    async def btn_back(self, interaction: discord.Interaction, _: discord.ui.Button):
        from cogs.menu_cog import MainMenuView, build_main_embed
        try:
            await interaction.response.edit_message(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
        except Exception:
            try:
                await interaction.edit_original_response(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
            except Exception:
                pass


# ----------------- helpers -----------------
async def _safe_ephemeral(interaction: discord.Interaction, content: str):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        pass


# ----------------- Cog -----------------
class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        # Ensure persistent view survives restarts
        try:
            self.bot.add_view(ModerationParentView(self.bot))
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
    try:
        bot.add_view(ModerationParentView(bot))
    except Exception:
        pass
