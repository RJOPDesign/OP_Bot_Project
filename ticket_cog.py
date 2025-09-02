import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput, ChannelSelect
import io
import datetime
import logging
import re
import uuid

# Import from the utils cog for consistency
from .utils import BaseSettingsView, create_embed, STRINGS

logger = logging.getLogger(__name__)

# --- Modals ---

class CreateTicketPanelModal(Modal, title="Create New Ticket Panel"):
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.panel_title = TextInput(label="Panel Title", placeholder="e.g., General Support", max_length=100)
        self.panel_description = TextInput(label="Panel Description", style=discord.TextStyle.long, placeholder="Click the button below to open a ticket.", max_length=1000)
        self.support_role_id = TextInput(label="Support Role ID (Optional)", required=False)
        self.add_item(self.panel_title)
        self.add_item(self.panel_description)
        self.add_item(self.support_role_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        panel_id = str(uuid.uuid4())
        support_role = None
        if self.support_role_id.value:
            try:
                role_id = int(self.support_role_id.value)
                if not interaction.guild.get_role(role_id):
                    raise ValueError("Role not found")
                support_role = role_id
            except (ValueError, TypeError):
                return await interaction.response.send_message("Invalid Role ID.", ephemeral=True)
        
        new_panel = {
            "panel_id": panel_id, 
            "title": self.panel_title.value, 
            "description": self.panel_description.value, 
            "support_role_id": support_role
        }
        await self.bot.db.configs.update_one({"guild_id": interaction.guild.id}, {"$push": {"tickets.panels": new_panel}}, upsert=True)
        logger.info(f"Ticket panel '{panel_id}' created for guild {interaction.guild.id} by {interaction.user}")
        
        view = PostPanelView(self.bot, panel_id)
        await interaction.response.send_message("✅ Panel created! Where should I post it?", view=view, ephemeral=True)

# --- Views ---

class PostPanelView(BaseSettingsView):
    """A view with a channel select to post a newly created ticket panel."""
    def __init__(self, bot: commands.Bot, panel_id: str):
        super().__init__(bot)
        self.panel_id = panel_id
        self.channel_selector = ChannelSelect(placeholder="Select channel to post panel in...")
        self.channel_selector.callback = self.post_panel_callback
        self.add_item(self.channel_selector)

    async def post_panel_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        
        # CORRECTED: Fetch the full channel object from the guild using its ID
        selected_channel_partial = self.channel_selector.values[0]
        selected_channel = interaction.guild.get_channel(selected_channel_partial.id)

        if not selected_channel:
            logger.error(f"Could not find channel with ID {selected_channel_partial.id} in guild {interaction.guild.id}")
            return await interaction.followup.send("❌ Error: Could not find the selected channel.", ephemeral=True)

        guild_config = await self.bot.get_guild_config(interaction.guild.id) or {}
        panel_data = next((p for p in guild_config.get("tickets", {}).get("panels", []) if p["panel_id"] == self.panel_id), None)
        if not panel_data:
            logger.error(f"Panel data not found for panel_id {self.panel_id} in guild {interaction.guild.id}")
            return await interaction.followup.send("❌ Error: Could not find panel data.", ephemeral=True)

        panel_embed = create_embed(panel_data["title"], panel_data["description"], discord.Color.dark_green())
        
        try:
            await selected_channel.send(embed=panel_embed, view=TicketPanelView(self.bot, self.panel_id))
            logger.info(f"Ticket panel posted in channel {selected_channel.id} for guild {interaction.guild.id}")
            await interaction.followup.send(f"✅ Ticket panel posted in {selected_channel.mention}!", ephemeral=True)
        except discord.Forbidden:
            logger.error(f"Forbidden error posting to channel {selected_channel.id} in guild {interaction.guild.id}")
            await interaction.followup.send("❌ I don't have permission to send messages there.", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to post ticket panel in guild {interaction.guild.id}: {e}")
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)


class TicketSettingsView(BaseSettingsView):
    """View for configuring the ticket system."""
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        
        back_button = Button(label="◀️ Back", custom_id="tickets:nav_staff")
        back_button.callback = self.go_to_staff_menu
        self.add_item(back_button)

        create_panel_button = Button(label="Create New Ticket Panel", emoji="➕", style=discord.ButtonStyle.success, custom_id="tickets:create_panel")
        create_panel_button.callback = self.create_panel
        self.add_item(create_panel_button)

        transcripts_channel_select = ChannelSelect(placeholder="Select a transcript channel...", custom_id="tickets:set_transcripts_channel")
        transcripts_channel_select.callback = self.set_transcripts_channel
        self.add_item(transcripts_channel_select)

    async def go_to_staff_menu(self, interaction: discord.Interaction) -> None:
        from .menu_cog import StaffMenuView
        embed = create_embed("🛡️ Staff Menu", "Select a category to configure.", discord.Color.blue())
        await interaction.response.edit_message(embed=embed, view=StaffMenuView(self.bot))

    async def create_panel(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CreateTicketPanelModal(self.bot))

    async def set_transcripts_channel(self, interaction: discord.Interaction):
        channel_id = int(interaction.data["values"][0])
        await self.bot.db.configs.update_one({"guild_id": interaction.guild.id}, {"$set": {"tickets.transcripts_channel_id": channel_id}}, upsert=True)
        await interaction.response.send_message(f"✅ Transcripts channel set to <#{channel_id}>!", ephemeral=True)


class TicketActionsView(View):
    """A persistent view containing buttons for actions inside an open ticket."""
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _generate_transcript(self, channel: discord.TextChannel) -> io.BytesIO:
        """Creates an HTML transcript of the ticket."""
        messages = [message async for message in channel.history(limit=1000, oldest_first=True)]
        
        transcript_html = f"""<html><head><title>Ticket Transcript: {channel.name}</title>
        <style>body {{ font-family: sans-serif; background-color: #36393f; color: #dcddde; }} 
        .message {{ margin-bottom: 1em; }} 
        .author {{ font-weight: bold; color: #ffffff; }} 
        .timestamp {{ font-size: 0.8em; color: #72767d; margin-left: 10px; }}
        </style></head><body><h1>Transcript for ticket #{channel.name}</h1>"""

        for msg in messages:
            transcript_html += f"""<div class="message">
            <span class="author">{msg.author.display_name}</span>
            <span class="timestamp">{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC</span>
            <p>{msg.content}</p></div>"""
        transcript_html += "</body></html>"
        
        return io.BytesIO(transcript_html.encode('utf-8'))

    @discord.ui.button(label="Close & Archive", style=discord.ButtonStyle.danger, custom_id="ticket:close_archive_button", emoji="🔒")
    async def close_button(self, interaction: discord.Interaction, button: Button):
        """Closes the ticket, sends a transcript, and deletes the channel."""
        await interaction.response.defer()

        try:
            config = await self.bot.get_guild_config(interaction.guild.id)
            if not config:
                await interaction.followup.send("⚠️ Guild configuration not found. Cannot close ticket.", ephemeral=True)
                return
        except Exception as e:
            logger.error(f"Error fetching guild config in ticket close: {e}")
            await interaction.followup.send("⚠️ An error occurred while accessing guild configuration.", ephemeral=True)
            return

        ticket_config = config.get("tickets", {})
        transcripts_channel_id = ticket_config.get("transcripts_channel_id")

        if transcripts_channel_id and isinstance(interaction.channel, discord.TextChannel):
            transcript_channel = interaction.guild.get_channel(transcripts_channel_id)
            if transcript_channel:
                try:
                    transcript_file = await self._generate_transcript(interaction.channel)
                    await transcript_channel.send(
                        f"Transcript for ticket `{interaction.channel.name}` closed by {interaction.user.mention}.",
                        file=discord.File(transcript_file, filename=f"{interaction.channel.name}-transcript.html")
                    )
                except Exception as e:
                    logger.error(f"Failed to create or send transcript for ticket {interaction.channel.id}: {e}")
                    await interaction.followup.send("⚠️ Failed to save transcript, but closing ticket anyway.", ephemeral=True)
        
        try:
            await interaction.followup.send("✅ This ticket has been closed and will be deleted shortly.")
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.errors.NotFound:
            pass
        except discord.Forbidden:
             await interaction.followup.send("⚠️ I lack the **Manage Channels** permission to delete this ticket.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error during ticket deletion for channel {interaction.channel.id}: {e}")
            await interaction.followup.send("⚠️ An unexpected error occurred while trying to delete the ticket channel.", ephemeral=True)


class TicketPanelView(View):
    """A persistent view with a 'Create Ticket' button."""
    def __init__(self, bot: commands.Bot, panel_id: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.panel_id = panel_id
        
        create_ticket_button = Button(
            label="Create Ticket",
            style=discord.ButtonStyle.success,
            emoji="🎫",
            custom_id=f"ticket:create_button:{self.panel_id}"
        )
        create_ticket_button.callback = self.create_button
        self.add_item(create_ticket_button)

    async def create_button(self, interaction: discord.Interaction):
        """Creates a new private ticket channel for the user."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild.me.guild_permissions.manage_channels:
            await interaction.followup.send("❌ I need the **Manage Channels** permission to create tickets.", ephemeral=True)
            return

        sanitized_name = re.sub(r'[^a-z0-9-]', '', interaction.user.name.lower())
        if not sanitized_name: sanitized_name = "user"
        channel_name = f"ticket-{sanitized_name}-{interaction.user.id % 1000}"[:100]

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        config = await self.bot.get_guild_config(interaction.guild.id) or {}
        
        # Robust panel data lookup using panel_id from custom_id
        panel_id_from_button = interaction.data["custom_id"].split(":")[-1]
        panel_data = next((p for p in config.get("tickets", {}).get("panels", []) if p["panel_id"] == panel_id_from_button), None)
        
        support_role_id = panel_data.get("support_role_id") if panel_data else None
        support_role = None
        if support_role_id:
            support_role = interaction.guild.get_role(support_role_id)
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        category = interaction.channel.category

        try:
            ticket_channel = await interaction.guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=category,
                reason=f"Ticket created by {interaction.user}"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permissions to create a channel here.", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"Unexpected error creating ticket channel for {interaction.user}: {e}")
            await interaction.followup.send("❌ An unexpected error occurred. Please try again later.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Support Ticket Opened",
            description=f"Welcome, {interaction.user.mention}!\n\nA staff member will be with you shortly. Please describe your issue in detail.",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        
        content = f"👋 {interaction.user.mention}"
        if support_role:
            content += f" {support_role.mention}"

        await ticket_channel.send(content=content, embed=embed, view=TicketActionsView(self.bot))
        await interaction.followup.send(f"✅ Your ticket has been created in {ticket_channel.mention}!", ephemeral=True)


class TicketCog(commands.Cog):
    """A cog for managing the ticket system."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

async def setup(bot: commands.Bot):
    """Adds the cog to the bot."""
    await bot.add_cog(TicketCog(bot))
    logger.info("TicketCog loaded.")
