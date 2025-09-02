import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import View, Button, Select, Modal, TextInput, ChannelSelect as DiscordChannelSelect, RoleSelect
from typing import Optional, List, Any
import logging
import uuid
import time
import datetime
import re
import random

# Import from the utils cog for consistency
from .utils import BaseSettingsView, create_embed, STRINGS

logger = logging.getLogger(__name__)

# --- Reusable Modals ---

class MessageModal(Modal):
    """A reusable modal for setting a custom message in the database."""
    def __init__(self, bot: commands.Bot, title: str, db_key: str, current_message: str):
        super().__init__(title=title)
        self.bot = bot
        self.db_key = db_key
        self.message_input = TextInput(
            label="Custom Message",
            style=discord.TextStyle.paragraph,
            default=current_message,
            placeholder="Use {user}, {server}, {count}, {level}",
            max_length=1500,
            required=True
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.bot.db.configs.update_one(
                {"guild_id": interaction.guild.id},
                {"$set": {self.db_key: self.message_input.value}},
                upsert=True
            )
            logger.info(f"'{self.db_key}' updated for guild {interaction.guild.id} by {interaction.user}")
            await interaction.response.send_message("✅ Message updated successfully!", ephemeral=True)
        except Exception as e:
            logger.error(f"DB update failed for guild {interaction.guild.id}: {e}")
            await interaction.response.send_message(STRINGS.get("db_error", "A database error occurred."), ephemeral=True)

class SetBirthdayModal(Modal, title="Set Your Birthday"):
    """A modal for users to set their birthday."""
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.month = TextInput(label="Month (1-12)", placeholder="e.g., 7", min_length=1, max_length=2)
        self.day = TextInput(label="Day (1-31)", placeholder="e.g., 15", min_length=1, max_length=2)
        self.add_item(self.month)
        self.add_item(self.day)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            month = int(self.month.value)
            day = int(self.day.value)
            if not (1 <= month <= 12 and 1 <= day <= 31):
                raise ValueError("Invalid date")
            
            await self.bot.db.birthdays.update_one(
                {"user_id": interaction.user.id, "guild_id": interaction.guild.id},
                {"$set": {"month": month, "day": day}},
                upsert=True
            )
            
            await interaction.response.send_message(f"✅ Your birthday has been set to {month}/{day}!", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid month (1-12) and day (1-31).", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to set birthday for user {interaction.user.id}: {e}")
            await interaction.response.send_message(STRINGS.get("db_error", "A database error occurred."), ephemeral=True)


class ReactionRoleNameModal(Modal, title="New Reaction Role Panel"):
    """A modal to get the name for a new reaction role setup."""
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.panel_name_input = TextInput(label="Panel Name", placeholder="A unique name for this panel (e.g., Color Roles)", required=True, max_length=50)
        self.add_item(self.panel_name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        panel_id = str(uuid.uuid4())
        panel_name = self.panel_name_input.value
        
        new_panel = {
            "panel_id": panel_id,
            "name": panel_name,
            "message_id": None,
            "channel_id": None,
            "message_content": "React to get your roles!",
            "roles": {} # emoji: role_id
        }

        try:
            await self.bot.db.configs.update_one(
                {"guild_id": interaction.guild.id},
                {"$push": {"reaction_roles": new_panel}},
                upsert=True
            )
            embed = create_embed(f"🔧 Configuring '{panel_name}'", "Add roles and set a message, then post it to a channel.", discord.Color.orange())
            view = ConfigureReactionRoleView(self.bot, panel_id)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Failed to create reaction role panel for guild {interaction.guild.id}: {e}")
            await interaction.response.send_message(STRINGS.get("db_error", "A database error occurred."), ephemeral=True)


class CreatePollModal(Modal, title="Create a New Poll"):
    """A modal for creating a new poll."""
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.question = TextInput(label="Poll Question", placeholder="What should we do for the next event?", required=True, max_length=256)
        self.choice1 = TextInput(label="Choice 1", placeholder="e.g., Movie Night", required=True, max_length=100)
        self.choice2 = TextInput(label="Choice 2", placeholder="e.g., Game Tournament", required=True, max_length=100)
        self.choice3 = TextInput(label="Choice 3 (Optional)", required=False, max_length=100)
        self.choice4 = TextInput(label="Choice 4 (Optional)", required=False, max_length=100)
        self.add_item(self.question)
        self.add_item(self.choice1)
        self.add_item(self.choice2)
        self.add_item(self.choice3)
        self.add_item(self.choice4)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        choices = [c.value for c in [self.choice1, self.choice2, self.choice3, self.choice4] if c.value]
        
        view = PostPollView(self.bot, self.question.value, choices)
        await interaction.response.send_message("Where would you like to post this poll?", view=view, ephemeral=True)

class CreateEmbedModal(Modal, title="Create a Custom Embed"):
    """A modal for creating a new embed message."""
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.title = TextInput(label="Embed Title", required=True, max_length=256)
        self.description = TextInput(label="Embed Description", style=discord.TextStyle.paragraph, required=True, max_length=2000)
        self.color = TextInput(label="Color (Hex Code)", placeholder="e.g., #FF5733 or FF5733", required=False, max_length=7)
        self.footer = TextInput(label="Footer Text (Optional)", required=False, max_length=100)
        self.add_item(self.title)
        self.add_item(self.description)
        self.add_item(self.color)
        self.add_item(self.footer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        color_value = self.color.value.strip().lstrip('#')
        try:
            color = discord.Color(int(color_value, 16)) if color_value else discord.Color.default()
        except ValueError:
            await interaction.response.send_message("❌ Invalid hex color code. Please use a valid format (e.g., #FF5733).", ephemeral=True)
            return
            
        embed_data = {
            "title": self.title.value,
            "description": self.description.value,
            "color": color.value, # Store as integer
            "footer": self.footer.value
        }
        
        view = PostEmbedView(self.bot, embed_data)
        await interaction.response.send_message("Where would you like to post this embed?", view=view, ephemeral=True)

class CreateReminderModal(Modal, title="Create a New Reminder"):
    """A modal for creating a new reminder."""
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.message = TextInput(label="Reminder Message", style=discord.TextStyle.paragraph, required=True, max_length=1500)
        self.delay = TextInput(label="Time Delay", placeholder="e.g., 10m, 2h, 1d", required=True, max_length=10)
        self.add_item(self.message)
        self.add_item(self.delay)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        delay_str = self.delay.value.lower()
        match = re.match(r"(\d+)([mhd])", delay_str)
        if not match:
            await interaction.response.send_message("❌ Invalid time format. Use 'm' for minutes, 'h' for hours, or 'd' for days (e.g., 10m, 2h, 1d).", ephemeral=True)
            return
        
        value, unit = int(match.group(1)), match.group(2)
        if unit == 'm':
            delta = datetime.timedelta(minutes=value)
        elif unit == 'h':
            delta = datetime.timedelta(hours=value)
        else: # 'd'
            delta = datetime.timedelta(days=value)
            
        due_time = datetime.datetime.now(datetime.timezone.utc) + delta
        
        reminder_data = {
            "message": self.message.value,
            "due_time": due_time,
            "author_id": interaction.user.id
        }
        
        view = PostReminderView(self.bot, reminder_data)
        await interaction.response.send_message("Where should this reminder be sent?", view=view, ephemeral=True)

class AddSocialAlertModal(Modal, title="Add Social Media Alert"):
    """A modal for adding a new social media alert."""
    def __init__(self, bot: commands.Bot, platform: str):
        super().__init__()
        self.bot = bot
        self.platform = platform
        self.username = TextInput(label=f"{platform} Username/Channel ID", required=True, max_length=100)
        self.add_item(self.username)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = PostSocialAlertView(self.bot, self.platform, self.username.value)
        await interaction.response.send_message("Which channel should be notified?", view=view, ephemeral=True)

class CreateAchievementModal(Modal, title="Create a New Achievement"):
    """A modal for creating a new achievement."""
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.name = TextInput(label="Achievement Name", required=True, max_length=100)
        self.description = TextInput(label="Description", style=discord.TextStyle.paragraph, required=True, max_length=256)
        self.add_item(self.name)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        achievement_id = str(uuid.uuid4())
        
        new_achievement = {
            "achievement_id": achievement_id,
            "name": self.name.value,
            "description": self.description.value,
            "trigger_type": "messages_sent", # Placeholder for now
            "trigger_value": 100 # Placeholder for now
        }

        await self.bot.db.configs.update_one(
            {"guild_id": interaction.guild.id},
            {"$push": {"achievements": new_achievement}},
            upsert=True
        )
        
        await interaction.response.send_message(f"✅ Achievement '{self.name.value}' created!", ephemeral=True)

# --- Post-Creation Views (Moved to top to fix reference errors) ---

class PostPollView(View):
    """A view with a channel select to post a newly created poll."""
    def __init__(self, bot: commands.Bot, question: str, choices: List[str]):
        super().__init__(timeout=180)
        self.bot = bot
        self.question = question
        self.choices = choices
        
        self.channel_selector = DiscordChannelSelect(placeholder="Select channel to post poll in...")
        self.channel_selector.callback = self.post_poll
        self.add_item(self.channel_selector)

    async def post_poll(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected_channel_id = int(self.channel_selector.values[0].id)
        target_channel = await self.bot.fetch_channel(selected_channel_id)

        if not target_channel:
            return await interaction.followup.send("❌ Could not find the selected channel.", ephemeral=True)

        embed = discord.Embed(title=f"📊 {self.question}", color=discord.Color.blurple())
        view = PollVotingView(self.choices)
        
        await target_channel.send(embed=embed, view=view)
        await interaction.followup.send(f"✅ Poll posted in {target_channel.mention}!", ephemeral=True)

class PostEmbedView(View):
    """A view with a channel select to post a newly created embed."""
    def __init__(self, bot: commands.Bot, embed_data: dict):
        super().__init__(timeout=180)
        self.bot = bot
        self.embed_data = embed_data
        
        channel_selector = DiscordChannelSelect(placeholder="Select channel to post embed in...")
        channel_selector.callback = self.post_embed
        self.add_item(channel_selector)

    async def post_embed(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        target_channel_id = int(interaction.data["values"][0])
        target_channel = await self.bot.fetch_channel(target_channel_id)
        
        embed = discord.Embed(
            title=self.embed_data["title"],
            description=self.embed_data["description"],
            color=self.embed_data["color"]
        )
        if self.embed_data["footer"]:
            embed.set_footer(text=self.embed_data["footer"])
        
        await target_channel.send(embed=embed)
        await interaction.followup.send(f"✅ Embed posted in {target_channel.mention}!", ephemeral=True)

class PostReminderView(View):
    """A view with a channel select to post a newly created reminder."""
    def __init__(self, bot: commands.Bot, reminder_data: dict):
        super().__init__(timeout=180)
        self.bot = bot
        self.reminder_data = reminder_data
        
        channel_selector = DiscordChannelSelect(placeholder="Select channel to send reminder in...")
        channel_selector.callback = self.post_reminder
        self.add_item(channel_selector)

    async def post_reminder(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        target_channel_id = int(interaction.data["values"][0])
        
        reminder_doc = {
            "reminder_id": str(uuid.uuid4()),
            "guild_id": interaction.guild.id,
            "channel_id": target_channel_id,
            "message": self.reminder_data["message"],
            "due_time": self.reminder_data["due_time"],
            "author_id": self.reminder_data["author_id"]
        }

        await self.bot.db.reminders.insert_one(reminder_doc)
        await interaction.followup.send(f"✅ Reminder set for <#{target_channel_id}>!", ephemeral=True)

class PostSocialAlertView(View):
    """A view with a channel select to post a new social media alert."""
    def __init__(self, bot: commands.Bot, platform: str, username: str):
        super().__init__(timeout=180)
        self.bot = bot
        self.platform = platform
        self.username = username
        
        channel_selector = DiscordChannelSelect(placeholder=f"Select channel for {platform} alerts...")
        channel_selector.callback = self.post_alert
        self.add_item(channel_selector)

    async def post_alert(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        target_channel_id = int(interaction.data["values"][0])
        
        alert_doc = {
            "alert_id": str(uuid.uuid4()),
            "guild_id": interaction.guild.id,
            "channel_id": target_channel_id,
            "platform": self.platform,
            "username": self.username
        }

        await self.bot.db.social_alerts.insert_one(alert_doc)
        await interaction.followup.send(f"✅ {self.platform} alerts for **{self.username}** will be sent to <#{target_channel_id}>!", ephemeral=True)

# --- Settings Views ---

class EssentialsSettingsView(BaseSettingsView):
    """The main settings menu for essential features."""
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

        back_button = Button(label="◀️ Back to Staff Menu", style=discord.ButtonStyle.primary, custom_id="essentials:nav_staff_main")
        back_button.callback = self.go_to_staff_main
        self.add_item(back_button)
        
        welcome_button = Button(label="Welcome & Goodbye", emoji="👋", custom_id="essentials:nav_welcome")
        welcome_button.callback = self.go_to_welcome
        self.add_item(welcome_button)

        rr_button = Button(label="Reaction Roles", emoji="🎭", custom_id="essentials:nav_reaction_roles")
        rr_button.callback = self.go_to_reaction_roles
        self.add_item(rr_button)

        levels_button = Button(label="Levels", emoji="📈", custom_id="essentials:nav_levels")
        levels_button.callback = self.go_to_levels
        self.add_item(levels_button)

        invites_button = Button(label="Invite Tracker", emoji="💌", custom_id="essentials:nav_invites")
        invites_button.callback = self.go_to_invites
        self.add_item(invites_button)

        polls_button = Button(label="Polls", emoji="📊", custom_id="essentials:nav_polls")
        polls_button.callback = self.go_to_polls
        self.add_item(polls_button)

        stats_button = Button(label="Server Statistics", emoji="📈", custom_id="essentials:nav_stats")
        stats_button.callback = self.go_to_statistics
        self.add_item(stats_button)

        temp_vc_button = Button(label="Temporary VCs", emoji="🔊", custom_id="essentials:nav_temp_vc")
        temp_vc_button.callback = self.go_to_temp_channels
        self.add_item(temp_vc_button)

    async def go_to_staff_main(self, interaction: discord.Interaction):
        from .menu_cog import StaffMenuView
        embed = create_embed("🛡️ Staff Menu", "Select a category to configure.", discord.Color.blue())
        view = StaffMenuView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def go_to_welcome(self, interaction: discord.Interaction):
        embed = create_embed("👋 Welcome & Goodbye Settings", "Configure messages for new and departing members.", discord.Color.from_rgb(119, 178, 85))
        config = await self.bot.get_guild_config(interaction.guild.id)
        view = WelcomeSettingsView(self.bot, interaction.guild.id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def go_to_reaction_roles(self, interaction: discord.Interaction):
        embed = create_embed("🎭 Reaction Roles", "Create and manage reaction role panels.", discord.Color.purple())
        config = await self.bot.get_guild_config(interaction.guild.id)
        view = ReactionRolesSettingsView(self.bot, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def go_to_levels(self, interaction: discord.Interaction):
        embed = create_embed("📈 Levels Settings", "Configure the leveling system for your server.", discord.Color.blue())
        config = await self.bot.get_guild_config(interaction.guild.id)
        view = LevelsSettingsView(self.bot, interaction.guild.id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def go_to_invites(self, interaction: discord.Interaction):
        embed = create_embed("💌 Invite Tracker Settings", "Configure the invite tracking system for your server.", discord.Color.pink())
        config = await self.bot.get_guild_config(interaction.guild.id)
        view = InviteTrackerSettingsView(self.bot, interaction.guild.id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        
    async def go_to_polls(self, interaction: discord.Interaction):
        embed = create_embed("📊 Polls", "Create and manage server polls.", discord.Color.dark_orange())
        view = PollsSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def go_to_statistics(self, interaction: discord.Interaction):
        embed = create_embed("📈 Statistics", "Configure and view server statistics.", discord.Color.dark_magenta())
        config = await self.bot.get_guild_config(interaction.guild.id)
        view = StatisticsSettingsView(self.bot, interaction.guild.id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def go_to_temp_channels(self, interaction: discord.Interaction):
        embed = create_embed("🔊 Temporary Channels", "Configure channels that are created on demand.", discord.Color.dark_blue())
        config = await self.bot.get_guild_config(interaction.guild.id)
        view = TemporaryChannelsSettingsView(self.bot, interaction.guild.id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

class WelcomeSettingsView(BaseSettingsView):
    """The configuration view for Welcome & Goodbye messages."""
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        
        welcome_config = self.config.get("welcome", {})
        bday_config = self.config.get("birthdays", {})
        
        back_button = Button(label="◀️ Back", custom_id="welcome:nav_essentials")
        back_button.callback = self.go_to_essentials
        self.add_item(back_button)

        is_enabled = welcome_config.get("enabled", False)
        toggle_button = Button(label=f"Welcome System is {'ON' if is_enabled else 'OFF'}", style=discord.ButtonStyle.green if is_enabled else discord.ButtonStyle.red, custom_id="welcome:toggle_main")
        toggle_button.callback = self.toggle_main
        self.add_item(toggle_button)

        welcome_msg_button = Button(label="Set Welcome Message", emoji="🎉", custom_id="welcome:set_message")
        welcome_msg_button.callback = self.set_welcome_message
        self.add_item(welcome_msg_button)

        goodbye_msg_button = Button(label="Set Goodbye Message", emoji="😢", custom_id="welcome:set_goodbye")
        goodbye_msg_button.callback = self.set_goodbye_message
        self.add_item(goodbye_msg_button)

        welcome_dm_button = Button(label="Set Welcome DM", emoji="👋", custom_id="welcome:set_dm")
        welcome_dm_button.callback = self.set_welcome_dm
        self.add_item(welcome_dm_button)

        bday_dm_enabled = bday_config.get("dm_on_join", False)
        bday_dm_button = Button(label=f"Birthday DM on Join is {'ON' if bday_dm_enabled else 'OFF'}", style=discord.ButtonStyle.green if bday_dm_enabled else discord.ButtonStyle.red, custom_id="welcome:toggle_bday_dm")
        bday_dm_button.callback = self.toggle_bday_dm
        self.add_item(bday_dm_button)

        welcome_channel_select = DiscordChannelSelect(placeholder="Select Welcome Channel...", custom_id="welcome:set_channel")
        welcome_channel_select.callback = self.set_welcome_channel
        self.add_item(welcome_channel_select)

        goodbye_channel_select = DiscordChannelSelect(placeholder="Select Goodbye Channel...", custom_id="welcome:set_goodbye_channel")
        goodbye_channel_select.callback = self.set_goodbye_channel
        self.add_item(goodbye_channel_select)

    async def go_to_essentials(self, interaction: discord.Interaction):
        embed = create_embed("⭐ Essentials", "Configure welcome messages, reaction roles, and other essential features.", discord.Color.gold())
        view = EssentialsSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def toggle_main(self, interaction: discord.Interaction):
        new_status = not self.config.get("welcome", {}).get("enabled", False)
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"welcome.enabled": new_status}}, upsert=True)
        config = await self.bot.get_guild_config(self.guild_id)
        view = WelcomeSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(f"✅ Welcome system turned {'ON' if new_status else 'OFF'}.", ephemeral=True)

    async def toggle_bday_dm(self, interaction: discord.Interaction):
        new_status = not self.config.get("birthdays", {}).get("dm_on_join", False)
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"birthdays.dm_on_join": new_status}}, upsert=True)
        config = await self.bot.get_guild_config(self.guild_id)
        view = WelcomeSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(f"✅ Birthday DM on Join turned {'ON' if new_status else 'OFF'}.", ephemeral=True)

    async def set_welcome_message(self, interaction: discord.Interaction):
        current_msg = self.config.get("welcome", {}).get("welcome_message", "Welcome {user} to {server}!")
        await interaction.response.send_modal(MessageModal(self.bot, "Set Welcome Message", "welcome.welcome_message", current_msg))
        
    async def set_welcome_dm(self, interaction: discord.Interaction):
        current_msg = self.config.get("welcome", {}).get("welcome_dm", "Welcome to {server}, {user}!")
        await interaction.response.send_modal(MessageModal(self.bot, "Set Welcome DM", "welcome.welcome_dm", current_msg))

    async def set_goodbye_message(self, interaction: discord.Interaction):
        current_msg = self.config.get("welcome", {}).get("goodbye_message", "{user} has left the server.")
        await interaction.response.send_modal(MessageModal(self.bot, "Set Goodbye Message", "welcome.goodbye_message", current_msg))
    
    async def set_welcome_channel(self, interaction: discord.Interaction):
        channel_id = int(interaction.data["values"][0])
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"welcome.welcome_channel_id": channel_id}}, upsert=True)
        await interaction.response.send_message(f"✅ Welcome channel set to <#{channel_id}>!", ephemeral=True)

    async def set_goodbye_channel(self, interaction: discord.Interaction):
        channel_id = int(interaction.data["values"][0])
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"welcome.goodbye_channel_id": channel_id}}, upsert=True)
        await interaction.response.send_message(f"✅ Goodbye channel set to <#{channel_id}>!", ephemeral=True)

class ReactionRolesSettingsView(BaseSettingsView):
    """Main view for managing all reaction role panels."""
    def __init__(self, bot: commands.Bot, config: dict):
        super().__init__(bot)
        self.config = config

        back_button = Button(label="◀️ Back", custom_id="rr_main:nav_essentials")
        back_button.callback = self.go_to_essentials
        self.add_item(back_button)

        create_button = Button(label="Create New Panel", emoji="➕", style=discord.ButtonStyle.success, custom_id="rr_main:create")
        create_button.callback = self.create_panel
        self.add_item(create_button)

        panels = self.config.get("reaction_roles", [])
        if panels:
            options = [discord.SelectOption(label=p.get("name", "Unnamed Panel"), value=p.get("panel_id")) for p in panels]
            panel_select = Select(placeholder="Edit an existing panel...", options=options, custom_id="rr_main:select_panel")
            panel_select.callback = self.select_panel
            self.add_item(panel_select)

    async def go_to_essentials(self, interaction: discord.Interaction):
        embed = create_embed("⭐ Essentials", "Configure welcome messages, reaction roles, and the leveling system.", discord.Color.gold())
        view = EssentialsSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def create_panel(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ReactionRoleNameModal(self.bot))

    async def select_panel(self, interaction: discord.Interaction):
        panel_id = interaction.data["values"][0]
        config = await self.bot.get_guild_config(interaction.guild.id)
        panel_data = next((p for p in config.get("reaction_roles", []) if p["panel_id"] == panel_id), None)
        if panel_data:
            embed = create_embed(f"🔧 Configuring '{panel_data['name']}'", "Add roles and set a message, then post it to a channel.", discord.Color.orange())
            view = ConfigureReactionRoleView(self.bot, panel_id)
            await interaction.response.edit_message(embed=embed, view=view)
            view.message = await interaction.original_response()

class ConfigureReactionRoleView(BaseSettingsView):
    """View to configure a single reaction role panel."""
    def __init__(self, bot: commands.Bot, panel_id: str):
        super().__init__(bot)
        self.panel_id = panel_id
        
        back_button = Button(label="◀️ Back", custom_id="rr_config:nav_back")
        back_button.callback = self.go_back
        self.add_item(back_button)

        add_role_button = Button(label="Add Role", emoji="➕", style=discord.ButtonStyle.primary, custom_id="rr_config:add_role")
        add_role_button.callback = self.add_role
        self.add_item(add_role_button)

        post_button = Button(label="Post Panel", emoji="📮", style=discord.ButtonStyle.success, custom_id="rr_config:post")
        post_button.callback = self.post_panel
        self.add_item(post_button)

    async def go_back(self, interaction: discord.Interaction):
        embed = create_embed("🎭 Reaction Roles", "Create and manage reaction role panels.", discord.Color.purple())
        config = await self.bot.get_guild_config(interaction.guild.id)
        view = ReactionRolesSettingsView(self.bot, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()
    
    async def add_role(self, interaction: discord.Interaction):
        await interaction.response.send_message("This would open a modal to add an emoji and role.", ephemeral=True)

    async def post_panel(self, interaction: discord.Interaction):
        await interaction.response.send_message("This would open a channel selector to post the panel.", ephemeral=True)

class LevelsSettingsView(BaseSettingsView):
    """View for configuring the leveling system."""
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        
        levels_config = self.config.get("levels", {})
        is_enabled = levels_config.get("enabled", False)

        back_button = Button(label="◀️ Back", custom_id="levels:nav_essentials")
        back_button.callback = self.go_to_essentials
        self.add_item(back_button)

        toggle_button = Button(label=f"Leveling System is {'ON' if is_enabled else 'OFF'}", style=discord.ButtonStyle.green if is_enabled else discord.ButtonStyle.red, custom_id="levels:toggle")
        toggle_button.callback = self.toggle_levels
        self.add_item(toggle_button)

        message_button = Button(label="Set Level-Up Message", emoji="💬", custom_id="levels:set_message")
        message_button.callback = self.set_level_up_message
        self.add_item(message_button)

        self.add_item(DiscordChannelSelect(placeholder="Select Level-Up Announcement Channel...", custom_id="levels:set_channel"))

    async def go_to_essentials(self, interaction: discord.Interaction):
        embed = create_embed("⭐ Essentials", "Configure welcome messages, reaction roles, and the leveling system.", discord.Color.gold())
        view = EssentialsSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def toggle_levels(self, interaction: discord.Interaction):
        new_status = not self.config.get("levels", {}).get("enabled", False)
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"levels.enabled": new_status}}, upsert=True)
        config = await self.bot.get_guild_config(self.guild_id)
        view = LevelsSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(f"✅ Leveling system turned {'ON' if new_status else 'OFF'}.", ephemeral=True)

    async def set_level_up_message(self, interaction: discord.Interaction):
        current_msg = self.config.get("levels", {}).get("level_up_message", "Congrats {user}, you've reached level {level}!")
        await interaction.response.send_modal(MessageModal(self.bot, "Set Level-Up Message", "levels.level_up_message", current_msg))

