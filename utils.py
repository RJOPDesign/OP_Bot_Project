"""
Utility helpers shared across cogs.

Provides:
- DEFAULT_CONFIG: base per-guild config structure
- ensure_guild_config(bot, guild_id): fetch/initialize config (Mongo if available)
- mk_embed(title, desc, color): consistent embeds with footer/timestamp
- is_admin(member): convenience
- admin_only(interaction): guard for admin-only UI actions

Notes
- If `bot.db` (Motor client) is not configured, an in-memory fallback is used
  so local/dev runs still work. Hosting should prefer Mongo for persistence.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional

import discord

# ----------------- Defaults -----------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "logging": {
        "enabled": True,
        "channel_id": None,
        "events": {
            "message_delete": True,
            "message_edit": True,
            "member_join": True,
            "member_leave": True,
        },
    },
    "tickets": {
        "enabled": True,
        "category_id": None,
    },
    "anti_raid": {
        "enabled": False,
        "join_gate": {"joins": 5, "seconds": 10},
    },
}


# ----------------- Persistence -----------------

async def ensure_guild_config(bot, guild_id: int) -> Dict[str, Any]:
    """Ensure a config document exists for the guild and return it.

    Tries MongoDB if available at `bot.db['configs']`. Falls back to
    a per-process memory dict (non-persistent), keyed by guild_id.
    """
    # Mongo path
    if getattr(bot, "db", None):
        col = bot.db["configs"]
        found = await col.find_one({"guild_id": guild_id})
        if found:
            # Backfill defaults in case of missing keys
            merged = _merge_with_defaults(found)
            if merged is not found:
                # Save back if we had to backfill missing keys
                await col.update_one({"guild_id": guild_id}, {"$set": merged}, upsert=True)
            return merged
        doc = {"guild_id": guild_id, **DEFAULT_CONFIG}
        await col.insert_one(doc)
        return doc

    # Memory fallback
    store: Dict[int, Dict[str, Any]] = bot.__dict__.setdefault("_mem_configs", {})  # type: ignore
    if guild_id in store:
        return _merge_with_defaults(store[guild_id])
    doc = {"guild_id": guild_id, **DEFAULT_CONFIG}
    store[guild_id] = doc
    return doc


def _merge_with_defaults(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow+nested merge to ensure missing keys are filled from defaults."""
    merged = {**doc}
    # logging
    log = {**DEFAULT_CONFIG["logging"], **(doc.get("logging") or {})}
    log["events"] = {**DEFAULT_CONFIG["logging"]["events"], **(log.get("events") or {})}
    merged["logging"] = log
    # tickets
    merged["tickets"] = {**DEFAULT_CONFIG["tickets"], **(doc.get("tickets") or {})}
    # anti_raid
    ar = {**DEFAULT_CONFIG["anti_raid"], **(doc.get("anti_raid") or {})}
    ar["join_gate"] = {**DEFAULT_CONFIG["anti_raid"]["join_gate"], **(ar.get("join_gate") or {})}
    merged["anti_raid"] = ar
    return merged


# ----------------- UI helpers -----------------

def mk_embed(title: str, desc: str = "", color: discord.Color = discord.Color.blurple()) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=color)
    e.set_footer(text="OP Control Panel")
    e.timestamp = dt.datetime.utcnow()
    return e


def is_admin(member: discord.Member) -> bool:
    """True if the member is a server admin.

    We check the Administrator permission explicitly. If you want to
    broaden this to Manage Guild/Moderate Members, adjust here.
    """
    try:
        return bool(member.guild_permissions.administrator)
    except Exception:
        return False


def admin_only(inter: discord.Interaction) -> bool:
    """Guard helper to use in button handlers.

    Returns True if the interaction user is a guild admin, False otherwise.
    Safe to call in DMs/edge cases (returns False).
    """
    try:
        user = inter.user  # type: ignore[attr-defined]
        if isinstance(user, discord.Member):
            return is_admin(user)
        return False
    except Exception:
        return False
