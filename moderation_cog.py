import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Select, Modal, TextInput, RoleSelect, UserSelect
import logging
import collections
import datetime
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple, Optional, List, Any

# Import from the utils cog for consistency
from .utils import BaseSettingsView, create_embed, STRINGS

logger = logging.getLogger(__name__)

# --- Model Initialization ---
try:
    from transformers import pipeline
    TOXICITY_CLASSIFIER = pipeline("text-classification", model="unitary/toxic-bert")
    logger.info("✅ AI Moderation model loaded (unitary/toxic-bert).")
    # Create a thread pool executor for running the model asynchronously
    executor = ThreadPoolExecutor(max_workers=1)
except Exception as e:
    TOXICITY_CLASSIFIER = None
    executor = None
    logger.error(f"⚠️ Could not load AI Moderation model. AI features will be disabled. Error: {e}")

# --- Reusable Modals ---

class ProfanityWordlistModal(Modal, title="Manage Profanity Wordlist"):
    """A modal to manage the custom profanity wordlist."""
    def __init__(self, bot: commands.Bot, current_words: List[str]):
        super().__init__()
        self.bot = bot
        self.word_list = TextInput(
            label="Banned Words (comma-separated)",
            style=discord.TextStyle.paragraph,
            default=", ".join(current_words),
            required=False,
            max_length=2000
        )
        self.add_item(self.word_list)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        words = [word.strip().lower() for word in self.word_list.value.split(',') if word.strip()]
        try:
            await self.bot.db.configs.update_one(
                {"guild_id": interaction.guild.id},
                {"$set": {"ai_moderation.filters.profanity.custom_word_list": words}},
                upsert=True
            )
            await interaction.response.send_message("✅ Profanity wordlist updated!", ephemeral=True)
        except Exception as e:
            logger.error(f"DB update failed for profanity list in guild {interaction.guild.id}: {e}")
            await interaction.response.send_message(STRINGS["db_error"], ephemeral=True)


class ValueInputModal(Modal):
    def __init__(self, bot: commands.Bot, title: str, label: str, db_key: str, current_value: Any):
        super().__init__(title=title)
        self.bot = bot
        self.db_key = db_key
        self.value_input = TextInput(label=label, default=str(current_value))
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            # Use int for whole numbers, float for sensitivity
            if "sensitivity" in self.db_key:
                new_value = float(self.value_input.value)
            else:
                new_value = int(self.value_input.value)

            if new_value < 0:
                raise ValueError("Value cannot be negative.")
        except ValueError:
            return await interaction.response.send_message("Invalid number format.", ephemeral=True)
        
        try:
            await self.bot.db.configs.update_one({"guild_id": interaction.guild.id}, {"$set": {self.db_key: new_value}}, upsert=True)
            logger.info(f"Value for '{self.db_key}' set to {new_value} for guild {interaction.guild.id} by {interaction.user}")
            await interaction.response.send_message(f"✅ {self.title} updated to **{new_value}**!", ephemeral=True)
        except Exception as e:
            logger.error(f"DB update failed for guild {interaction.guild.id} on key {self.db_key}: {e}")
            await interaction.response.send_message(STRINGS["db_error"], ephemeral=True)

# --- UI Views ---

