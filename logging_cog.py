"""
Logging cog — menu-only configuration and event logging.

Features
- Toggle logging on/off per guild
- Pick a log channel
- Toggle individual events: message delete/edit, member join/leave
- Persistent buttons with stable custom_ids
- Mongo-backed persistence if `bot.db` is available (collection: `configs`)
- Safe interaction handling to avoid "Interaction Failed"

Integrations
- utils.mk_embed, utils.admin_only, utils.ensure_guild_config
"""
from __future__ import annotations

from typing import Dict, Any, Optional, List
import datetime as dt

import discord
from discord.ext import commands

from utils import mk_embed, admin_only, ensure_guild_config


DEFAULT_EVENTS = {
    "message_delete": True,
    "message_edit": True,
    "member_join": True,
    "member_leave": True,
}


# --------------- storage helpers ---------------
async def get_log_config(bot: commands.Bot, guild_id: int) -> Dict[str, Any]:
    cfg = await ensure_guild_config(bot, guild_id)
    log_cfg = cfg.get("logging") or {}
    # normalize shape
    return {
        "enabled": bool(log_cfg.get("enabled", True)),
        "channel_id": log_cfg.get("channel_id"),
        "events": {**DEFAULT_EVENTS, **(log_cfg.get("events") or {})},
    }


async def save_log_config(bot: commands.Bot, guild_id: int, new_cfg: Dict[str, Any]):
    # merge into root configs.logging
    base = await ensure_guild_config(bot, guild_id)
    merged = {
        **base,
        "logging": {
            "enabled": bool(new_cfg.get("enabled", True)),
            "channel_id": new_cfg.get("channel_id"),
            "events": {**DEFAULT_EVENTS, **(new_cfg.get("events") or {})},
        },
    }
    if getattr(bot, "db", None):
        await bot.db["configs"].update_one({"guild_id": guild_id}, {"$set": merged}, upsert=True)
    else:
        # memory fallback (non-persistent)
        store = bot.__dict__.setdefault("_mem_configs", {})
        store[guild_id] = merged


# --------------- embed ---------------
async def build_logging_embed_async(guild: discord.Guild, bot: commands.Bot) -> discord.Embed:
    cfg = await get_log_config(bot, guild.id)
    ch_text = f"<#{cfg['channel_id']}>" if cfg.get("channel_id") else "Not set"
    ev = cfg.get("events", {})
    lines = [
        f"**Status:** {'ON' if cfg.get('enabled') else 'OFF'}",
        f"**Channel:** {ch_text}",
        "**Events:**",
        f"• Message Delete: {'✅' if ev.get('message_delete') else '❌'}",
        f"• Message Edit: {'✅' if ev.get('message_edit') else '❌'}",
        f"• Member Join: {'✅' if ev.get('member_join') else '❌'}",
        f"• Member Leave: {'✅' if ev.get('member_leave') else '❌'}",
    ]
    return mk_embed("Logging Settings", "\n".join(lines))


def build_logging_embed(guild: discord.Guild) -> discord.Embed:
    # Synchronous placeholder (main menu can call this; view will refresh with async details)
    return mk_embed("Logging Settings", "Loading…")


