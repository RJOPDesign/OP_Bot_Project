import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure, OperationFailure, NetworkTimeout
import logging
from logging.handlers import RotatingFileHandler
from typing import List
import asyncio
from copy import deepcopy

# --- Setup Logging ---
log_dir = 'logs'
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)
# Use RotatingFileHandler to prevent log files from growing indefinitely
handler = RotatingFileHandler(filename=f'{log_dir}/discord.log', encoding='utf-8', mode='a', maxBytes=5*1024*1024, backupCount=2)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger.addHandler(handler)


# --- Load and Validate Environment Variables ---
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
# Configurable DB connection settings with defaults
MONGO_MAX_POOL_SIZE = int(os.getenv('MONGO_MAX_POOL_SIZE', 100))
MONGO_MIN_POOL_SIZE = int(os.getenv('MONGO_MIN_POOL_SIZE', 10))
MONGO_MAX_RETRIES = int(os.getenv('MONGO_MAX_RETRIES', 3))
MONGO_RETRY_DELAY = float(os.getenv('MONGO_RETRY_DELAY', 2))


def validate_env_vars():
    """Ensures all required environment variables are set."""
    if not TOKEN:
        logger.critical("❌ DISCORD_BOT_TOKEN environment variable not found.")
        raise SystemExit("Missing DISCORD_BOT_TOKEN")
    if not MONGO_URI:
        logger.critical("❌ MONGO_URI environment variable not found.")
        raise SystemExit("Missing MONGO_URI")
    if MONGO_MAX_POOL_SIZE < MONGO_MIN_POOL_SIZE:
        logger.critical("❌ MONGO_MAX_POOL_SIZE cannot be less than MONGO_MIN_POOL_SIZE.")
        raise SystemExit("Invalid MongoDB pool sizes")

# --- Default Guild Configuration ---
DEFAULT_CONFIG = {
    "guild_id": None,
    "ai_moderation": {
        "enabled": True, "exempt_roles": [], "exempt_users": [], "log_channel_id": None,
        "actions": {"warn_user": True, "delete_message": True, "timeout_user": False},
        "filters": {
            "toxicity": {"enabled": True, "sensitivity": 0.85}, "hate_speech": {"enabled": True, "sensitivity": 0.90},
            "spam": {"enabled": True, "sensitivity": 3}, "profanity": {"enabled": True, "custom_word_list": []},
            "server_invites": {"enabled": True}
        }
    },
    "anti_raid": {
        "enabled": True, "log_channel_id": None, "alert_role_id": None,
        "join_gate": {"enabled": True, "joins": 10, "seconds": 15},
        "account_age_gate": {"enabled": True, "hours": 24},
        "anti_nuke": {"enabled": False, "action_threshold": 5, "timeframe_seconds": 10}
    },
    "logging": {
        "enabled": True, "log_channel_id": None,
        "enabled_events": [
            "message_delete", "message_edit", "member_join", "member_leave", 
            "member_role_update", "member_nick_update", "channel_events"
        ]
    },
    "tickets": {
        "enabled": True, "log_channel_id": None, "panels": [], "transcripts_channel_id": None
    },
    "welcome": {
        "enabled": False,
        "welcome_channel_id": None,
        "goodbye_channel_id": None,
        "welcome_message": "Welcome {user} to {server}!",
        "goodbye_message": "{user} has left the server.",
        "welcome_dm": "Welcome to {server}, {user}!"
    },
    "levels": {
        "enabled": False,
        "channel_id": None,
        "level_up_message": "Congrats {user}, you've reached level {level}!"
    },
    "invites": {
        "enabled": False,
        "rewards": [],
        "no_invite_roles": []
    },
    "statistics": {
        "enabled": False,
        "channel_id": None
    },
    "temp_channels": {
        "enabled": False,
        "create_channel_id": None,
        "category_id": None
    },
    "social_alerts": {
        "enabled": False
    },
    "achievements": {
        "enabled": False,
        "achievements": []
    },
    "reaction_roles": [],
    "birthdays": {
        "enabled": False,
        "channel_id": None,
        "role_id": None,
        "dm_on_join": False
    },
    "economy": {
        "enabled": True
    }
}

