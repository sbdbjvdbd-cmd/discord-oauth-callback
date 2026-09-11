"""
Discord Bot – TikTok Toolkit
Lädt alle Commands und startet den Bot.
"""
import logging
import discord
from discord import app_commands
from discord.ext import commands

from .config.settings import DISCORD_TOKEN, GUILD_ID
from .database.db import init_db
from .commands import tiktok as tiktok_commands

logger = logging.getLogger(__name__)


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    bot     = commands.Bot(command_prefix="!", intents=intents)

    guild_obj = discord.Object(id=GUILD_ID) if GUILD_ID else None

    @bot.event
    async def on_ready():
        # Commands registrieren
        tiktok_commands.register(bot.tree, guild=guild_obj)
        try:
            if guild_obj:
                bot.tree.copy_global_to(guild=guild_obj)
                synced = await bot.tree.sync(guild=guild_obj)
            else:
                synced = await bot.tree.sync()
            names = [c.name for c in synced]
            logger.info("✅ Bot online: %s | Commands: %s", bot.user, names)
            print(f"✅ Bot online als {bot.user}")
            print(f"   Commands: {names}")
        except Exception as exc:
            logger.error("Sync fehlgeschlagen: %s", exc)

    @bot.event
    async def on_application_command_error(interaction: discord.Interaction, error: Exception):
        logger.error("Command error: %s", error)
        msg = "❌ Ein interner Fehler ist aufgetreten."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    return bot


def run():
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN fehlt!")
        raise SystemExit(1)
    init_db()
    bot = create_bot()
    bot.run(DISCORD_TOKEN, log_handler=None)