class ModerationParentView(BaseSettingsView):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        
        back_button = Button(label="◀️ Back", custom_id="mod_parent:nav_staff")
        back_button.callback = self.go_to_staff_menu
        self.add_item(back_button)

        ai_mod_button = Button(label="AI Content Filters", emoji="🤖", custom_id="mod_parent:nav_ai_mod")
        ai_mod_button.callback = self.go_to_ai_mod
        self.add_item(ai_mod_button)

        anti_raid_button = Button(label="Anti-Raid & Anti-Nuke", emoji="🚨", custom_id="mod_parent:nav_anti_raid")
        anti_raid_button.callback = self.go_to_anti_raid
        self.add_item(anti_raid_button)

    async def go_to_staff_menu(self, interaction: discord.Interaction) -> None:
        from .menu_cog import StaffMenuView
        embed = create_embed("🛡️ Staff Menu", "Select a category to configure.", discord.Color.blue())
        await interaction.response.edit_message(embed=embed, view=StaffMenuView(self.bot))

    async def go_to_ai_mod(self, interaction: discord.Interaction) -> None:
        embed = create_embed("🤖 AI Content Filters", "Configure AI-powered text moderation.", discord.Color.dark_blue())
        config = await self.bot.get_guild_config(interaction.guild.id)
        view = AIModSettingsView(self.bot, interaction.guild.id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def go_to_anti_raid(self, interaction: discord.Interaction) -> None:
        embed = create_embed("🚨 Anti-Raid & Anti-Nuke Settings", "Configure protection against join-bots.", discord.Color.dark_red())
        config = await self.bot.get_guild_config(interaction.guild.id)
        view = AntiRaidSettingsView(self.bot, interaction.guild.id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

class AIModSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        mod_config = self.config.get("ai_moderation", {})
        is_enabled = mod_config.get("enabled", False)

        back_button = Button(label="◀️ Back", custom_id="ai_mod:nav_parent")
        back_button.callback = self.go_to_parent
        self.add_item(back_button)

        toggle_button = Button(label=f"AI Moderation is {'ON' if is_enabled else 'OFF'}", style=discord.ButtonStyle.green if is_enabled else discord.ButtonStyle.red, custom_id="ai_mod:toggle")
        toggle_button.callback = self.toggle_ai_mod
        self.add_item(toggle_button)
        
        actions_button = Button(label="Configure Actions", emoji="⚙️", custom_id="ai_mod:nav_actions")
        actions_button.callback = self.go_to_actions
        self.add_item(actions_button)

        filters_button = Button(label="Configure Filters", emoji="🔬", custom_id="ai_mod:nav_filters")
        filters_button.callback = self.go_to_filters
        self.add_item(filters_button)

        exempt_button = Button(label="Manage Exemptions", emoji="🛡️", custom_id="ai_mod:manage_exemptions")
        exempt_button.callback = self.manage_exemptions
        self.add_item(exempt_button)

    async def go_to_parent(self, interaction: discord.Interaction) -> None:
        embed = create_embed("🛡️ Moderation Settings", "Configure moderation.", discord.Color.blue())
        view = ModerationParentView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def toggle_ai_mod(self, interaction: discord.Interaction) -> None:
        new_status = not self.config.get("ai_moderation", {}).get("enabled", False)
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"ai_moderation.enabled": new_status}}, upsert=True)
        config = await self.bot.get_guild_config(self.guild_id)
        view = AIModSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        view.message = await interaction.original_response()
        await interaction.followup.send(f"✅ AI Moderation turned {'ON' if new_status else 'OFF'}.", ephemeral=True)

    async def go_to_actions(self, interaction: discord.Interaction) -> None:
        embed = create_embed("⚙️ Configure Moderation Actions", "Select actions for the bot to take.", discord.Color.dark_purple())
        config = await self.bot.get_guild_config(self.guild_id)
        view = ActionConfigView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def go_to_filters(self, interaction: discord.Interaction) -> None:
        embed = create_embed("🔬 Configure Content Filters", "Enable or disable specific filters.", discord.Color.teal())
        config = await self.bot.get_guild_config(self.guild_id)
        view = FilterConfigView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def manage_exemptions(self, interaction: discord.Interaction) -> None:
        embed = create_embed("🛡️ Manage Exemptions", "Select roles and users to be exempt from AI moderation.", discord.Color.dark_grey())
        config = await self.bot.get_guild_config(self.guild_id)
        view = ExemptionManagerView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

