"""
Purge cog — menu-only advanced message cleanup tools.

Features
- Purge Recent: delete N most recent messages in the current channel (<= 1000 scanned)
- Purge By User: delete N messages by a specific user
- Purge Contains: delete N messages containing a keyword/phrase
- Purge Links: delete N messages that contain links
- Purge Bots: delete N messages sent by bots
- All actions are admin-only and perform safe, rate-limit-friendly deletions
- Works even for messages older than 14 days by deleting one-by-one (Discord limitation)
- Persistent view with stable custom_ids; safe ephemeral responses to avoid "Interaction Failed"

Integrations
- utils.mk_embed, utils.admin_only
"""
from __future__ import annotations

import re
from typing import Optional, Callable, Awaitable

import discord
from discord.ext import commands

from utils import mk_embed, admin_only


# ----------------- Embed -----------------

def build_purge_embed(guild: discord.Guild) -> discord.Embed:
    desc = (
        "Advanced purge tools for this channel.\n\n"
        "• Purge Recent — delete the most recent N messages.\n"
        "• Purge By User — delete messages by a specific user.\n"
        "• Purge Contains — delete messages containing a keyword.\n"
        "• Purge Links — delete messages that contain URLs.\n"
        "• Purge Bots — delete messages sent by bots.\n\n"
        "Note: Messages older than 14 days are removed one-by-one (slower)."
    )
    return mk_embed("Purge Tools", desc, discord.Color.red())


# ----------------- Modals -----------------

class CountOnlyModal(discord.ui.Modal, title="How many messages?"):
    count = discord.ui.TextInput(label="Count (1–1000)", placeholder="100", required=True, max_length=4)

    def __init__(self, on_submit_cb: Callable[[discord.Interaction, int], Awaitable[None]]):
        super().__init__(timeout=None)
        self._cb = on_submit_cb

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = max(1, min(1000, int(str(self.count))))
        except ValueError:
            return await _safe_ephemeral(interaction, "Enter a whole number between 1 and 1000.")
        await self._cb(interaction, n)


class CountAndUserModal(discord.ui.Modal, title="Purge by user"):
    user = discord.ui.TextInput(label="User ID or mention", placeholder="123456789012345678 or @User", required=True, max_length=40)
    count = discord.ui.TextInput(label="Count (1–1000)", placeholder="100", required=True, max_length=4)

    def __init__(self, on_submit_cb: Callable[[discord.Interaction, int, int], Awaitable[None]]):
        super().__init__(timeout=None)
        self._cb = on_submit_cb

    async def on_submit(self, interaction: discord.Interaction):
        # Resolve user id
        text = str(self.user).strip()
        uid: Optional[int] = None
        m = re.search(r"(\d{15,25})", text)
        if m:
            try:
                uid = int(m.group(1))
            except Exception:
                uid = None
        if uid is None:
            return await _safe_ephemeral(interaction, "Could not parse a valid user ID or mention.")
        try:
            n = max(1, min(1000, int(str(self.count))))
        except ValueError:
            return await _safe_ephemeral(interaction, "Enter a whole number between 1 and 1000.")
        await self._cb(interaction, uid, n)


class CountAndQueryModal(discord.ui.Modal, title="Purge contains"):
    query = discord.ui.TextInput(label="Keyword / phrase", placeholder="spam.com", required=True, max_length=100)
    count = discord.ui.TextInput(label="Count (1–1000)", placeholder="100", required=True, max_length=4)

    def __init__(self, on_submit_cb: Callable[[discord.Interaction, str, int], Awaitable[None]]):
        super().__init__(timeout=None)
        self._cb = on_submit_cb

    async def on_submit(self, interaction: discord.Interaction):
        q = str(self.query).strip().lower()
        if not q:
            return await _safe_ephemeral(interaction, "Enter a keyword.")
        try:
            n = max(1, min(1000, int(str(self.count))))
        except ValueError:
            return await _safe_ephemeral(interaction, "Enter a whole number between 1 and 1000.")
        await self._cb(interaction, q, n)


