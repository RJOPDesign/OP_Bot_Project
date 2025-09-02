import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput, ChannelSelect as DiscordChannelSelect
from typing import Optional, List
import logging
import uuid
import datetime
import random
import re

# Import from the utils cog for consistency
from .utils import BaseSettingsView, create_embed, STRINGS
from .essentials_cog import EssentialsSettingsView

logger = logging.getLogger(__name__)

# --- Modals for Giveaway Feature ---

class CreateGiveawayModal(Modal, title="Create a New Giveaway"):
    """A modal for creating a new giveaway."""
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.prize = TextInput(label="What is the prize?", required=True, max_length=200)
        self.duration = TextInput(label="Duration (e.g., 10m, 8h, 7d)", required=True, max_length=10)
        self.winner_count = TextInput(label="Number of Winners", default="1", required=True, max_length=2)
        self.required_role = TextInput(label="Required Role ID (Optional)", required=False, max_length=20)
        self.add_item(self.prize)
        self.add_item(self.duration)
        self.add_item(self.winner_count)
        self.add_item(self.required_role)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # --- Input Validation ---
        delay_str = self.duration.value.lower()
        match = re.match(r"(\d+)([mhd])", delay_str)
        if not match:
            return await interaction.response.send_message("❌ Invalid time format. Use 'm' for minutes, 'h' for hours, or 'd' for days.", ephemeral=True)
        
        try:
            winners = int(self.winner_count.value)
            if winners < 1: raise ValueError()
        except ValueError:
            return await interaction.response.send_message("❌ Number of winners must be a positive number.", ephemeral=True)

        role_id = None
        if self.required_role.value:
            try:
                role_id = int(self.required_role.value)
                if not interaction.guild.get_role(role_id):
                    raise ValueError()
            except (ValueError, TypeError):
                return await interaction.response.send_message("❌ Invalid or non-existent Role ID.", ephemeral=True)

        # --- Calculate End Time ---
        value, unit = int(match.group(1)), match.group(2)
        if unit == 'm': delta = datetime.timedelta(minutes=value)
        elif unit == 'h': delta = datetime.timedelta(hours=value)
        else: delta = datetime.timedelta(days=value)
        end_time = datetime.datetime.now(datetime.timezone.utc) + delta

        giveaway_data = {
            "prize": self.prize.value,
            "end_time": end_time,
            "winner_count": winners,
            "required_role_id": role_id
        }
        
        view = PostGiveawayView(self.bot, giveaway_data)
        await interaction.response.send_message("Where should I post this giveaway?", view=view, ephemeral=True)


# --- Views for Giveaway Feature ---

