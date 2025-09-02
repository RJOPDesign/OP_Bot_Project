import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Select, ChannelSelect as DiscordChannelSelect
import datetime
import logging

# Import from the utils cog for consistency
from .utils import BaseSettingsView, create_embed, STRINGS

logger = logging.getLogger(__name__)

# --- UI Views ---

class LogEventSelect(Select):
    def __init__(self, bot: commands.Bot, guild_config: dict):
        self.bot = bot
        log_config = guild_config.get("logging", {})
        current_events = log_config.get("enabled_events", [])
        options = [
            discord.SelectOption(label="Message Deletions", value="message_delete", default="message_delete" in current_events),
            discord.SelectOption(label="Message Edits", value="message_edit", default="message_edit" in current_events),
            discord.SelectOption(label="Member Joins", value="member_join", default="member_join" in current_events),
            discord.SelectOption(label="Member Leaves", value="member_leave", default="member_leave" in current_events),
            discord.SelectOption(label="Member Bans", value="member_ban", default="member_ban" in current_events),
            discord.SelectOption(label="Member Unbans", value="member_unban", default="member_unban" in current_events),
            discord.SelectOption(label="Nickname Changes", value="member_nick_update", default="member_nick_update" in current_events),
            discord.SelectOption(label="Role Updates", value="member_role_update", default="member_role_update" in current_events),
            discord.SelectOption(label="Channel Events", value="channel_events", default="channel_events" in current_events),
        ]
        super().__init__(placeholder="Select events to log...", min_values=0, max_values=len(options), options=options, custom_id="log_event_selector")

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await self.bot.db.configs.update_one({"guild_id": interaction.guild.id}, {"$set": {"logging.enabled_events": self.values}}, upsert=True)
            await interaction.response.send_message(f"✅ Log events updated! Now logging **{len(self.values)}** types of events.", ephemeral=True)
        except Exception as e:
            logger.error(f"DB update failed for guild {interaction.guild.id}: {e}")
            await interaction.response.send_message(STRINGS.get("db_error", "A database error occurred."), ephemeral=True)

class LoggingSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        log_config = self.config.get("logging", {})
        is_enabled = log_config.get("enabled", False)
        
        back_button = Button(label="◀️ Back", custom_id="logging:nav_staff")
        back_button.callback = self.go_to_staff_menu
        self.add_item(back_button)

        toggle_button = Button(label=f"Logging is {'ON' if is_enabled else 'OFF'}", style=discord.ButtonStyle.green if is_enabled else discord.ButtonStyle.red, custom_id="logging:toggle")
        toggle_button.callback = self.toggle_logging
        self.add_item(toggle_button)

        channel_select = DiscordChannelSelect(placeholder="Select a log channel...", custom_id="logging:set_channel")
        channel_select.callback = self.set_log_channel
        self.add_item(channel_select)
        
        self.add_item(LogEventSelect(self.bot, self.config))

    async def go_to_staff_menu(self, interaction: discord.Interaction) -> None:
        from .menu_cog import StaffMenuView
        embed = create_embed("🛡️ Staff Menu", "Select a category to configure.", discord.Color.blue())
        await interaction.response.edit_message(embed=embed, view=StaffMenuView(self.bot))

    async def toggle_logging(self, interaction: discord.Interaction) -> None:
        new_status = not self.config.get("logging", {}).get("enabled", False)
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"logging.enabled": new_status}}, upsert=True)
        config = await self.bot.get_guild_config(self.guild_id)
        view = LoggingSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        view.message = await interaction.original_response()
        await interaction.followup.send(f"✅ Logging turned {'ON' if new_status else 'OFF'}.", ephemeral=True)

    async def set_log_channel(self, interaction: discord.Interaction):
        channel_id = int(interaction.data["values"][0])
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"logging.log_channel_id": channel_id}}, upsert=True)
        await interaction.response.send_message(f"✅ Log channel set to <#{channel_id}>!", ephemeral=True)


# --- Main Cog ---