# ----------------- View -----------------

class PurgePanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    # ------- Buttons -------
    @discord.ui.button(label="Purge Recent", style=discord.ButtonStyle.primary, custom_id="op:purge:recent")
    async def purge_recent(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        async def run(inter: discord.Interaction, n: int):
            await _purge_with_filter(inter, n, lambda m: True)
        await interaction.response.send_modal(CountOnlyModal(run))

    @discord.ui.button(label="Purge By User", style=discord.ButtonStyle.secondary, custom_id="op:purge:user")
    async def purge_user(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        async def run(inter: discord.Interaction, uid: int, n: int):
            await _purge_with_filter(inter, n, lambda m: m.author and m.author.id == uid)
        await interaction.response.send_modal(CountAndUserModal(run))

    @discord.ui.button(label="Purge Contains", style=discord.ButtonStyle.secondary, custom_id="op:purge:contains")
    async def purge_contains(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        async def run(inter: discord.Interaction, q: str, n: int):
            ql = q.lower()
            await _purge_with_filter(inter, n, lambda m: (m.content or "").lower().find(ql) != -1)
        await interaction.response.send_modal(CountAndQueryModal(run))

    @discord.ui.button(label="Purge Links", style=discord.ButtonStyle.secondary, custom_id="op:purge:links")
    async def purge_links(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        url_regex = re.compile(r"https?://|discord\.gg/|www\.", re.I)
        async def run(inter: discord.Interaction, n: int):
            await _purge_with_filter(inter, n, lambda m: bool(url_regex.search(m.content or "")))
        await interaction.response.send_modal(CountOnlyModal(run))

    @discord.ui.button(label="Purge Bots", style=discord.ButtonStyle.secondary, custom_id="op:purge:bots")
    async def purge_bots(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        async def run(inter: discord.Interaction, n: int):
            await _purge_with_filter(inter, n, lambda m: getattr(m.author, 'bot', False))
        await interaction.response.send_modal(CountOnlyModal(run))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.danger, custom_id="op:purge:back")
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button):
        from cogs.menu_cog import MainMenuView, build_main_embed
        try:
            await interaction.response.edit_message(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
        except Exception:
            try:
                await interaction.edit_original_response(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
            except Exception:
                pass


# ----------------- Core purge logic -----------------

async def _purge_with_filter(interaction: discord.Interaction, target_count: int, pred: Callable[[discord.Message], bool]):
    # Permission checks
    if not interaction.guild:
        return await _safe_ephemeral(interaction, "Guild-only action.")
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
        return await _safe_ephemeral(interaction, "Admins only.")
    if not isinstance(interaction.channel, discord.TextChannel):
        return await _safe_ephemeral(interaction, "This isn’t a text channel.")
    me_perms = interaction.channel.permissions_for(interaction.guild.me)
    if not me_perms.manage_messages:
        return await _safe_ephemeral(interaction, "I need the **Manage Messages** permission here.")

    await _safe_ephemeral(interaction, "Working… (this can take a moment for older messages)")

    deleted = 0
    scanned = 0
    # We scan up to ~5000 recent messages to find matches, but stop once we delete target_count
    async for msg in interaction.channel.history(limit=5000):
        scanned += 1
        if pred(msg):
            try:
                await msg.delete()
                deleted += 1
            except discord.Forbidden:
                pass
            except Exception:
                pass
            if deleted >= target_count:
                break

    await _safe_ephemeral(interaction, f"Purged {deleted} message(s). Scanned {scanned}.)")


# ----------------- Helpers -----------------

async def _safe_ephemeral(interaction: discord.Interaction, content: str):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        pass


# ----------------- Cog -----------------

class PurgeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            self.bot.add_view(PurgePanelView(self.bot))
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(PurgeCog(bot))
    try:
        bot.add_view(PurgePanelView(bot))
    except Exception:
        pass