class GiveawayView(View):
    """The public-facing view for entering a giveaway."""
    def __init__(self, bot: commands.Bot, giveaway_id: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.giveaway_id = giveaway_id
        
        enter_button = Button(label="Enter", emoji="🎉", style=discord.ButtonStyle.success, custom_id=f"giveaway:enter:{self.giveaway_id}")
        enter_button.callback = self.enter_giveaway
        self.add_item(enter_button)

    async def enter_giveaway(self, interaction: discord.Interaction):
        """Adds a user to the participant list for a giveaway."""
        giveaway = await self.bot.db.giveaways.find_one({"giveaway_id": self.giveaway_id})
        if not giveaway or giveaway.get("ended"):
            return await interaction.response.send_message("This giveaway has already ended.", ephemeral=True)

        if interaction.user.id in giveaway.get("participants", []):
            return await interaction.response.send_message("You have already entered this giveaway.", ephemeral=True)

        # Check for required role
        required_role_id = giveaway.get("required_role_id")
        if required_role_id and required_role_id not in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message(f"You need the <@&{required_role_id}> role to enter this giveaway.", ephemeral=True)
        
        await self.bot.db.giveaways.update_one(
            {"giveaway_id": self.giveaway_id},
            {"$push": {"participants": interaction.user.id}}
        )
        await interaction.response.send_message("You have successfully entered the giveaway!", ephemeral=True)

class GiveawaysSettingsView(BaseSettingsView):
    """View for managing giveaways."""
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        
        back_button = Button(label="◀️ Back", custom_id="giveaways:nav_essentials")
        back_button.callback = self.go_to_essentials
        self.add_item(back_button)

        create_button = Button(label="Create New Giveaway", emoji="➕", style=discord.ButtonStyle.success, custom_id="giveaways:create")
        create_button.callback = self.create_giveaway
        self.add_item(create_button)

    async def go_to_essentials(self, interaction: discord.Interaction):
        embed = create_embed("⭐ Essentials", "Configure welcome messages, reaction roles, and the leveling system.", discord.Color.gold())
        view = EssentialsSettingsView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def create_giveaway(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CreateGiveawayModal(self.bot))

class PostGiveawayView(View):
    """A view with a channel select to post a newly created giveaway."""
    def __init__(self, bot: commands.Bot, giveaway_data: dict):
        super().__init__(timeout=180)
        self.bot = bot
        self.giveaway_data = giveaway_data
        
        channel_selector = DiscordChannelSelect(placeholder="Select channel to post giveaway in...")
        channel_selector.callback = self.post_giveaway
        self.add_item(channel_selector)

    async def post_giveaway(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # CORRECTED: Fetch the full channel object from the guild using its ID
        selected_channel_partial = self.children[0].values[0]
        target_channel = interaction.guild.get_channel(selected_channel_partial.id)

        if not target_channel:
            logger.error(f"Could not find channel with ID {selected_channel_partial.id} in guild {interaction.guild.id}")
            return await interaction.followup.send("❌ Error: Could not find the selected channel.", ephemeral=True)
        
        giveaway_id = str(uuid.uuid4())
        end_time = self.giveaway_data["end_time"]
        
        embed = discord.Embed(
            title=f"🎉 Giveaway: {self.giveaway_data['prize']}",
            description=f"Click the 'Enter' button to join!\nEnds: {discord.utils.format_dt(end_time, style='R')}\nWinners: **{self.giveaway_data['winner_count']}**",
            color=discord.Color.gold(),
            timestamp=end_time
        ).set_footer(text="Ends at")

        try:
            giveaway_message = await target_channel.send(embed=embed, view=GiveawayView(self.bot, giveaway_id))
            
            giveaway_doc = {
                "giveaway_id": giveaway_id,
                "guild_id": interaction.guild.id,
                "channel_id": target_channel.id,
                "message_id": giveaway_message.id,
                "prize": self.giveaway_data["prize"],
                "end_time": end_time,
                "winner_count": self.giveaway_data["winner_count"],
                "required_role_id": self.giveaway_data["required_role_id"],
                "participants": [],
                "ended": False
            }
            await self.bot.db.giveaways.insert_one(giveaway_doc)
            
            await interaction.followup.send(f"✅ Giveaway posted in {target_channel.mention}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(f"❌ I don't have permission to send messages in {target_channel.mention}.", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to post giveaway in guild {interaction.guild.id}: {e}")
            await interaction.followup.send(STRINGS["db_error"], ephemeral=True)


# --- Cog Loader ---
class GiveawaysCog(commands.Cog):
    """A cog for handling giveaways."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    @tasks.loop(seconds=30)
    async def check_giveaways(self):
        """Checks for ended giveaways every 30 seconds."""
        now = datetime.datetime.now(datetime.timezone.utc)
        ended_giveaways = self.bot.db.giveaways.find({"end_time": {"$lte": now}, "ended": False})
        
        async for giveaway in ended_giveaways:
            guild = self.bot.get_guild(giveaway["guild_id"])
            if not guild: continue

            channel = guild.get_channel(giveaway["channel_id"])
            if not channel: continue

            try:
                message = await channel.fetch_message(giveaway["message_id"])
            except (discord.NotFound, discord.Forbidden):
                await self.bot.db.giveaways.update_one({"_id": giveaway["_id"]}, {"$set": {"ended": True}})
                continue

            participants = giveaway.get("participants", [])
            winners = []
            if participants:
                winner_count = min(giveaway["winner_count"], len(participants))
                winners = random.sample(participants, winner_count)

            # Announce winners
            winner_mentions = [f"<@{w_id}>" for w_id in winners]
            if winners:
                announcement = f"Congratulations {', '.join(winner_mentions)}! You won the **{giveaway['prize']}**!"
            else:
                announcement = "The giveaway has ended, but there were no participants."
            
            await channel.send(announcement, reference=message)

            # Update original message
            new_embed = message.embeds[0]
            new_embed.description = f"Giveaway has ended!\nWinners: {', '.join(winner_mentions) if winners else 'None'}"
            new_embed.color = discord.Color.dark_grey()
            await message.edit(embed=new_embed, view=None)

            await self.bot.db.giveaways.update_one({"_id": giveaway["_id"]}, {"$set": {"ended": True}})

    @check_giveaways.before_loop
    async def before_check_giveaways(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    """Adds the cog to the bot."""
    await bot.add_cog(GiveawaysCog(bot))