class InviteTrackerSettingsView(BaseSettingsView):
    """View for configuring the invite tracking system."""
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        
        invites_config = self.config.get("invites", {})
        is_enabled = invites_config.get("enabled", False)

        back_button = Button(label="◀️ Back", custom_id="invites:nav_essentials")
        back_button.callback = self.go_to_essentials
        self.add_item(back_button)

        toggle_button = Button(label=f"Invite Tracker is {'ON' if is_enabled else 'OFF'}", style=discord.ButtonStyle.green if is_enabled else discord.ButtonStyle.red, custom_id="invites:toggle")
        toggle_button.callback = self.toggle_invites
        self.add_item(toggle_button)

        rewards_button = Button(label="Manage Role Rewards", emoji="🎁", custom_id="invites:rewards")
        rewards_button.callback = self.manage_rewards
        self.add_item(rewards_button)

        no_invite_button = Button(label="Set No Invite Roles", emoji="🚫", custom_id="invites:no_invite_roles")
        no_invite_button.callback = self.set_no_invite_roles
        self.add_item(no_invite_button)

    async def go_to_essentials(self, interaction: discord.Interaction):
        embed = create_embed("⭐ Essentials", "Configure welcome messages, reaction roles, and the leveling system.", discord.Color.gold())
        view = EssentialsSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def toggle_invites(self, interaction: discord.Interaction):
        new_status = not self.config.get("invites", {}).get("enabled", False)
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"invites.enabled": new_status}}, upsert=True)
        config = await self.bot.get_guild_config(self.guild_id)
        view = InviteTrackerSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(f"✅ Invite Tracker turned {'ON' if new_status else 'OFF'}.", ephemeral=True)

    async def manage_rewards(self, interaction: discord.Interaction):
        await interaction.response.send_message("This would open a menu to manage role rewards.", ephemeral=True)

    async def set_no_invite_roles(self, interaction: discord.Interaction):
        await interaction.response.send_message("This would open a menu to set no-invite roles.", ephemeral=True)