class ExemptionManagerView(BaseSettingsView):
    """View to manage role and user exemptions with dropdowns."""
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        mod_config = self.config.get("ai_moderation", {})
        
        back_button = Button(label="◀️ Back", custom_id="exempt:nav_back")
        back_button.callback = self.go_back
        self.add_item(back_button)

        self.role_select = RoleSelect(
            placeholder="Select exempt roles...",
            min_values=0,
            max_values=25,
            custom_id="exempt:role_select"
        )
        self.role_select.callback = self.on_select
        self.add_item(self.role_select)

        self.user_select = UserSelect(
            placeholder="Select exempt users...",
            min_values=0,
            max_values=25,
            custom_id="exempt:user_select"
        )
        self.user_select.callback = self.on_select
        self.add_item(self.user_select)

    async def go_back(self, interaction: discord.Interaction) -> None:
        embed = create_embed("🤖 AI Content Filters", "Configure AI-powered text moderation.", discord.Color.dark_blue())
        config = await self.bot.get_guild_config(self.guild_id)
        view = AIModSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def on_select(self, interaction: discord.Interaction) -> None:
        role_ids = [role.id for role in self.role_select.values]
        user_ids = [user.id for user in self.user_select.values]
        
        try:
            await self.bot.db.configs.update_one(
                {"guild_id": self.guild_id},
                {"$set": {"ai_moderation.exempt_roles": role_ids, "ai_moderation.exempt_users": user_ids}},
                upsert=True
            )
            await interaction.response.send_message("✅ Exemptions updated!", ephemeral=True)
        except Exception as e:
            logger.error(f"DB update failed for exemptions in guild {self.guild_id}: {e}")
            await interaction.response.send_message(STRINGS["db_error"], ephemeral=True)


class ActionConfigView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        actions = self.config.get("ai_moderation", {}).get("actions", {})
        
        back_button = Button(label="◀️ Back", custom_id="actions:nav_parent")
        back_button.callback = self.go_to_parent
        self.add_item(back_button)
        self.add_item(Select(placeholder="Select actions to perform...", min_values=0, max_values=3, options=[
            discord.SelectOption(label="Warn User in DMs", value="warn_user", default=actions.get("warn_user")),
            discord.SelectOption(label="Delete Message", value="delete_message", default=actions.get("delete_message")),
            discord.SelectOption(label="Timeout User", value="timeout_user", default=actions.get("timeout_user")),
        ]))

    async def go_to_parent(self, interaction: discord.Interaction) -> None:
        embed = create_embed("🤖 AI Content Filters", "Configure AI-powered text moderation.", discord.Color.dark_blue())
        config = await self.bot.get_guild_config(self.guild_id)
        view = AIModSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

class FilterConfigView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        filters_config = self.config.get("ai_moderation", {}).get("filters", {})
        
        back_button = Button(label="◀️ Back", custom_id="filters:nav_parent")
        back_button.callback = self.go_to_parent
        self.add_item(back_button)

        for key in filters_config.keys():
            is_enabled = filters_config.get(key, {}).get("enabled", False)
            toggle_button = Button(
                label=f"{key.replace('_', ' ').title()}: {'ON' if is_enabled else 'OFF'}",
                style=discord.ButtonStyle.green if is_enabled else discord.ButtonStyle.red,
                custom_id=f"filter:toggle:{key[:20]}"
            )
            toggle_button.callback = self.toggle_filter
            self.add_item(toggle_button)
            
            if key in ["toxicity", "hate_speech", "spam"]:
                sensitivity_button = Button(
                    label=f"Set {key.replace('_', ' ').title()} Sensitivity",
                    custom_id=f"filter:sensitivity:{key[:20]}"
                )
                sensitivity_button.callback = self.set_sensitivity
                self.add_item(sensitivity_button)
            
            if key == "profanity":
                wordlist_button = Button(
                    label="Manage Profanity List",
                    emoji="📝",
                    custom_id="filter:wordlist"
                )
                wordlist_button.callback = self.manage_wordlist
                self.add_item(wordlist_button)

    async def go_to_parent(self, interaction: discord.Interaction) -> None:
        embed = create_embed("🤖 AI Content Filters", "Configure AI-powered text moderation.", discord.Color.dark_blue())
        config = await self.bot.get_guild_config(self.guild_id)
        view = AIModSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def toggle_filter(self, interaction: discord.Interaction) -> None:
        filter_key = interaction.data['custom_id'].split(':')[-1]
        current_status = self.config.get("ai_moderation", {}).get("filters", {}).get(filter_key, {}).get("enabled", False)
        
        try:
            await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {f"ai_moderation.filters.{filter_key}.enabled": not current_status}}, upsert=True)
        except Exception as e:
            logger.error(f"DB update failed for guild {self.guild_id}: {e}")
            await interaction.followup.send(STRINGS["db_error"], ephemeral=True)
            return

        config = await self.bot.get_guild_config(self.guild_id)
        view = FilterConfigView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        view.message = await interaction.original_response()
        await interaction.followup.send(f"✅ {filter_key.replace('_', ' ').title()} filter turned {'ON' if not current_status else 'OFF'}.", ephemeral=True)

    async def set_sensitivity(self, interaction: discord.Interaction) -> None:
        filter_key = interaction.data['custom_id'].split(':')[-1]
        current_val = self.config.get("ai_moderation", {}).get("filters", {}).get(filter_key, {}).get("sensitivity", 0.5)
        await interaction.response.send_modal(
            ValueInputModal(self.bot, f"Set {filter_key.replace('_', ' ').title()} Sensitivity", "Sensitivity (0.0-1.0)", f"ai_moderation.filters.{filter_key}.sensitivity", current_val)
        )

    async def manage_wordlist(self, interaction: discord.Interaction) -> None:
        current_words = self.config.get("ai_moderation", {}).get("filters", {}).get("profanity", {}).get("custom_word_list", [])
        await interaction.response.send_modal(ProfanityWordlistModal(self.bot, current_words))

class AntiRaidSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, guild_id: int, config: dict):
        super().__init__(bot)
        self.guild_id = guild_id
        self.config = config
        raid_config = self.config.get("anti_raid", {})
        is_enabled = raid_config.get("enabled", False)
        nuke_enabled = raid_config.get("anti_nuke", {}).get("enabled", False)

        back_button = Button(label="◀️ Back", custom_id="anti_raid:nav_parent")
        back_button.callback = self.go_to_parent
        self.add_item(back_button)

        toggle_raid_button = Button(label=f"Anti-Raid is {'ON' if is_enabled else 'OFF'}", style=discord.ButtonStyle.green if is_enabled else discord.ButtonStyle.red, custom_id="anti_raid:toggle")
        toggle_raid_button.callback = self.toggle_anti_raid
        self.add_item(toggle_raid_button)
        
        join_gate_button = Button(label="Configure Join Gate", emoji="🚪", custom_id="anti_raid:config_join_gate")
        join_gate_button.callback = self.config_join_gate
        self.add_item(join_gate_button)

        age_gate_button = Button(label="Configure Account Age Gate", emoji="🔞", custom_id="anti_raid:config_age_gate")
        age_gate_button.callback = self.config_age_gate
        self.add_item(age_gate_button)

        toggle_nuke_button = Button(label=f"Anti-Nuke is {'ON' if nuke_enabled else 'OFF'}", style=discord.ButtonStyle.green if nuke_enabled else discord.ButtonStyle.red, custom_id="anti_raid:toggle_nuke")
        toggle_nuke_button.callback = self.toggle_anti_nuke
        self.add_item(toggle_nuke_button)

    async def go_to_parent(self, interaction: discord.Interaction) -> None:
        embed = create_embed("🛡️ Moderation Settings", "Configure moderation.", discord.Color.blue())
        view = ModerationParentView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def toggle_anti_raid(self, interaction: discord.Interaction) -> None:
        new_status = not self.config.get("anti_raid", {}).get("enabled", False)
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"anti_raid.enabled": new_status}}, upsert=True)
        config = await self.bot.get_guild_config(self.guild_id)
        view = AntiRaidSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        view.message = await interaction.original_response()
        await interaction.followup.send(f"✅ Anti-Raid turned {'ON' if new_status else 'OFF'}.", ephemeral=True)

    async def config_join_gate(self, interaction: discord.Interaction) -> None:
        current_val = self.config.get("anti_raid", {}).get("join_gate", {}).get("joins", 10)
        await interaction.response.send_modal(ValueInputModal(self.bot, "Set Join Gate Threshold", "Number of Joins", "anti_raid.join_gate.joins", current_val))

    async def config_age_gate(self, interaction: discord.Interaction) -> None:
        current_val = self.config.get("anti_raid", {}).get("account_age_gate", {}).get("hours", 24)
        await interaction.response.send_modal(ValueInputModal(self.bot, "Set Account Age Gate", "Minimum Hours Old", "anti_raid.account_age_gate.hours", current_val))

    async def toggle_anti_nuke(self, interaction: discord.Interaction) -> None:
        new_status = not self.config.get("anti_raid", {}).get("anti_nuke", {}).get("enabled", False)
        await self.bot.db.configs.update_one({"guild_id": self.guild_id}, {"$set": {"anti_raid.anti_nuke.enabled": new_status}}, upsert=True)
        config = await self.bot.get_guild_config(self.guild_id)
        view = AntiRaidSettingsView(self.bot, self.guild_id, config)
        await interaction.response.edit_message(view=view)
        view.message = await interaction.original_response()
        await interaction.followup.send(f"✅ Anti-Nuke turned {'ON' if new_status else 'OFF'}.", ephemeral=True)