class OPBot(commands.Bot):
    """The main bot class for OPBot. Uses slash commands only."""
    def __init__(self):
        intents = discord.Intents(
            guilds=True,
            members=True,
            messages=True,
            message_content=True,
            reactions=True,
            voice_states=True
        )
        super().__init__(command_prefix=None, intents=intents)
        self.db = None
        self.mongo_client = None
        self._default_config = deepcopy(DEFAULT_CONFIG) # Cache the default config

    async def close(self):
        """Clean up resources before shutting down."""
        logger.info("🛑 Shutting down bot...")
        if self.mongo_client:
            self.mongo_client.close()
            logger.info("✅ MongoDB client closed.")
        await super().close()

    async def setup_database(self):
        """Initializes the async database connection with exponential backoff."""
        for attempt in range(MONGO_MAX_RETRIES):
            try:
                self.mongo_client = AsyncIOMotorClient(
                    MONGO_URI,
                    maxPoolSize=MONGO_MAX_POOL_SIZE,
                    minPoolSize=MONGO_MIN_POOL_SIZE
                )
                await self.mongo_client.admin.command('ismaster')
                self.db = self.mongo_client['OP_Bot_DB']
                # Create indexes for performance
                await self.db.configs.create_index("guild_id", unique=True)
                await self.db.reminders.create_index("due_time")
                await self.db.social_alerts.create_index([("guild_id", 1), ("platform", 1)])
                await self.db.birthdays.create_index([("month", 1), ("day", 1)])
                await self.db.economy.create_index("user_id")
                logger.info("✅ Successfully connected to MongoDB and ensured indexes.")
                return
            except ConnectionFailure as e:
                logger.error(f"Attempt {attempt + 1}/{MONGO_MAX_RETRIES} failed to connect to MongoDB: {e}")
                if attempt + 1 == MONGO_MAX_RETRIES:
                    logger.critical("❌ Max retries reached. Bot cannot start.")
                    raise SystemExit(f"Database connection failed: {e}") from e
                await asyncio.sleep(MONGO_RETRY_DELAY * (2 ** attempt)) # Exponential backoff
            except Exception as e:
                logger.critical(f"Unexpected error during database setup: {e}")
                raise

    async def get_guild_config(self, guild_id: int) -> dict:
        """Retrieves guild configuration from the database asynchronously."""
        if self.db is None:
            logger.error("Database connection is not available.")
            config = deepcopy(self._default_config)
            config["guild_id"] = guild_id
            return config
        try:
            config = await self.db.configs.find_one({"guild_id": guild_id})
            if not config:
                logger.info(f"No config found for guild {guild_id}. Creating a new one.")
                new_config = deepcopy(self._default_config)
                new_config["guild_id"] = guild_id
                await self.db.configs.insert_one(new_config)
                return new_config
            return config
        except NetworkTimeout:
            logger.warning(f"Network timeout fetching config for guild {guild_id}. Returning default config.")
            config = deepcopy(self._default_config)
            config["guild_id"] = guild_id
            return config
        except OperationFailure as e:
            logger.error(f"Database operation failed for guild {guild_id}: {e}")
            raise
        except Exception as e:
            logger.critical(f"Unexpected error fetching guild config for guild {guild_id}: {e}")
            raise

    async def setup_hook(self):
        """Sets up the bot by connecting to the DB, registering views, and loading cogs."""
        await self.setup_database()

        try:
            from cogs.ticket_cog import TicketActionsView
            # CORRECTED: Removed TicketPanelView registration as it requires a panel_id at initialization
            self.add_view(TicketActionsView(self))
            logger.info("✅ All persistent views registered.")
        except ImportError as e:
            logger.critical(f"❌ Failed to import a view for registration. Error: {e}")
            raise
        except Exception as e:
            logger.critical(f"❌ Failed to register persistent views: {e}")
            raise

        logger.info("⚙️  Loading cogs...")
        cogs_to_load = [
            'menu_cog', 'ticket_cog', 'logging_cog', 'moderation_cog', 
            'essentials_cog', 'purge_cog', 'music_cog', 
            'giveaways_cog'
        ]
        
        for cog in cogs_to_load:
            try:
                await self.load_extension(f'cogs.{cog}')
                logger.info(f"  -> Loaded cog: {cog}.py")
            except Exception as e:
                logger.critical(f"  -> FAILED to load cog {cog}: {e}")
                raise

        logger.info("✅ Cogs loaded.")
        
        try:
            await self.tree.sync()
            logger.info("✅ Global command tree synced.")
        except Exception as e:
            logger.error(f"Failed to sync command tree: {e}")

    async def on_ready(self):
        logger.info(f'🤖 Logged in as {self.user} (ID: {self.user.id})')
        logger.info('-----------------------------------------')
        await self.change_presence(activity=discord.Game(name="Watching Servers | /menu"))

bot = OPBot()

if __name__ == "__main__":
    validate_env_vars()
    bot.run(TOKEN, log_handler=handler)