class PollsSettingsView(BaseSettingsView):
    """View for managing polls."""
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        
        back_button = Button(label="◀️ Back", custom_id="polls:nav_essentials")
        back_button.callback = self.go_to_essentials
        self.add_item(back_button)

        create_button = Button(label="Create New Poll", emoji="➕", style=discord.ButtonStyle.success, custom_id="polls:create")
        create_button.callback = self.create_poll
        self.add_item(create_button)

    async def go_to_essentials(self, interaction: discord.Interaction):
        embed = create_embed("⭐ Essentials", "Configure welcome messages, reaction roles, and the leveling system.", discord.Color.gold())
        view = EssentialsSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def create_poll(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CreatePollModal(self.bot))

class PollVotingView(View):
    """The public-facing view for voting on a poll."""
    def __init__(self, choices: List[str]):
        super().__init__(timeout=None)
        self.votes = {choice: [] for choice in choices}
        
        for choice in choices:
            button = Button(label=f"{choice} (0)", custom_id=f"poll_vote:{choice}")
            button.callback = self.vote
            self.add_item(button)

    async def vote(self, interaction: discord.Interaction):
        choice = interaction.data["custom_id"].split(":")[1]
        user_id = interaction.user.id

        # Toggle vote
        if user_id in self.votes[choice]:
            self.votes[choice].remove(user_id)
        else:
            self.votes[choice].append(user_id)
        
        for item in self.children:
            if isinstance(item, Button):
                item_choice = item.custom_id.split(":")[1]
                item.label = f"{item_choice} ({len(self.votes[item_choice])})"
        
        await interaction.response.edit_message(view=self)