class LoggingCog(commands.Cog):
    """A cog for logging various server events to a designated channel."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send_log(self, guild_id: int, event_name: str, embed: discord.Embed):
        """A robust helper function to check configuration and send a log message."""
        try:
            config = await self.bot.get_guild_config(guild_id)
            if not config:
                logger.warning(f"No config found for guild {guild_id} in _send_log")
                return
        except Exception as e:
            logger.error(f"Error fetching guild config for {guild_id}: {e}")
            return

        log_config = config.get("logging", {})
        
        if not log_config.get("enabled") or event_name not in log_config.get("enabled_events", []):
            return
            
        log_channel_id = log_config.get("log_channel_id")
        if not log_channel_id:
            return
            
        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel or not isinstance(log_channel, discord.TextChannel):
            logger.warning(f"Log channel {log_channel_id} not found or is not a text channel in guild {guild_id}.")
            return

        guild = self.bot.get_guild(guild_id)
        if not guild: return

        required_perms = discord.Permissions(send_messages=True, embed_links=True)
        if not log_channel.permissions_for(guild.me).is_superset(required_perms):
            logger.warning(f"Missing permissions (Send Messages, Embed Links) in log channel {log_channel_id} for guild {guild_id}.")
            return

        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            logger.error(f"Forbidden to send log to channel {log_channel_id} in guild {guild_id}.")
        except discord.HTTPException as e:
            logger.error(f"HTTP error sending log to channel {log_channel_id}: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred in _send_log for guild {guild_id}: {e}")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Logs when a message is deleted."""
        if not message.guild or message.author.bot:
            return

        embed = discord.Embed(
            color=discord.Color.red(),
            description=f"**Message sent by {message.author.mention} deleted in {message.channel.mention}**",
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        if message.content:
            embed.add_field(name="Content", value=f"```{message.content[:1020]}```", inline=False)
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.set_footer(text=f"Author ID: {message.author.id} | Message ID: {message.id}")
        await self._send_log(message.guild.id, "message_delete", embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Logs when a message is edited."""
        if not before.guild or before.author.bot or before.content == after.content:
            return

        embed = discord.Embed(
            color=discord.Color.orange(),
            description=f"**Message edited in {before.channel.mention}** [Jump to Message]({after.jump_url})",
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        if before.content:
            embed.add_field(name="Before", value=f"```{before.content[:1020]}```", inline=False)
        if after.content:
            embed.add_field(name="After", value=f"```{after.content[:1020]}```", inline=False)
        embed.set_author(name=str(before.author), icon_url=before.author.display_avatar.url)
        embed.set_footer(text=f"Author ID: {before.author.id} | Message ID: {before.id}")
        await self._send_log(before.guild.id, "message_edit", embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Logs when a member joins the server."""
        embed = discord.Embed(
            color=discord.Color.green(),
            description=f"{member.mention} **joined the server**",
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, style='R'))
        embed.set_footer(text=f"User ID: {member.id}")
        await self._send_log(member.guild.id, "member_join", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Logs when a member leaves the server."""
        embed = discord.Embed(
            color=discord.Color.dark_red(),
            description=f"{member.mention} **left the server**",
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_footer(text=f"User ID: {member.id}")
        await self._send_log(member.guild.id, "member_leave", embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        """Logs when a member is banned."""
        embed = discord.Embed(
            color=discord.Color.from_rgb(0,0,0),
            description=f"{user.mention} **was banned from the server**",
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_footer(text=f"User ID: {user.id}")
        await self._send_log(guild.id, "member_ban", embed)
        
    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        """Logs when a member is unbanned."""
        embed = discord.Embed(
            color=discord.Color.from_rgb(173, 216, 230),
            description=f"{user.mention} **was unbanned from the server**",
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_footer(text=f"User ID: {user.id}")
        await self._send_log(guild.id, "member_unban", embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Logs nickname and role changes."""
        # Nickname changes
        if before.nick != after.nick:
            embed = discord.Embed(
                color=discord.Color.blue(),
                description=f"**{after.mention}'s nickname was changed**",
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.add_field(name="Before", value=before.nick or "None", inline=False)
            embed.add_field(name="After", value=after.nick or "None", inline=False)
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embed.set_footer(text=f"User ID: {after.id}")
            await self._send_log(after.guild.id, "member_nick_update", embed)
        
        # Role changes
        if before.roles != after.roles:
            added_roles = [r.mention for r in after.roles if r not in before.roles]
            removed_roles = [r.mention for r in before.roles if r not in after.roles]
            if not added_roles and not removed_roles: return
            
            embed = discord.Embed(
                color=discord.Color.purple(),
                description=f"**{after.mention}'s roles were updated**",
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            # Truncate role lists to prevent exceeding embed limits
            if added_roles:
                embed.add_field(name="Added Roles", value=", ".join(added_roles)[:1020], inline=False)
            if removed_roles:
                embed.add_field(name="Removed Roles", value=", ".join(removed_roles)[:1020], inline=False)
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embed.set_footer(text=f"User ID: {after.id}")
            await self._send_log(after.guild.id, "member_role_update", embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        """Logs when a channel is created."""
        embed = discord.Embed(
            color=discord.Color.teal(),
            description=f"**Channel Created: #{channel.name}**",
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_footer(text=f"Channel ID: {channel.id}")
        await self._send_log(channel.guild.id, "channel_events", embed)
        
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """Logs when a channel is deleted."""
        embed = discord.Embed(
            color=discord.Color.dark_teal(),
            description=f"**Channel Deleted: #{channel.name}**",
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_footer(text=f"Channel ID: {channel.id}")
        await self._send_log(channel.guild.id, "channel_events", embed)

async def setup(bot: commands.Bot):
    """Adds the cog to the bot."""
    await bot.add_cog(LoggingCog(bot))