# --------------- views ---------------
class LoggingSettingsView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _refresh_embed(self, interaction: discord.Interaction):
        e = await build_logging_embed_async(interaction.guild, self.bot)
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=e, view=self)
            else:
                await interaction.response.edit_message(embed=e, view=self)
        except Exception:
            pass

    # --- Toggle on/off ---
    @discord.ui.button(label="Toggle On/Off", style=discord.ButtonStyle.primary, custom_id="op:log:toggle")
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        cfg = await get_log_config(self.bot, interaction.guild.id)
        cfg["enabled"] = not cfg.get("enabled", True)
        await save_log_config(self.bot, interaction.guild.id, cfg)
        await _safe_ephemeral(interaction, f"Logging turned {'ON' if cfg['enabled'] else 'OFF'}.")
        await self._refresh_embed(interaction)

    # --- Set channel ---
    @discord.ui.button(label="Set Channel", style=discord.ButtonStyle.secondary, custom_id="op:log:channel")
    async def set_channel(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        await _present_channel_picker(self.bot, interaction)

    # --- Toggle events ---
    @discord.ui.button(label="Toggle Events", style=discord.ButtonStyle.secondary, custom_id="op:log:events")
    async def toggle_events(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        await _present_event_picker(self.bot, interaction)

    # --- Back ---
    @discord.ui.button(label="Back", style=discord.ButtonStyle.danger, custom_id="op:log:back")
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button):
        from cogs.menu_cog import MainMenuView, build_main_embed
        try:
            await interaction.response.edit_message(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
        except Exception:
            try:
                await interaction.edit_original_response(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
            except Exception:
                pass


class LogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="Pick a log channel…", min_values=1, max_values=1, custom_id="op:log:pickchan")


class LogEventsSelect(discord.ui.Select):
    def __init__(self, current: Dict[str, bool]):
        opts = [
            discord.SelectOption(label="Message Delete", value="message_delete", default=current.get("message_delete", True)),
            discord.SelectOption(label="Message Edit", value="message_edit", default=current.get("message_edit", True)),
            discord.SelectOption(label="Member Join", value="member_join", default=current.get("member_join", True)),
            discord.SelectOption(label="Member Leave", value="member_leave", default=current.get("member_leave", True)),
        ]
        super().__init__(placeholder="Toggle events (select those to ENABLE)", min_values=0, max_values=len(opts), options=opts, custom_id="op:log:pickevents")


class LogChannelPickView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=60)
        self.bot = bot
        self.add_item(LogChannelSelect())

    @discord.ui.button(label="Save", style=discord.ButtonStyle.primary, custom_id="op:log:savechan")
    async def save(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        sel: LogChannelSelect = next((c for c in self.children if isinstance(c, LogChannelSelect)), None)
        if not sel or not sel.values:
            return await _safe_ephemeral(interaction, "Select a channel first.")
        channel_id = int(sel.values[0])
        cfg = await get_log_config(self.bot, interaction.guild.id)
        cfg["channel_id"] = channel_id
        await save_log_config(self.bot, interaction.guild.id, cfg)
        await _safe_ephemeral(interaction, f"Log channel set to <#{channel_id}>.")
        # Close picker and refresh parent message if possible
        await _refresh_parent_logging_embed(self.bot, interaction)


class LogEventsPickView(discord.ui.View):
    def __init__(self, bot: commands.Bot, current: Dict[str, bool]):
        super().__init__(timeout=60)
        self.bot = bot
        self.add_item(LogEventsSelect(current))

    @discord.ui.button(label="Save", style=discord.ButtonStyle.primary, custom_id="op:log:saveevents")
    async def save(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        sel: LogEventsSelect = next((c for c in self.children if isinstance(c, LogEventsSelect)), None)
        values = set(sel.values) if sel else set()
        # Selected are enabled
        cfg = await get_log_config(self.bot, interaction.guild.id)
        cfg_events = {k: (k in values) for k in DEFAULT_EVENTS.keys()}
        cfg["events"] = cfg_events
        await save_log_config(self.bot, interaction.guild.id, cfg)
        await _safe_ephemeral(interaction, "Logging events updated.")
        await _refresh_parent_logging_embed(self.bot, interaction)


async def _present_channel_picker(bot: commands.Bot, interaction: discord.Interaction):
    try:
        await interaction.response.send_message("Select a log channel:", view=LogChannelPickView(bot), ephemeral=True)
    except Exception:
        pass


async def _present_event_picker(bot: commands.Bot, interaction: discord.Interaction):
    cfg = await get_log_config(bot, interaction.guild.id)
    try:
        await interaction.response.send_message("Toggle events to enable:", view=LogEventsPickView(bot, cfg.get("events", {})), ephemeral=True)
    except Exception:
        pass


async def _refresh_parent_logging_embed(bot: commands.Bot, interaction: discord.Interaction):
    # Tries to find the last message with LoggingSettingsView in the channel and refresh it.
    try:
        async for msg in interaction.channel.history(limit=20):
            if msg.author.id == bot.user.id and isinstance(msg.components, list):
                # best-effort: just try editing with a fresh view
                e = await build_logging_embed_async(interaction.guild, bot)
                try:
                    await msg.edit(embed=e, view=LoggingSettingsView(bot))
                except Exception:
                    pass
                break
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


# --------------- Cog ---------------
class LoggingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        # Ensure persistent settings view is registered for restarts
        try:
            self.bot.add_view(LoggingSettingsView(self.bot))
        except Exception:
            pass

    # --- Event handlers ---
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        cfg = await get_log_config(self.bot, message.guild.id)
        if not cfg.get("enabled") or not cfg["events"].get("message_delete"):
            return
        ch = message.guild.get_channel(cfg.get("channel_id") or 0)
        if not isinstance(ch, discord.TextChannel):
            return
        e = discord.Embed(title="🗑️ Message Deleted", color=discord.Color.red(), timestamp=dt.datetime.utcnow())
        e.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
        e.add_field(name="Channel", value=message.channel.mention)
        if message.content:
            e.add_field(name="Content", value=(message.content[:1024]), inline=False)
        e.set_footer(text=f"Message ID: {message.id}")
        try:
            await ch.send(embed=e)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or after.author.bot:
            return
        if before.content == after.content:
            return
        cfg = await get_log_config(self.bot, after.guild.id)
        if not cfg.get("enabled") or not cfg["events"].get("message_edit"):
            return
        ch = after.guild.get_channel(cfg.get("channel_id") or 0)
        if not isinstance(ch, discord.TextChannel):
            return
        e = discord.Embed(title="✏️ Message Edited", color=discord.Color.orange(), timestamp=dt.datetime.utcnow())
        e.add_field(name="Author", value=f"{after.author} ({after.author.id})", inline=False)
        e.add_field(name="Channel", value=after.channel.mention)
        if before.content:
            e.add_field(name="Before", value=before.content[:1024], inline=False)
        if after.content:
            e.add_field(name="After", value=after.content[:1024], inline=False)
        e.set_footer(text=f"Message ID: {after.id}")
        try:
            await ch.send(embed=e)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = await get_log_config(self.bot, member.guild.id)
        if not cfg.get("enabled") or not cfg["events"].get("member_join"):
            return
        ch = member.guild.get_channel(cfg.get("channel_id") or 0)
        if not isinstance(ch, discord.TextChannel):
            return
        e = discord.Embed(title="➕ Member Joined", description=f"{member.mention} ({member.id})", color=discord.Color.green(), timestamp=dt.datetime.utcnow())
        try:
            await ch.send(embed=e)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        cfg = await get_log_config(self.bot, member.guild.id)
        if not cfg.get("enabled") or not cfg["events"].get("member_leave"):
            return
        ch = member.guild.get_channel(cfg.get("channel_id") or 0)
        if not isinstance(ch, discord.TextChannel):
            return
        e = discord.Embed(title="➖ Member Left", description=f"{member} ({member.id})", color=discord.Color.dark_grey(), timestamp=dt.datetime.utcnow())
        try:
            await ch.send(embed=e)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(LoggingCog(bot))
    try:
        bot.add_view(LoggingSettingsView(bot))
    except Exception:
        pass