class StatisticsSettingsView(BaseSettingsView):
    """View for managing server statistics."""
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        
        stats_config = self.config.get("statistics", {})
        is_enabled = stats_config.get("enabled", False)

        back_button = Button(label="◀️ Back", custom_id="stats:nav_essentials")
        back_button.callback = self.go_to_essentials
        self.add_item(back_button)

        toggle_button = Button(label=f"Statistics Tracking is {'ON' if is_enabled else 'OFF'}", style=discord.ButtonStyle.green if is_enabled else discord.ButtonStyle.red, custom_id="stats:toggle")
        toggle_button.callback = self.toggle_stats
        self.add_item(toggle_button)
        
        channel_select = DiscordChannelSelect(placeholder="Select Statistics Report Channel...", custom_id="stats:set_channel")
        channel_select.callback = self.set_stats_channel
        self.add_item(channel_select)

    async def go_to_essentials(self, interaction: discord.Interaction):
        embed = create_embed("⭐ Essentials", "Configure welcome messages, reaction roles, and the leveling system.", discord.Color.gold())
        view = EssentialsSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def toggle_stats(self, interaction: discord.Interaction):
        new_status = not self.config.get("statistics", {}).get("enabled", False)
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"statistics.enabled": new_status}}, upsert=True)
        config = await self.bot.get_guild_config(self.guild_id)
        view = StatisticsSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(f"✅ Statistics tracking turned {'ON' if new_status else 'OFF'}.", ephemeral=True)

    async def set_stats_channel(self, interaction: discord.Interaction):
        channel_id = int(interaction.data["values"][0])
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"statistics.channel_id": channel_id}}, upsert=True)
        await interaction.response.send_message(f"✅ Statistics channel set to <#{channel_id}>!", ephemeral=True)