# --- Main Cog ---

class ModerationCog(commands.Cog):
    """A cog for handling AI-powered moderation and anti-raid protection."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.recent_joins = collections.defaultdict(lambda: collections.deque(maxlen=100))
        self.user_actions = collections.defaultdict(lambda: collections.deque(maxlen=20))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Processes incoming messages for moderation."""
        if not message.guild or message.author.bot:
            return

        try:
            config = await self.bot.get_guild_config(message.guild.id)
            if not config:
                logger.warning(f"No config found for guild {message.guild.id} in on_message")
                return
        except Exception as e:
            logger.error(f"Error fetching guild config for {message.guild.id}: {e}")
            return

        mod_config = config.get("ai_moderation", {})
        if not mod_config.get("enabled"):
            return
            
        if any(role.id in mod_config.get("exempt_roles", []) for role in message.author.roles):
            return
        if message.author.id in mod_config.get("exempt_users", []):
            return

        is_flagged, reason = await self._check_message_filters(message, mod_config.get("filters", {}))

        if is_flagged:
            await self._execute_mod_action(message, reason, mod_config)

    async def _check_message_filters(self, message: discord.Message, filters_config: dict) -> Tuple[bool, Optional[str]]:
        """Checks message content against all enabled filters asynchronously."""
        content = message.content
        
        if filters_config.get("profanity", {}).get("enabled"):
            word_list = filters_config.get("profanity", {}).get("custom_word_list", [])
            if any(re.search(rf'\b{re.escape(word)}\b', content, re.IGNORECASE) for word in word_list):
                return True, "Use of a word from the custom blocklist"
        
        if filters_config.get("server_invites", {}).get("enabled"):
            if re.search(r'(discord\.(gg|io|me|li)|discordapp\.com/invite)/[a-zA-Z0-9]+', content):
                return True, "Posting a server invite link"

        if TOXICITY_CLASSIFIER and executor and filters_config.get("toxicity", {}).get("enabled"):
            sensitivity = filters_config.get("toxicity", {}).get("sensitivity", 0.85)
            try:
                results = await self.bot.loop.run_in_executor(executor, TOXICITY_CLASSIFIER, content)
                score = results[0]['score'] if results[0]['label'] == 'TOXIC' else 1 - results[0]['score']
                
                if score > sensitivity:
                    return True, f"AI detected high toxicity (Score: {score:.2f})"
            except Exception as e:
                logger.error(f"Error during AI classification for message {message.id}: {e}")
                log_channel_id = filters_config.get("log_channel_id")
                if log_channel_id:
                    log_channel = message.guild.get_channel(log_channel_id)
                    if log_channel:
                        await log_channel.send(f"⚠️ AI moderation failed for a message due to an internal error: `{e}`")

        return False, None

    async def _execute_mod_action(self, message: discord.Message, reason: str, mod_config: dict):
        """Executes the configured actions (warn, delete, etc.) on a flagged message."""
        actions = mod_config.get("actions", {})
        log_channel_id = mod_config.get("log_channel_id")
        log_channel = message.guild.get_channel(log_channel_id) if log_channel_id else None

        if log_channel:
            embed = discord.Embed(
                title="AI Moderation Action",
                description=f"**User:** {message.author.mention} (`{message.author.id}`)\n**Reason:** {reason}\n**Channel:** {message.channel.mention}",
                color=discord.Color.dark_orange(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.add_field(name="Content", value=f"```{message.content[:1000]}```", inline=False)
            await log_channel.send(embed=embed)

        if actions.get("warn_user"):
            try:
                await message.author.send(f"Your message in **{message.guild.name}** was flagged for the following reason: **{reason}**. Please review the server rules.")
            except discord.Forbidden:
                logger.warning(f"Could not DM user {message.author.id} in guild {message.guild.id}")
                if log_channel:
                    await log_channel.send(f"⚠️ Could not warn {message.author.mention}: DMs are disabled.")

        if actions.get("timeout_user"):
            duration = datetime.timedelta(minutes=mod_config.get("actions", {}).get("timeout_duration_minutes", 5))
            try:
                await message.author.timeout(duration, reason=f"AI Moderation: {reason}")
            except discord.Forbidden:
                logger.error(f"Failed to timeout {message.author.id}: Missing permissions.")
                if log_channel:
                    await log_channel.send(f"⚠️ Failed to timeout {message.author.mention}: Missing permissions.")
            except Exception as e:
                logger.error(f"Failed to timeout {message.author.id}: {e}")

        if actions.get("delete_message"):
            try:
                await message.delete()
            except discord.Forbidden:
                logger.error(f"Failed to delete message {message.id}: Missing Manage Messages permission.")
                if log_channel:
                    await log_channel.send(f"⚠️ Failed to delete a flagged message in {message.channel.mention}: Missing permissions.")
            except discord.NotFound:
                pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Processes member joins for anti-raid protection."""
        try:
            config = await self.bot.get_guild_config(member.guild.id)
            if not config:
                logger.warning(f"No config found for guild {member.guild.id} in on_member_join")
                return
        except Exception as e:
            logger.error(f"Error fetching guild config for {member.guild.id}: {e}")
            return

        raid_config = config.get("anti_raid", {})
        if not raid_config.get("enabled"):
            return

        now = discord.utils.utcnow()

        age_gate = raid_config.get("account_age_gate", {})
        if age_gate.get("enabled"):
            min_hours = age_gate.get("hours", 24)
            if now - member.created_at < datetime.timedelta(hours=min_hours):
                await self._trigger_raid_action(member, f"Account is newer than {min_hours} hours.")
                return

        join_gate = raid_config.get("join_gate", {})
        if join_gate.get("enabled"):
            join_deque = self.recent_joins[member.guild.id]
            join_deque.append(now)
            
            max_joins = join_gate.get("joins", 10)
            time_window = datetime.timedelta(seconds=join_gate.get("seconds", 15))
            
            relevant_joins = [t for t in join_deque if now - t <= time_window]
            if len(relevant_joins) >= max_joins:
                await self._trigger_raid_action(member, f"Server join rate exceeded ({len(relevant_joins)} joins in {time_window.seconds}s).")
                self.recent_joins[member.guild.id].clear()
                
    async def _trigger_raid_action(self, member: discord.Member, reason: str):
        """Kicks a user and logs the anti-raid action."""
        action_taken = "Kick FAILED (Missing Permissions)"
        try:
            if member.guild.me.guild_permissions.kick_members:
                await member.kick(reason=f"Anti-Raid: {reason}")
                action_taken = "Kicked"
            else:
                logger.error(f"Cannot kick {member.id} in guild {member.guild.id}: Missing Kick Members permission.")
        except discord.Forbidden:
            logger.error(f"Cannot kick {member.id} in guild {member.guild.id}: Missing Kick Members permission.")
        except Exception as e:
            logger.error(f"Error during raid action for member {member.id}: {e}")
            action_taken = f"Kick FAILED (Error: {e})"
        
        config = await self.bot.get_guild_config(member.guild.id)
        log_channel_id = config.get("anti_raid", {}).get("log_channel_id")
        if log_channel_id:
            log_channel = member.guild.get_channel(log_channel_id)
            if log_channel:
                await log_channel.send(f"🚨 **Anti-Raid Triggered**\n**User:** {member.mention} (`{member.id}`)\n**Reason:** {reason}\n**Action:** {action_taken}")


async def setup(bot: commands.Bot):
    """Adds the cog to the bot."""
    await bot.add_cog(ModerationCog(bot))
