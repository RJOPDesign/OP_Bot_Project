"""
Ticket cog — menu-only ticketing with persistent buttons and Mongo persistence.

Features
- Settings panel: enable/disable tickets, choose a Category for ticket channels.
- Public ticket panel: "Create Ticket" button for members (ephemeral success).
- Creates a private text channel under the configured Category with proper perms.
- Ticket actions view inside each ticket:
  • Close (locks channel, sets status)
  • Claim (marks staff member)
  • Add Member (grant view/send)
  • Rename (rename ticket channel)
  • Transcript (posts a .txt file of recent messages)
  • Delete (admin only)
- Resilient interaction handling (no "Interaction Failed").
- Persistent views with stable custom_ids.
- Mongo-backed if `bot.db` is available; in-memory fallback otherwise.

Collections
- configs: stores tickets.enabled + tickets.category_id (via utils.ensure_guild_config)
- tickets: one doc per ticket channel
  {
    guild_id, channel_id, owner_id, status: 'open'|'closed',
    created_ts, claimed_by, members: [user_ids]
  }
"""
from __future__ import annotations

import io
import time
from typing import Optional, Dict, Any, List

import discord
from discord.ext import commands

from utils import mk_embed, admin_only, ensure_guild_config


# --------------- Settings / Embeds ---------------

def build_ticket_embed(guild: discord.Guild) -> discord.Embed:
    return mk_embed(
        "Tickets",
        (
            "Create and manage private support tickets.\n\n"
            "• Configure a Category in Settings.\n"
            "• Members click *Create Ticket* to open a private channel.\n"
            "• Staff manage with the actions bar inside the ticket."
        ),
    )


async def get_ticket_settings(bot: commands.Bot, guild_id: int) -> Dict[str, Any]:
    cfg = await ensure_guild_config(bot, guild_id)
    t = cfg.get("tickets") or {}
    return {
        "enabled": bool(t.get("enabled", True)),
        "category_id": t.get("category_id"),
    }


async def save_ticket_settings(bot: commands.Bot, guild_id: int, new_cfg: Dict[str, Any]):
    base = await ensure_guild_config(bot, guild_id)
    merged = {
        **base,
        "tickets": {
            "enabled": bool(new_cfg.get("enabled", True)),
            "category_id": new_cfg.get("category_id"),
        },
    }
    if getattr(bot, "db", None):
        await bot.db["configs"].update_one({"guild_id": guild_id}, {"$set": merged}, upsert=True)
    else:
        store = bot.__dict__.setdefault("_mem_configs", {})
        store[guild_id] = merged