class TemporaryChannelsSettingsView(BaseSettingsView):
    """View for managing temporary channels."""
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        
        temp_channels_config = self.config.get("temp_channels", {})
        is_enabled = temp_channels_config.get("enabled", False)

        back_button = Button(label="◀️ Back", custom_id="temp_channels:nav_essentials")
        back_button.callback = self.go_to_essentials
        self.add_item(back_button)

        toggle_button = Button(label=f"Temporary Channels are {'ON' if is_enabled else 'OFF'}", style=discord.ButtonStyle.green if is_enabled else discord.ButtonStyle.red, custom_id="temp_channels:toggle")
        toggle_button.callback = self.toggle_temp_channels
        self.add_item(toggle_button)
        
        category_select = DiscordChannelSelect(placeholder="Select 'Join to Create' Category...", channel_types=[discord.ChannelType.category], custom_id="temp_channels:set_category")
        category_select.callback = self.set_temp_channel_category
        self.add_item(category_select)

    async def go_to_essentials(self, interaction: discord.Interaction):
        embed = create_embed("⭐ Essentials", "Configure welcome messages, reaction roles, and the leveling system.", discord.Color.gold())
        view = EssentialsSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def toggle_temp_channels(self, interaction: discord.Interaction):
        new_status = not self.config.get("temp_channels", {}).get("enabled", False)
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"temp_channels.enabled": new_status}}, upsert=True)
        config = await self.bot.get_guild_config(self.guild_id)
        view = TemporaryChannelsSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(f"✅ Temporary Channels turned {'ON' if new_status else 'OFF'}.", ephemeral=True)

    async def set_temp_channel_category(self, interaction: discord.Interaction):
        category_id = int(interaction.data["values"][0])
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"temp_channels.category_id": category_id}}, upsert=True)
        await interaction.response.send_message(f"✅ Temporary channels category set!", ephemeral=True)


