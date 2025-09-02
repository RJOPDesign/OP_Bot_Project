import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput, UserSelect
import logging
from typing import Optional
import asyncio

# Import from the utils cog for consistency
from .utils import BaseSettingsView, create_embed, STRINGS

logger = logging.getLogger(__name__)

# --- Modals for Purge Feature ---

class PurgeFilterModal(Modal, title="Advanced Purge Options"):
    """A modal to configure advanced purge settings."""
    def __init__(self, bot: commands.Bot, target_user: Optional[discord.User] = None):
        super().__init__()
        self.bot = bot
        self.target_user = target_user
        self.amount = TextInput(
            label="Number of messages to check (1-1000)",
            required=True,
            max_length=4,
            placeholder="Enter a number between 1 and 1000"
        )
        self.keyword = TextInput(
            label="Keyword to filter (optional)",
            required=False,
            placeholder="Enter a keyword to match (case-insensitive)"
        )
        self.message_type = TextInput(
            label="Message type (optional: bot, embed, attachment)",
            required=False,
            placeholder="Enter 'bot', 'embed', or 'attachment' to filter"
        )
        self.add_item(self.amount)
        self.add_item(self.keyword)
        self.add_item(self.message_type)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # 1. Validate input
        try:
            amount = int(self.amount.value) if self.amount.value else 100
            if not 1 <= amount <= 1000:
                raise ValueError("Amount must be between 1 and 1000.")
        except ValueError as e:
            await interaction.response.send_message(f"❌ Invalid input: {e}. Please check your inputs.", ephemeral=True)
            return

        # 2. Check permissions
        if not interaction.channel.permissions_for(interaction.guild.me).manage_messages:
            await interaction.response.send_message("❌ I don't have the **Manage Messages** permission to do that.", ephemeral=True)
            return

        # This is the function that will do the actual purging.
        # It will be called with an interaction that is already deferred.
        async def do_purge(purge_interaction: discord.Interaction):
            try:
                # The interaction is already deferred, so we can proceed.
                def check(m):
                    if self.target_user and m.author != self.target_user:
                        return False
                    if self.keyword.value and self.keyword.value.lower() not in m.content.lower():
                        return False
                    if self.message_type.value:
                        msg_type = self.message_type.value.lower()
                        if msg_type == "bot" and not m.author.bot: return False
                        elif msg_type == "embed" and not m.embeds: return False
                        elif msg_type == "attachment" and not m.attachments: return False
                    return True

                # Use the original modal interaction's channel
                deleted_messages = await interaction.channel.purge(limit=amount, check=check, before=interaction.created_at)
                deleted_count = len(deleted_messages)

                # Log the purge action
                config = await self.bot.get_guild_config(interaction.guild.id)
                log_channel_id = config.get("logging", {}).get("log_channel_id")
                if log_channel_id:
                    log_channel = interaction.guild.get_channel(log_channel_id)
                    if log_channel:
                        filters = []
                        if self.target_user: filters.append(f"User: {self.target_user.mention}")
                        if self.keyword.value: filters.append(f"Keyword: `{self.keyword.value}`")
                        if self.message_type.value: filters.append(f"Type: `{self.message_type.value}`")
                        filter_str = ", ".join(filters) if filters else "None"
                        
                        embed = create_embed(
                            "🗑️ Messages Purged (Advanced)",
                            f"**Moderator**: {interaction.user.mention}\n"
                            f"**Channel**: {interaction.channel.mention}\n"
                            f"**Messages Deleted**: {deleted_count}\n"
                            f"**Filters**: {filter_str}",
                            discord.Color.dark_red()
                        )
                        await log_channel.send(embed=embed)
                
                # Use the purge_interaction to send the final confirmation
                await purge_interaction.followup.send(f"✅ Successfully deleted {deleted_count} messages.", ephemeral=True)

            except Exception as e:
                logger.error(f"Error during message purge in guild {interaction.guild.id}: {e}", exc_info=True)
                # Use the purge_interaction to send the error
                await purge_interaction.followup.send(STRINGS["db_error"], ephemeral=True)

        # 3. Handle confirmation flow
        if amount > 100:
            confirm_view = ConfirmationView()
            # Use the modal interaction to send the confirmation message
            await interaction.response.send_message(f"You are about to delete up to {amount} messages. Are you sure?", view=confirm_view, ephemeral=True)
            await confirm_view.wait()

            if confirm_view.value:
                # If confirmed, use the deferred button interaction to start the purge
                await do_purge(confirm_view.interaction)
            # If cancelled, the view handles the response itself.
        else:
            # No confirmation needed, defer the modal interaction and start the purge
            await interaction.response.defer(ephemeral=True)
            await do_purge(interaction)


# --- Confirmation View (CORRECTED) ---
class ConfirmationView(View):
    def __init__(self):
        super().__init__(timeout=60)
        self.value = None
        self.interaction: Optional[discord.Interaction] = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        self.value = True
        self.interaction = interaction
        # Disable buttons to prevent double-clicks
        for item in self.children:
            item.disabled = True
        # Defer the interaction instead of editing. The caller will handle the response.
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        self.value = False
        self.interaction = interaction
        for item in self.children:
            item.disabled = True
        # Respond directly here since it's a final action.
        await interaction.response.edit_message(content="❌ Purge cancelled.", view=None)
        self.stop()

# --- Purge Settings View ---

class PurgeSettingsView(BaseSettingsView):
    """The settings menu for the message purging feature."""
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

        back_button = Button(label="◀️ Back to Staff Menu", style=discord.ButtonStyle.primary, custom_id="purge:nav_staff_main")
        back_button.callback = self.go_to_staff_main
        self.add_item(back_button)

        purge_user_button = Button(label="Purge by User", emoji="👤", custom_id="purge:user")
        purge_user_button.callback = self.purge_user
        self.add_item(purge_user_button)

        purge_advanced_button = Button(label="Advanced Purge", emoji="⚙️", custom_id="purge:advanced")
        purge_advanced_button.callback = self.purge_advanced
        self.add_item(purge_advanced_button)

    async def go_to_staff_main(self, interaction: discord.Interaction):
        """Returns to the main staff menu."""
        from .menu_cog import StaffMenuView
        embed = create_embed("🛡️ Staff Menu", "Select a category to configure.", discord.Color.blue())
        view = StaffMenuView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def purge_user(self, interaction: discord.Interaction):
        """Opens a view to select a user to purge messages from."""
        view = View(timeout=180)
        user_select = UserSelect(placeholder="Select a user...")

        async def user_select_callback(select_interaction: discord.Interaction):
            target_user = user_select.values[0]
            await select_interaction.response.send_modal(PurgeFilterModal(self.bot, target_user))

        async def on_timeout():
            try:
                await interaction.edit_original_response(content="❌ User selection timed out. Please try again.", view=None)
            except discord.NotFound:
                logger.warning("Original interaction not found during timeout handling.")

        view.on_timeout = on_timeout
        user_select.callback = user_select_callback
        view.add_item(user_select)
        await interaction.response.send_message("Please select the user whose messages you want to purge:", view=view, ephemeral=True)

    async def purge_advanced(self, interaction: discord.Interaction):
        """Opens a modal for advanced purge options."""
        await interaction.response.send_modal(PurgeFilterModal(self.bot))

# --- Cog Loader ---
class PurgeCog(commands.Cog):
    """A cog for handling message purging."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

async def setup(bot: commands.Bot):
    """Adds the cog to the bot."""
    await bot.add_cog(PurgeCog(bot))
