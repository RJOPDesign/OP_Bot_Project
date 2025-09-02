"""
Giveaways cog — menu-only giveaways with persistent buttons.

Features
- Create giveaway (modal: prize, duration minutes, winners count)
- Users click "Enter" to toggle entry
- Auto-end at deadline; pick winners randomly
- Admin actions: End Now, Reroll
- List active giveaways (ephemeral)
- MongoDB persistence if bot.db is available (recommended for hosting)
- Safe interaction handling to avoid "Interaction Failed"

Schema (MongoDB: collection "giveaways")
{
  guild_id: int,
  channel_id: int,
  message_id: int,
  prize: str,
  winners: int,
  end_ts: int,            # unix timestamp (seconds)
  host_id: int,
  entrants: [int],
  status: "active"|"ended",
  last_winners: [int]     # optional
}
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional, List, Dict

import discord
from discord.ext import commands

from utils import mk_embed, admin_only


# ---------- Embeds ----------
def build_gw_panel(guild: discord.Guild) -> discord.Embed:
    desc = (
        "Giveaways panel.\n\n"
        "• Create a giveaway (prize, duration, winners).\n"
        "• Users join by clicking *Enter*.\n"
        "• Auto-picks winners at end time. Admins can end early or reroll.\n"
        "• Use MongoDB for persistence across restarts."
    )
    return mk_embed("Giveaways", desc)


def build_gw_message_embed(prize: str, winners: int, end_ts: int, host: discord.Member, *, entrants: int = 0, ended: bool = False, last_winners: Optional[List[int]] = None) -> discord.Embed:
    remaining = max(0, end_ts - int(time.time()))
    mins, secs = divmod(remaining, 60)
    status = "Ended" if ended else f"Ends in {mins}m {secs}s"
    e = discord.Embed(title=f"🎉 Giveaway: {prize}", color=discord.Color.blurple())
    e.add_field(name="Winners", value=str(winners))
    e.add_field(name="Entries", value=str(entrants))
    e.add_field(name="Status", value=status, inline=False)
    e.set_footer(text=f"Host: {host} • OP Control Panel")
    if ended and last_winners:
        e.add_field(name="Last Winners", value=", ".join(f"<@{uid}>" for uid in last_winners) or "None", inline=False)
    return e


# ---------- Data model ----------
@dataclass
class Giveaway:
    guild_id: int
    channel_id: int
    message_id: int
    prize: str
    winners: int
    end_ts: int
    host_id: int
    entrants: List[int]
    status: str = "active"
    last_winners: Optional[List[int]] = None


# ---------- Modal ----------
class CreateGiveawayModal(discord.ui.Modal, title="Create Giveaway"):
    prize = discord.ui.TextInput(label="Prize", placeholder="$50 Amazon Gift Card", required=True, max_length=100)
    duration = discord.ui.TextInput(label="Duration (minutes)", placeholder="60", required=True, max_length=6)
    winners = discord.ui.TextInput(label="Number of winners", placeholder="1", required=True, max_length=3)

    def __init__(self, bot: commands.Bot, parent_view: 'GiveawaysPanelView'):
        super().__init__(timeout=None)
        self.bot = bot
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not admin_only(interaction):
            return await interaction.followup.send("Admins only.", ephemeral=True)
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.followup.send("Guild-only action.", ephemeral=True)
        try:
            minutes = int(str(self.duration))
            winners = max(1, int(str(self.winners)))
            if minutes <= 0:
                raise ValueError
        except ValueError:
            return await interaction.followup.send("Enter valid integers for duration and winners.", ephemeral=True)

        end_ts = int(time.time()) + minutes * 60
        prize = str(self.prize).strip()
        channel: discord.TextChannel = interaction.channel  # type: ignore

        # Post giveaway message with entry buttons
        try:
            host = interaction.user
            e = build_gw_message_embed(prize, winners, end_ts, host, entrants=0)
            view = GiveawayEntryView(self.bot)  # will attach giveaway_id after DB insert
            msg = await channel.send(embed=e, view=view)
        except discord.Forbidden:
            return await interaction.followup.send("I lack permission to send messages here.", ephemeral=True)

        gw = Giveaway(
            guild_id=interaction.guild.id,
            channel_id=channel.id,
            message_id=msg.id,
            prize=prize,
            winners=winners,
            end_ts=end_ts,
            host_id=host.id,
            entrants=[],
        )

        # Persist
        await save_giveaway(self.bot, gw)

        # Rebuild view with giveaway id and make it persistent
        try:
            view.giveaway_message_id = msg.id
            await update_giveaway_message(self.bot, gw)  # updates embed with correct entry count
            self.bot.add_view(GiveawayEntryView(self.bot, giveaway_message_id=msg.id))
        except Exception:
            pass

        # Schedule auto end
        await schedule_end_task(self.bot, gw)

        await interaction.followup.send(f"🎉 Giveaway created: **{prize}** (ends in {minutes}m)", ephemeral=True)


# ---------- Views ----------
class GiveawaysPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Create Giveaway", style=discord.ButtonStyle.primary, custom_id="op:gw:create")
    async def create(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        await interaction.response.send_modal(CreateGiveawayModal(self.bot, self))

    @discord.ui.button(label="List Active", style=discord.ButtonStyle.secondary, custom_id="op:gw:list")
    async def list_active(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        gws = await list_active_giveaways(self.bot, interaction.guild.id)
        if not gws:
            return await _safe_ephemeral(interaction, "No active giveaways.")
        lines = []
        now = int(time.time())
        for gw in gws:
            rem = max(0, gw.end_ts - now)
            m, s = divmod(rem, 60)
            lines.append(f"• **{gw.prize}** — <#{gw.channel_id}> — ends in {m}m {s}s (entries: {len(gw.entrants)})")
        await _safe_ephemeral(interaction, "\n".join(lines))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.danger, custom_id="op:gw:back")
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button):
        from cogs.menu_cog import MainMenuView, build_main_embed
        try:
            await interaction.response.edit_message(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
        except Exception:
            try:
                await interaction.edit_original_response(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
            except Exception:
                pass


class GiveawayEntryView(discord.ui.View):
    """Persistent view attached to each giveaway message."""

    def __init__(self, bot: commands.Bot, *, giveaway_message_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.bot = bot
        self.giveaway_message_id = giveaway_message_id  # message id used as unique key

    @discord.ui.button(label="Enter/Leave", style=discord.ButtonStyle.primary, custom_id="op:gw:enter")
    async def enter(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not interaction.guild:
            return await _safe_ephemeral(interaction, "Guild only.")
        doc = await get_giveaway_by_message(self.bot, self.giveaway_message_id)
        if not doc or doc.status != "active":
            return await _safe_ephemeral(interaction, "This giveaway is not active.")
        uid = interaction.user.id
        entrants = set(doc.entrants)
        if uid in entrants:
            entrants.remove(uid)
            action = "left"
        else:
            entrants.add(uid)
            action = "entered"
        doc.entrants = list(entrants)
        await save_giveaway(self.bot, doc)
        await update_giveaway_message(self.bot, doc)
        await _safe_ephemeral(interaction, f"You {action} the giveaway for **{doc.prize}**.")

    @discord.ui.button(label="End Now", style=discord.ButtonStyle.secondary, custom_id="op:gw:end")
    async def end_now(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        doc = await get_giveaway_by_message(self.bot, self.giveaway_message_id)
        if not doc or doc.status != "active":
            return await _safe_ephemeral(interaction, "Not active.")
        await end_giveaway(self.bot, doc)
        await _safe_ephemeral(interaction, "Giveaway ended.")

    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.secondary, custom_id="op:gw:reroll")
    async def reroll(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        doc = await get_giveaway_by_message(self.bot, self.giveaway_message_id)
        if not doc:
            return await _safe_ephemeral(interaction, "Not found.")
        if not doc.entrants:
            return await _safe_ephemeral(interaction, "No entrants to reroll.")
        winners = pick_winners(doc.entrants, doc.winners)
        doc.last_winners = winners
        await save_giveaway(self.bot, doc)
        await announce_winners(self.bot, doc, reroll=True)
        await update_giveaway_message(self.bot, doc)
        await _safe_ephemeral(interaction, "Rerolled winners.")


# ---------- Storage helpers ----------
async def save_giveaway(bot: commands.Bot, gw: Giveaway):
    if getattr(bot, "db", None):
        col = bot.db["giveaways"]
        await col.update_one(
            {"message_id": gw.message_id},
            {"$set": {
                "guild_id": gw.guild_id,
                "channel_id": gw.channel_id,
                "message_id": gw.message_id,
                "prize": gw.prize,
                "winners": gw.winners,
                "end_ts": gw.end_ts,
                "host_id": gw.host_id,
                "entrants": gw.entrants,
                "status": gw.status,
                "last_winners": gw.last_winners or [],
            }},
            upsert=True,
        )
    else:
        mem: Dict[int, Giveaway] = bot.__dict__.setdefault("_mem_giveaways", {})  # type: ignore
        mem[gw.message_id] = gw


async def get_giveaway_by_message(bot: commands.Bot, message_id: Optional[int]) -> Optional[Giveaway]:
    if not message_id:
        return None
    if getattr(bot, "db", None):
        col = bot.db["giveaways"]
        doc = await col.find_one({"message_id": message_id})
        if not doc:
            return None
        return Giveaway(**{
            "guild_id": doc["guild_id"],
            "channel_id": doc["channel_id"],
            "message_id": doc["message_id"],
            "prize": doc["prize"],
            "winners": doc["winners"],
            "end_ts": doc["end_ts"],
            "host_id": doc["host_id"],
            "entrants": list(doc.get("entrants", [])),
            "status": doc.get("status", "active"),
            "last_winners": list(doc.get("last_winners", [])),
        })
    # memory fallback
    mem: Dict[int, Giveaway] = bot.__dict__.get("_mem_giveaways", {})  # type: ignore
    return mem.get(message_id)


async def list_active_giveaways(bot: commands.Bot, guild_id: int) -> List[Giveaway]:
    out: List[Giveaway] = []
    if getattr(bot, "db", None):
        col = bot.db["giveaways"]
        async for doc in col.find({"guild_id": guild_id, "status": "active"}):
            out.append(Giveaway(**{
                "guild_id": doc["guild_id"],
                "channel_id": doc["channel_id"],
                "message_id": doc["message_id"],
                "prize": doc["prize"],
                "winners": doc["winners"],
                "end_ts": doc["end_ts"],
                "host_id": doc["host_id"],
                "entrants": list(doc.get("entrants", [])),
                "status": doc.get("status", "active"),
                "last_winners": list(doc.get("last_winners", [])),
            }))
        return out
    # memory fallback
    mem: Dict[int, Giveaway] = bot.__dict__.get("_mem_giveaways", {})  # type: ignore
    for g in mem.values():
        if g.guild_id == guild_id and g.status == "active":
            out.append(g)
    return out


# ---------- Mechanics ----------
def pick_winners(entrants: List[int], count: int) -> List[int]:
    if not entrants:
        return []
    pool = list(set(entrants))
    random.shuffle(pool)
    return pool[: max(1, count)]


async def update_giveaway_message(bot: commands.Bot, gw: Giveaway):
    guild = bot.get_guild(gw.guild_id)
    if not guild:
        return
    channel = guild.get_channel(gw.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        msg = await channel.fetch_message(gw.message_id)
    except discord.NotFound:
        return
    host = guild.get_member(gw.host_id) or guild.me
    e = build_gw_message_embed(gw.prize, gw.winners, gw.end_ts, host, entrants=len(gw.entrants), ended=(gw.status=="ended"), last_winners=gw.last_winners)
    try:
        await msg.edit(embed=e, view=GiveawayEntryView(bot, giveaway_message_id=gw.message_id))
    except Exception:
        pass


async def end_giveaway(bot: commands.Bot, gw: Giveaway):
    if gw.status == "ended":
        return
    gw.status = "ended"
    winners = pick_winners(gw.entrants, gw.winners)
    gw.last_winners = winners
    await save_giveaway(bot, gw)
    await announce_winners(bot, gw)
    await update_giveaway_message(bot, gw)


async def announce_winners(bot: commands.Bot, gw: Giveaway, *, reroll: bool = False):
    guild = bot.get_guild(gw.guild_id)
    if not guild:
        return
    channel = guild.get_channel(gw.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    if not gw.last_winners:
        await channel.send(f"🎉 Giveaway for **{gw.prize}** ended. No valid entries.")
        return
    mentions = " ".join(f"<@{uid}>" for uid in gw.last_winners)
    prefix = "🔁 Reroll!" if reroll else "🎉 Winners!"
    await channel.send(f"{prefix} **{gw.prize}** → {mentions}")


async def schedule_end_task(bot: commands.Bot, gw: Giveaway):
    # create a background task that sleeps until end time
    async def _runner():
        now = int(time.time())
        delay = max(0, gw.end_ts - now)
        try:
            await asyncio.sleep(delay)
            # reload doc in case entrants changed
            latest = await get_giveaway_by_message(bot, gw.message_id)
            if latest and latest.status == "active":
                await end_giveaway(bot, latest)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    # store tasks per-guild/message so we can avoid duplicates
    tasks: Dict[int, asyncio.Task] = bot.__dict__.setdefault("_gw_tasks", {})  # type: ignore
    # cancel any existing for same message
    if gw.message_id in tasks:
        try:
            tasks[gw.message_id].cancel()
        except Exception:
            pass
    tasks[gw.message_id] = asyncio.create_task(_runner())


# ---------- Cog ----------
class GiveawaysCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Persist panel view across restarts
        try:
            self.bot.add_view(GiveawaysPanelView(self.bot))
        except Exception:
            pass
        # Restore all active giveaways and attach entry views + schedules
        try:
            # We don't know all guilds/channels, but we can pull from DB/memory
            if getattr(self.bot, "db", None):
                col = self.bot.db["giveaways"]
                async for doc in col.find({"status": "active"}):
                    gw = Giveaway(**{
                        "guild_id": doc["guild_id"],
                        "channel_id": doc["channel_id"],
                        "message_id": doc["message_id"],
                        "prize": doc["prize"],
                        "winners": doc["winners"],
                        "end_ts": doc["end_ts"],
                        "host_id": doc["host_id"],
                        "entrants": list(doc.get("entrants", [])),
                        "status": doc.get("status", "active"),
                        "last_winners": list(doc.get("last_winners", [])),
                    })
                    self.bot.add_view(GiveawayEntryView(self.bot, giveaway_message_id=gw.message_id))
                    await schedule_end_task(self.bot, gw)
            else:
                mem: Dict[int, Giveaway] = self.bot.__dict__.get("_mem_giveaways", {})  # type: ignore
                for gw in mem.values():
                    if gw.status == "active":
                        self.bot.add_view(GiveawayEntryView(self.bot, giveaway_message_id=gw.message_id))
                        await schedule_end_task(self.bot, gw)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawaysCog(bot))
    try:
        bot.add_view(GiveawaysPanelView(bot))
    except Exception:
        pass