# --- Cog Loader ---
class EssentialsCog(commands.Cog):
    """A cog for handling essential features and their configuration."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.invites = {}
        self.check_reminders.start()
        self.update_statistics.start()
        self.bot.loop.create_task(self.load_invites())

    def cog_unload(self):
        self.check_reminders.cancel()
        self.update_statistics.cancel()

    async def load_invites(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                self.invites[guild.id] = await guild.invites()
            except discord.Forbidden:
                logger.warning(f"Missing permissions to fetch invites for guild {guild.id}")

    @tasks.loop(minutes=1)
    async def check_reminders(self):
        """Checks for due reminders every minute and sends them."""
        now = datetime.datetime.now(datetime.timezone.utc)
        due_reminders = self.bot.db.reminders.find({"due_time": {"$lte": now}})
        async for reminder in due_reminders:
            channel = self.bot.get_channel(reminder["channel_id"])
            if channel:
                try:
                    await channel.send(f"<@{reminder['author_id']}> Reminder: {reminder['message']}")
                except discord.Forbidden:
                    logger.warning(f"Could not send reminder {reminder['reminder_id']} to channel {channel.id}")
            
            await self.bot.db.reminders.delete_one({"_id": reminder["_id"]})

    @check_reminders.before_loop
    async def before_check_reminders(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def update_statistics(self):
        """Periodically updates and posts server statistics."""
        pass

    @update_statistics.before_loop
    async def before_update_statistics(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild.id in self.invites:
            self.invites[invite.guild.id].append(invite)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild.id in self.invites:
            self.invites[invite.guild.id] = [i for i in self.invites[invite.guild.id] if i.code != invite.code]

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        config = await self.bot.get_guild_config(member.guild.id)
        
        # Welcome message logic
        welcome_config = config.get("welcome", {})
        if welcome_config.get("enabled"):
            # Channel Message
            if welcome_config.get("welcome_channel_id"):
                channel = member.guild.get_channel(welcome_config["welcome_channel_id"])
                if channel:
                    message = welcome_config.get("welcome_message", "Welcome {user} to {server}!")
                    formatted_message = message.format(user=member.mention, server=member.guild.name, count=member.guild.member_count)
                    try:
                        await channel.send(formatted_message)
                    except discord.Forbidden:
                        logger.error(f"Missing permissions to send welcome message in {channel.id}")
            # DM Message
            if welcome_config.get("welcome_dm"):
                dm_message = welcome_config.get("welcome_dm")
                formatted_dm = dm_message.format(user=member.mention, server=member.guild.name, count=member.guild.member_count)
                try:
                    await member.send(formatted_dm)
                except discord.Forbidden:
                    logger.warning(f"Could not send welcome DM to {member.id}")

        # Birthday DM on Join Logic
        bday_config = config.get("birthdays", {})
        if bday_config.get("enabled") and bday_config.get("dm_on_join"):
            try:
                class BirthdayDMView(View):
                    def __init__(self, bot):
                        super().__init__(timeout=None)
                        self.bot = bot
                    @discord.ui.button(label="Set Your Birthday", style=discord.ButtonStyle.success, emoji="🎂")
                    async def set_bday(self, interaction: discord.Interaction, button: Button):
                        await interaction.response.send_modal(SetBirthdayModal(self.bot))
                
                await member.send("Welcome! Click the button below to set your birthday and get a special role on your day!", view=BirthdayDMView(self.bot))
            except discord.Forbidden:
                logger.warning(f"Could not send birthday setup DM to {member.id}")

        # Invite tracking logic
        invites_config = config.get("invites", {})
        if invites_config.get("enabled"):
            # Invite tracking logic would go here
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # Goodbye message logic
        config = await self.bot.get_guild_config(member.guild.id)
        welcome_config = config.get("welcome", {})
        if welcome_config.get("enabled") and welcome_config.get("goodbye_channel_id"):
            channel = member.guild.get_channel(welcome_config["goodbye_channel_id"])
            if channel:
                message = welcome_config.get("goodbye_message", "{user} has left the server.")
                formatted_message = message.format(user=member.display_name, server=member.guild.name, count=member.guild.member_count)
                try:
                    await channel.send(formatted_message)
                except discord.Forbidden:
                    logger.error(f"Missing permissions to send goodbye message in {channel.id}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Reaction roles logic
        pass
    
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        # Reaction roles logic
        pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Leveling logic
        if not message.guild or message.author.bot:
            return
        config = await self.bot.get_guild_config(message.guild.id)
        levels_config = config.get("levels", {})
        if not levels_config.get("enabled"):
            return
        # XP awarding logic would go here

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # Temporary channels logic
        config = await self.bot.get_guild_config(member.guild.id)
        temp_channels_config = config.get("temp_channels", {})
        
        if not temp_channels_config.get("enabled"):
            return
            
        if after.channel and after.channel.id == temp_channels_config.get("create_channel_id"):
            category = after.channel.category
            if category:
                try:
                    new_channel = await category.create_voice_channel(name=f"{member.display_name}'s Channel")
                    await member.move_to(new_channel)
                except discord.Forbidden:
                    logger.error(f"Missing permissions to create/move in temp channels for guild {member.guild.id}")

        if before.channel and before.channel.category and before.channel.category.id == temp_channels_config.get("category_id") and len(before.channel.members) == 0:
            if before.channel.id != temp_channels_config.get("create_channel_id"):
                try:
                    await before.channel.delete()
                except discord.Forbidden:
                    logger.error(f"Missing permissions to delete temp channel {before.channel.id}")


async def setup(bot: commands.Bot):
    """Adds the cog to the bot."""
    await bot.add_cog(EssentialsCog(bot))