# --------------- Settings View ---------------
class TicketSettingsView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _refresh(self, interaction: discord.Interaction):
        e = await self._embed(interaction.guild)
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=e, view=self)
            else:
                await interaction.response.edit_message(embed=e, view=self)
        except Exception:
            pass

    async def _embed(self, guild: discord.Guild) -> discord.Embed:
        s = await get_ticket_settings(self.bot, guild.id)
        cat_text = f"<#{s['category_id']}>" if s.get("category_id") else "Not set"
        desc = [
            f"**Status:** {'ON' if s.get('enabled') else 'OFF'}",
            f"**Category:** {cat_text}",
            "Post the public panel in any channel to let users create tickets.",
        ]
        return mk_embed("Ticket Settings", "\n".join(desc))

    @discord.ui.button(label="Toggle On/Off", style=discord.ButtonStyle.primary, custom_id="op:tickets:toggle")
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        s = await get_ticket_settings(self.bot, interaction.guild.id)
        s["enabled"] = not s.get("enabled", True)
        await save_ticket_settings(self.bot, interaction.guild.id, s)
        await _safe_ephemeral(interaction, f"Tickets turned {'ON' if s['enabled'] else 'OFF'}.")
        await self._refresh(interaction)

    @discord.ui.button(label="Set Category", style=discord.ButtonStyle.secondary, custom_id="op:tickets:setcat")
    async def set_category(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        await _present_category_picker(self.bot, interaction)

    @discord.ui.button(label="Post Public Panel", style=discord.ButtonStyle.secondary, custom_id="op:tickets:postpanel")
    async def post_public(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        try:
            await interaction.channel.send(embed=build_ticket_embed(interaction.guild), view=TicketPanelView(self.bot))
            await _safe_ephemeral(interaction, "Ticket panel posted.")
        except Exception:
            await _safe_ephemeral(interaction, "Failed to post panel (check permissions).")

    @discord.ui.button(label="Back", style=discord.ButtonStyle.danger, custom_id="op:tickets:back")
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button):
        from cogs.menu_cog import MainMenuView, build_main_embed
        try:
            await interaction.response.edit_message(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
        except Exception:
            try:
                await interaction.edit_original_response(embed=build_main_embed(interaction.guild), view=MainMenuView(self.bot))
            except Exception:
                pass


class CategorySelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(channel_types=[discord.ChannelType.category], placeholder="Choose a ticket Category…", min_values=1, max_values=1, custom_id="op:tickets:pickcat")


class CategoryPickView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=60)
        self.bot = bot
        self.add_item(CategorySelect())

    @discord.ui.button(label="Save", style=discord.ButtonStyle.primary, custom_id="op:tickets:savecat")
    async def save(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not admin_only(interaction):
            return await _safe_ephemeral(interaction, "Admins only.")
        sel: CategorySelect = next((c for c in self.children if isinstance(c, CategorySelect)), None)
        if not sel or not sel.values:
            return await _safe_ephemeral(interaction, "Pick a category first.")
        cat_id = int(sel.values[0])
        s = await get_ticket_settings(self.bot, interaction.guild.id)
        s["category_id"] = cat_id
        await save_ticket_settings(self.bot, interaction.guild.id, s)
        await _safe_ephemeral(interaction, f"Ticket category set to <#{cat_id}>.")


async def _present_category_picker(bot: commands.Bot, interaction: discord.Interaction):
    try:
        await interaction.response.send_message("Select a category for new tickets:", view=CategoryPickView(bot), ephemeral=True)
    except Exception:
        pass


# --------------- Public Panel ---------------
class TicketPanelView(discord.ui.View):
    """Public-facing panel users click to open a ticket."""

    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="op:tickets:create")
    async def create_ticket(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = await get_ticket_settings(self.bot, interaction.guild.id)
        if not s.get("enabled", True):
            return await _safe_ephemeral(interaction, "Tickets are currently disabled.")
        if not s.get("category_id"):
            return await _safe_ephemeral(interaction, "Tickets category is not set.")
        category = interaction.guild.get_channel(s["category_id"])  # type: ignore
        if not isinstance(category, discord.CategoryChannel):
            return await _safe_ephemeral(interaction, "Configured category not found.")

        # Permissions: Channel visible to requester and admins only
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
        }
        try:
            ch = await category.create_text_channel(
                name=f"ticket-{interaction.user.name[:12]}-{interaction.user.discriminator}",
                overwrites=overwrites,
                topic=f"Support ticket for {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            return await _safe_ephemeral(interaction, "I lack permission to create channels in that category.")
        except Exception:
            return await _safe_ephemeral(interaction, "Failed to create ticket channel.")

        # Persist ticket
        doc = {
            "guild_id": interaction.guild.id,
            "channel_id": ch.id,
            "owner_id": interaction.user.id,
            "status": "open",
            "created_ts": int(time.time()),
            "claimed_by": None,
            "members": [interaction.user.id],
        }
        await save_ticket_doc(self.bot, doc)

        # Post actions view
        await ch.send(content=f"Welcome {interaction.user.mention}! A staff member will be with you shortly.")
        await ch.send(embed=mk_embed("Ticket Actions", "Use the buttons below to manage this ticket."), view=TicketActionsView(self.bot, ticket_channel_id=ch.id))
        await _safe_ephemeral(interaction, f"Ticket created: {ch.mention}")


# --------------- Ticket Actions ---------------
class TicketActionsView(discord.ui.View):
    def __init__(self, bot: commands.Bot, *, ticket_channel_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_channel_id = ticket_channel_id

    def _resolve_ticket_channel(self, interaction: discord.Interaction) -> Optional[discord.TextChannel]:
        ch = interaction.channel
        if isinstance(ch, discord.TextChannel):
            return ch
        if self.ticket_channel_id:
            g = interaction.guild
            if g:
                found = g.get_channel(self.ticket_channel_id)
                if isinstance(found, discord.TextChannel):
                    return found
        return None

    async def _load_doc(self, channel_id: int) -> Optional[Dict[str, Any]]:
        return await get_ticket_doc(self.bot, channel_id)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, custom_id="op:ticket:close")
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return await _safe_ephemeral(interaction, "Guild only.")
        ch = self._resolve_ticket_channel(interaction)
        if not ch:
            return await _safe_ephemeral(interaction, "Could not resolve ticket channel.")
        doc = await self._load_doc(ch.id)
        if not doc:
            return await _safe_ephemeral(interaction, "Ticket record not found.")
        # Allow owner or admin to close
        if interaction.user.id != doc["owner_id"] and not interaction.user.guild_permissions.administrator:
            return await _safe_ephemeral(interaction, "Only the ticket owner or an admin can close this ticket.")
        try:
            await ch.set_permissions(interaction.guild.default_role, view_channel=False)
            await ch.edit(name=f"closed-{ch.name}")
        except Exception:
            pass
        doc["status"] = "closed"
        await save_ticket_doc(self.bot, doc)
        await _safe_ephemeral(interaction, "Ticket closed.")

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, custom_id="op:ticket:claim")
    async def claim(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return await _safe_ephemeral(interaction, "Guild only.")
        ch = self._resolve_ticket_channel(interaction)
        if not ch:
            return await _safe_ephemeral(interaction, "Could not resolve ticket channel.")
        doc = await self._load_doc(ch.id)
        if not doc:
            return await _safe_ephemeral(interaction, "Ticket record not found.")
        doc["claimed_by"] = interaction.user.id
        await save_ticket_doc(self.bot, doc)
        await ch.send(f"✅ Ticket claimed by {interaction.user.mention}")
        await _safe_ephemeral(interaction, "Claim recorded.")

    @discord.ui.button(label="Add Member", style=discord.ButtonStyle.secondary, custom_id="op:ticket:add")
    async def add_member(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return await _safe_ephemeral(interaction, "Guild only.")
        await interaction.response.send_modal(AddMemberModal(self.bot, self.ticket_channel_id))

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, custom_id="op:ticket:rename")
    async def rename(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(RenameTicketModal(self.bot, self.ticket_channel_id))

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.secondary, custom_id="op:ticket:transcript")
    async def transcript(self, interaction: discord.Interaction, _: discord.ui.Button):
        ch = self._resolve_ticket_channel(interaction)
        if not ch:
            return await _safe_ephemeral(interaction, "Could not resolve ticket channel.")
        buf = io.StringIO()
        async for m in ch.history(limit=1000, oldest_first=True):
            ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else ""
            author = f"{m.author} ({m.author.id})"
            content = m.content or ""
            buf.write(f"[{ts}] {author}: {content}\n")
        f = discord.File(io.BytesIO(buf.getvalue().encode("utf-8")), filename=f"transcript-{ch.id}.txt")
        await _safe_ephemeral(interaction, "Transcript generated — uploading…")
        try:
            await ch.send(file=f)
        except Exception:
            pass

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="op:ticket:delete")
    async def delete(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            return await _safe_ephemeral(interaction, "Admins only.")
        ch = self._resolve_ticket_channel(interaction)
        if not ch:
            return await _safe_ephemeral(interaction, "Could not resolve ticket channel.")
        try:
            await ch.delete(reason=f"Deleted by {interaction.user}")
        except Exception:
            return await _safe_ephemeral(interaction, "Failed to delete channel.")
        # Mark as closed in DB
        doc = await self._load_doc(ch.id)
        if doc:
            doc["status"] = "closed"
            await save_ticket_doc(self.bot, doc)


class AddMemberModal(discord.ui.Modal, title="Add Member to Ticket"):
    user_id = discord.ui.TextInput(label="User ID or mention", placeholder="123456789012345678", required=True, max_length=40)

    def __init__(self, bot: commands.Bot, ticket_channel_id: Optional[int]):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_channel_id = ticket_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        ch = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        if not ch and self.ticket_channel_id and interaction.guild:
            maybe = interaction.guild.get_channel(self.ticket_channel_id)
            if isinstance(maybe, discord.TextChannel):
                ch = maybe
        if not ch:
            return await _safe_ephemeral(interaction, "Could not resolve ticket channel.")
        m = None
        import re as _re
        mm = _re.search(r"(\d{15,25})", str(self.user_id))
        if mm:
            uid = int(mm.group(1))
            m = interaction.guild.get_member(uid) if interaction.guild else None
        if not m:
            return await _safe_ephemeral(interaction, "Could not resolve that user.")
        try:
            await ch.set_permissions(m, view_channel=True, send_messages=True, read_message_history=True)
        except Exception:
            return await _safe_ephemeral(interaction, "Failed to set permissions.")
        # update doc
        doc = await get_ticket_doc(self.bot, ch.id)
        if doc:
            mems = set(doc.get("members", []))
            mems.add(m.id)
            doc["members"] = list(mems)
            await save_ticket_doc(self.bot, doc)
        await _safe_ephemeral(interaction, f"Added {m.mention} to this ticket.")


class RenameTicketModal(discord.ui.Modal, title="Rename Ticket"):
    name = discord.ui.TextInput(label="New name", placeholder="ticket-billing-issue", required=True, max_length=90)

    def __init__(self, bot: commands.Bot, ticket_channel_id: Optional[int]):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_channel_id = ticket_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        ch = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        if not ch and self.ticket_channel_id and interaction.guild:
            maybe = interaction.guild.get_channel(self.ticket_channel_id)
            if isinstance(maybe, discord.TextChannel):
                ch = maybe
        if not ch:
            return await _safe_ephemeral(interaction, "Could not resolve ticket channel.")
        try:
            await ch.edit(name=str(self.name).strip())
            await _safe_ephemeral(interaction, "Ticket renamed.")
        except Exception:
            await _safe_ephemeral(interaction, "Failed to rename ticket.")


# --------------- Storage ---------------
async def save_ticket_doc(bot: commands.Bot, doc: Dict[str, Any]):
    if getattr(bot, "db", None):
        await bot.db["tickets"].update_one({"channel_id": doc["channel_id"]}, {"$set": doc}, upsert=True)
    else:
        mem = bot.__dict__.setdefault("_mem_tickets", {})
        mem[doc["channel_id"]] = doc


async def get_ticket_doc(bot: commands.Bot, channel_id: int) -> Optional[Dict[str, Any]]:
    if getattr(bot, "db", None):
        doc = await bot.db["tickets"].find_one({"channel_id": channel_id})
        return doc
    mem = bot.__dict__.get("_mem_tickets", {})
    return mem.get(channel_id)


# --------------- Cog ---------------
class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Persistent views
        try:
            self.bot.add_view(TicketPanelView(self.bot))
            self.bot.add_view(TicketActionsView(self.bot))
            self.bot.add_view(TicketSettingsView(self.bot))
        except Exception:
            pass
        # Reattach actions view to existing open ticket channels
        try:
            if getattr(self.bot, "db", None):
                async for doc in self.bot.db["tickets"].find({"status": "open"}):
                    guild = self.bot.get_guild(doc["guild_id"]) 
                    if not guild:
                        continue
                    ch = guild.get_channel(doc["channel_id"]) 
                    if isinstance(ch, discord.TextChannel):
                        try:
                            await ch.send(embed=mk_embed("Ticket Actions", "Use the buttons below to manage this ticket."), view=TicketActionsView(self.bot, ticket_channel_id=ch.id))
                        except Exception:
                            pass
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
    try:
        bot.add_view(TicketPanelView(bot))
        bot.add_view(TicketActionsView(bot))
        bot.add_view(TicketSettingsView(bot))
    except Exception:
        pass


# --------------- Utilities ---------------
async def _safe_ephemeral(interaction: discord.Interaction, content: str):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        pass
