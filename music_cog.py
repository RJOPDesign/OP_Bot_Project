import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput, Select
import logging
import asyncio
import yt_dlp
from typing import Optional, List
import random

# Import from the utils cog for consistency
from .utils import BaseSettingsView, create_embed, STRINGS

logger = logging.getLogger(__name__)

# --- YouTube-DL Configuration ---
yt_dlp.utils.bug_reports_message = lambda: ''
YTDL_FORMAT_OPTIONS = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}
ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)

class YTDLSource(discord.PCMVolumeTransformer):
    """A class to handle streaming audio from YouTube."""
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('webpage_url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data)

# --- Music Player Class ---
class MusicPlayer:
    """A class to manage the music queue and playback for a single guild."""
    def __init__(self, bot: commands.Bot, guild: discord.Guild):
        self.bot = bot
        self.guild = guild
        self.queue = []
        self.next = asyncio.Event()
        self.current = None
        self.autoplay = False
        self.bot.loop.create_task(self.player_loop())

    def _after_play(self, error):
        """Callback for when a song finishes playing."""
        if error:
            logger.error(f"Error in player_loop for guild {self.guild.id}: {error}")
        self.bot.loop.call_soon_threadsafe(self.next.set)

    async def player_loop(self):
        """The main loop that plays songs from the queue."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            self.next.clear()
            
            if not self.queue:
                if self.autoplay and self.current:
                    try:
                        # Simple autoplay: search for a mix of the last song
                        query = f"{self.current['title']} mix"
                        source = await YTDLSource.from_url(query, loop=self.bot.loop, stream=True)
                        song = {
                            'source': source,
                            'title': source.title,
                            'url': source.url,
                            'requester': self.bot.user,
                            'channel': self.current['channel']
                        }
                        self.queue.append(song)
                        await self.current['channel'].send(f"🔄 Autoplaying: **{source.title}**")
                    except Exception as e:
                        logger.error(f"Autoplay failed for guild {self.guild.id}: {e}")
                        self.current = None # Stop trying if it fails
                else:
                    await asyncio.sleep(300) # Wait 5 minutes before disconnecting
                    if not self.queue and self.guild.voice_client:
                        await self.guild.voice_client.disconnect()
                        self.bot.get_cog("MusicCog").players.pop(self.guild.id, None)
                        return

            self.current = self.queue.pop(0)

            try:
                player = await YTDLSource.from_url(self.current['url'], loop=self.bot.loop, stream=True)
                if self.guild.voice_client:
                    self.guild.voice_client.play(player, after=self._after_play)
                    if self.current['requester'] != self.bot.user: # Don't send "Now playing" for autoplay
                        await self.current['channel'].send(f"Now playing: **{player.title}**")
                else:
                    logger.warning(f"Voice client disconnected unexpectedly in guild {self.guild.id}")
                    self.next.set() # Move to next song if client is gone
            except Exception as e:
                logger.error(f"Error playing song in guild {self.guild.id}: {e}")
                await self.current['channel'].send("❌ An error occurred while trying to play this song.")
                self.next.set()

            await self.next.wait()

# --- Music Player Views & Modals ---

class MusicPlayerView(BaseSettingsView):
    """The main view for the music player."""
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

        # Row 1
        back_button = Button(label="◀️ Back", custom_id="music:back_to_main_menu")
        back_button.callback = self.go_to_main_menu
        self.add_item(back_button)
        
        play_button = Button(label="Play", emoji="▶️", style=discord.ButtonStyle.success, custom_id="music:play")
        play_button.callback = self.play_song
        self.add_item(play_button)

        pause_button = Button(label="Pause/Resume", emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="music:pause")
        pause_button.callback = self.pause_resume
        self.add_item(pause_button)

        skip_button = Button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="music:skip")
        skip_button.callback = self.skip_song
        self.add_item(skip_button)

        stop_button = Button(label="Stop & Leave", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="music:stop")
        stop_button.callback = self.stop_player
        self.add_item(stop_button)
        
        # Row 2
        queue_button = Button(label="Queue", emoji="📜", custom_id="music:queue", row=1)
        queue_button.callback = self.show_queue
        self.add_item(queue_button)

        clear_button = Button(label="Clear Queue", emoji="🗑️", custom_id="music:clear", row=1)
        clear_button.callback = self.clear_queue
        self.add_item(clear_button)

        shuffle_button = Button(label="Shuffle", emoji="🔀", custom_id="music:shuffle", row=1)
        shuffle_button.callback = self.shuffle_queue
        self.add_item(shuffle_button)

        np_button = Button(label="Now Playing", emoji="🎶", custom_id="music:np", row=1)
        np_button.callback = self.now_playing
        self.add_item(np_button)

        # Row 3
        play_next_button = Button(label="Play Next", emoji="⬆️", custom_id="music:playnext", row=2)
        play_next_button.callback = self.play_next
        self.add_item(play_next_button)

        play_skip_button = Button(label="Play Skip", emoji="⏯️", custom_id="music:playskip", row=2)
        play_skip_button.callback = self.play_skip
        self.add_item(play_skip_button)

        autoplay_button = Button(label="Autoplay", emoji="🔄", custom_id="music:autoplay", row=2)
        autoplay_button.callback = self.toggle_autoplay
        self.add_item(autoplay_button)

        grab_button = Button(label="Grab Song", emoji="📥", custom_id="music:grab", row=2)
        grab_button.callback = self.grab_song
        self.add_item(grab_button)
        
        # Row 4
        playlists_button = Button(label="Playlists", emoji="🎵", style=discord.ButtonStyle.primary, custom_id="music:playlists", row=3)
        playlists_button.callback = self.go_to_playlists
        self.add_item(playlists_button)

    async def go_to_main_menu(self, interaction: discord.Interaction):
        """Returns to the initial Music/Staff selection menu."""
        from .menu_cog import MainMenuSelectionView
        embed = create_embed("👋 Welcome!", "Please choose a menu to continue.", discord.Color.blurple())
        view = MainMenuSelectionView(self.bot)
        await interaction.response.edit_message(embed=embed, view=view)

    async def play_song(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PlaySongModal(self.bot, play_next=False))

    async def play_next(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PlaySongModal(self.bot, play_next=True))
        
    async def play_skip(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PlaySongModal(self.bot, play_next=True, skip_current=True))

    async def pause_resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc: return await interaction.response.send_message("I am not connected to a voice channel.", ephemeral=True)
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("Paused the music.", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message("Resumed the music.", ephemeral=True)
        else:
            await interaction.response.send_message("Not currently playing anything.", ephemeral=True)

    async def skip_song(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("Skipped the current song.", ephemeral=True)
        else:
            await interaction.response.send_message("Not currently playing anything to skip.", ephemeral=True)

    async def stop_player(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        player = await cog.get_player(interaction)
        player.queue.clear()
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message("Music stopped and disconnected.", ephemeral=True)
        else:
            await interaction.response.send_message("Not connected to a voice channel.", ephemeral=True)

    async def show_queue(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.show_queue(interaction)

    async def clear_queue(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.clear_queue(interaction)

    async def shuffle_queue(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.shuffle_queue(interaction)

    async def grab_song(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.grab_song(interaction)

    async def toggle_autoplay(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.toggle_autoplay(interaction)
        
    async def now_playing(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.now_playing(interaction)

    async def go_to_playlists(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.show_playlists_menu(interaction)


class PlaySongModal(Modal, title="Play a Song"):
    """A modal to get a song URL or search query."""
    def __init__(self, bot: commands.Bot, play_next: bool = False, skip_current: bool = False):
        super().__init__()
        self.bot = bot
        self.play_next = play_next
        self.skip_current = skip_current
        self.song_query = TextInput(label="Song Name or YouTube URL", placeholder="e.g., Never Gonna Give You Up", required=True)
        self.add_item(self.song_query)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("MusicCog")
        await cog.play(interaction, self.song_query.value, play_next=self.play_next, skip_current=self.skip_current)

class CreatePlaylistModal(Modal, title="Create Playlist"):
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.playlist_name = TextInput(label="Playlist Name", required=True, max_length=50)
        self.add_item(self.playlist_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("MusicCog")
        await cog.create_playlist(interaction, self.playlist_name.value)

class PlaylistsView(BaseSettingsView):
    """View for managing playlists."""
    def __init__(self, bot: commands.Bot, playlists: list):
        super().__init__(bot)
        
        back_button = Button(label="◀️ Back", custom_id="playlist:back")
        back_button.callback = self.go_back
        self.add_item(back_button)

        create_button = Button(label="Create", emoji="➕", custom_id="playlist:create")
        create_button.callback = self.create_playlist
        self.add_item(create_button)

        add_button = Button(label="Add Current Song", emoji="📥", custom_id="playlist:add")
        add_button.callback = self.add_to_playlist
        self.add_item(add_button)
        
        if playlists:
            options = [discord.SelectOption(label=p['name'], value=p['name']) for p in playlists[:25]]
            
            view_select = Select(placeholder="View a playlist...", options=options, custom_id="playlist:view")
            view_select.callback = self.view_playlist
            self.add_item(view_select)

            load_select = Select(placeholder="Load a playlist...", options=options, custom_id="playlist:load")
            load_select.callback = self.load_playlist
            self.add_item(load_select)

            delete_select = Select(placeholder="Delete a playlist...", options=options, custom_id="playlist:delete")
            delete_select.callback = self.delete_playlist
            self.add_item(delete_select)

    async def go_back(self, interaction: discord.Interaction):
        embed = create_embed("🎶 Music Player", "Use the buttons below to control the music.", discord.Color.purple())
        await interaction.response.edit_message(embed=embed, view=MusicPlayerView(self.bot))

    async def create_playlist(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CreatePlaylistModal(self.bot))

    async def add_to_playlist(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.add_song_to_playlist_prompt(interaction)
        
    async def view_playlist(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.view_playlist(interaction, interaction.data['values'][0])
        
    async def load_playlist(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.load_playlist(interaction, interaction.data['values'][0])
        
    async def delete_playlist(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("MusicCog")
        await cog.delete_playlist(interaction, interaction.data['values'][0])


# --- Cog Loader ---
class MusicCog(commands.Cog):
    """A cog for handling music features."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players = {}

    async def get_player(self, interaction: discord.Interaction) -> MusicPlayer:
        """Retrieves the music player for a guild, creating it if it doesn't exist."""
        if interaction.guild.id not in self.players:
            self.players[interaction.guild.id] = MusicPlayer(self.bot, interaction.guild)
        return self.players[interaction.guild.id]

    async def play(self, interaction: discord.Interaction, query: str, play_next: bool = False, skip_current: bool = False):
        """Handles the song playing logic."""
        if not interaction.user.voice:
            return await interaction.response.send_message("❌ You must be in a voice channel to play music.", ephemeral=True)

        await interaction.response.send_message(f"🎵 Searching for `{query}`...", ephemeral=True)
        
        voice_channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if not vc:
            try:
                vc = await voice_channel.connect(timeout=30.0)
            except asyncio.TimeoutError:
                return await interaction.followup.send("❌ Could not connect to the voice channel in time.", ephemeral=True)
            except discord.ClientException:
                return await interaction.followup.send("❌ Already connected to a voice channel.", ephemeral=True)
        
        player = await self.get_player(interaction)
        
        try:
            source = await YTDLSource.from_url(query, loop=self.bot.loop, stream=True)
            song = {
                'source': source,
                'title': source.title,
                'url': source.url,
                'requester': interaction.user,
                'channel': interaction.channel
            }
            
            if play_next:
                player.queue.insert(0, song)
                await interaction.followup.send(f"Added to front of queue: **{source.title}**", ephemeral=True)
            else:
                player.queue.append(song)
                await interaction.followup.send(f"Added to queue: **{source.title}**", ephemeral=True)

            if skip_current and vc.is_playing():
                vc.stop()

        except Exception as e:
            logger.error(f"Error playing song: {e}")
            await interaction.followup.send("❌ An error occurred while trying to play this song.", ephemeral=True)

    async def now_playing(self, interaction: discord.Interaction):
        """Displays the currently playing song."""
        player = await self.get_player(interaction)
        if not player.current:
            return await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)
        
        embed = create_embed("🎶 Now Playing", f"**[{player.current['title']}]({player.current['url']})**\nRequested by: {player.current['requester'].mention}", discord.Color.purple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def show_queue(self, interaction: discord.Interaction):
        """Displays the current music queue."""
        player = await self.get_player(interaction)
        if not player.queue and not player.current:
            return await interaction.response.send_message("The queue is empty.", ephemeral=True)
        
        embed = create_embed("📜 Music Queue", "", discord.Color.purple())
        if player.current:
            embed.description += f"**Now Playing:** {player.current['title']}\n\n"
        
        for i, song in enumerate(player.queue[:10]):
            embed.description += f"`{i+1}.` {song['title']}\n"
            
        if len(player.queue) > 10:
            embed.set_footer(text=f"...and {len(player.queue) - 10} more.")
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def clear_queue(self, interaction: discord.Interaction):
        """Clears the music queue."""
        player = await self.get_player(interaction)
        player.queue.clear()
        await interaction.response.send_message("🗑️ Queue cleared.", ephemeral=True)

    async def shuffle_queue(self, interaction: discord.Interaction):
        """Shuffles the music queue."""
        player = await self.get_player(interaction)
        random.shuffle(player.queue)
        await interaction.response.send_message("🔀 Queue shuffled.", ephemeral=True)

    async def grab_song(self, interaction: discord.Interaction):
        """Sends the current song to the user's DMs."""
        player = await self.get_player(interaction)
        if not player.current:
            return await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)
        
        try:
            await interaction.user.send(f"Here's the song you requested: {player.current['url']}")
            await interaction.response.send_message("✅ Sent the song to your DMs.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I couldn't send you a DM. Please check your privacy settings.", ephemeral=True)

    async def toggle_autoplay(self, interaction: discord.Interaction):
        """Toggles autoplay for the music player."""
        player = await self.get_player(interaction)
        player.autoplay = not player.autoplay
        await interaction.response.send_message(f"Autoplay is now {'ON' if player.autoplay else 'OFF'}.", ephemeral=True)
        
    # --- Playlist Methods ---
    
    async def show_playlists_menu(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        playlists = await self.bot.db.playlists.find({"user_id": interaction.user.id}).to_list(length=25)
        embed = create_embed("🎵 Playlists", "Manage your saved playlists.", discord.Color.dark_purple())
        await interaction.followup.send(embed=embed, view=PlaylistsView(self.bot, playlists), ephemeral=True)

    async def create_playlist(self, interaction: discord.Interaction, name: str):
        existing = await self.bot.db.playlists.find_one({"user_id": interaction.user.id, "name": name})
        if existing:
            return await interaction.response.send_message("A playlist with that name already exists.", ephemeral=True)
        
        await self.bot.db.playlists.insert_one({"user_id": interaction.user.id, "name": name, "songs": []})
        await interaction.response.send_message(f"✅ Playlist '{name}' created!", ephemeral=True)
        await self.show_playlists_menu(interaction) # Refresh menu

    async def add_song_to_playlist_prompt(self, interaction: discord.Interaction):
        player = await self.get_player(interaction)
        if not player.current:
            return await interaction.response.send_message("There is no song currently playing.", ephemeral=True)
            
        playlists = await self.bot.db.playlists.find({"user_id": interaction.user.id}).to_list(length=25)
        if not playlists:
            return await interaction.response.send_message("You have no playlists. Create one first!", ephemeral=True)
        
        options = [discord.SelectOption(label=p['name'], value=p['name']) for p in playlists]
        
        class AddToPlaylistSelect(View):
            def __init__(self, bot):
                super().__init__()
                self.bot = bot
                select = Select(placeholder="Choose a playlist to add to...", options=options)
                select.callback = self.select_callback
                self.add_item(select)
            
            async def select_callback(self, select_interaction: discord.Interaction):
                cog = self.bot.get_cog("MusicCog")
                await cog.add_song_to_playlist(select_interaction, select_interaction.data['values'][0])

        await interaction.response.send_message("Select a playlist to add the current song to:", view=AddToPlaylistSelect(self.bot), ephemeral=True)

    async def add_song_to_playlist(self, interaction: discord.Interaction, playlist_name: str):
        player = await self.get_player(interaction)
        song_to_add = {"title": player.current['title'], "url": player.current['url']}
        
        await self.bot.db.playlists.update_one(
            {"user_id": interaction.user.id, "name": playlist_name},
            {"$push": {"songs": song_to_add}}
        )
        await interaction.response.send_message(f"✅ Added '{song_to_add['title']}' to playlist '{playlist_name}'!", ephemeral=True)

    async def view_playlist(self, interaction: discord.Interaction, playlist_name: str):
        await interaction.response.defer(ephemeral=True)
        playlist = await self.bot.db.playlists.find_one({"user_id": interaction.user.id, "name": playlist_name})
        
        embed = create_embed(f"🎵 Playlist: {playlist_name}", "", discord.Color.dark_purple())
        description = ""
        for i, song in enumerate(playlist.get("songs", [])[:20]):
            description += f"`{i+1}.` {song['title']}\n"
        
        embed.description = description if description else "This playlist is empty."
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def load_playlist(self, interaction: discord.Interaction, playlist_name: str):
        await interaction.response.send_message(f"Loading playlist '{playlist_name}'...", ephemeral=True)
        playlist = await self.bot.db.playlists.find_one({"user_id": interaction.user.id, "name": playlist_name})
        player = await self.get_player(interaction)

        for song in playlist.get("songs", []):
            song_data = {
                'url': song['url'],
                'title': song['title'],
                'requester': interaction.user,
                'channel': interaction.channel
            }
            player.queue.append(song_data)
            
        if not interaction.guild.voice_client.is_playing():
            player.next.set()

    async def delete_playlist(self, interaction: discord.Interaction, playlist_name: str):
        await self.bot.db.playlists.delete_one({"user_id": interaction.user.id, "name": playlist_name})
        await interaction.response.send_message(f"✅ Playlist '{playlist_name}' deleted.", ephemeral=True)
        await self.show_playlists_menu(interaction) # Refresh menu

async def setup(bot: commands.Bot):
    """Adds the cog to the bot."""
    await bot.add_cog(MusicCog(bot))
