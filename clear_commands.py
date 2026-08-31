"""
Löscht alle global registrierten Slash-Commands bei Discord.
Einmalig ausführen um alte Commands zu entfernen.
"""

import asyncio
import os
from dotenv import load_dotenv
import discord

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "")

async def clear():
    if not TOKEN:
        print("FEHLER: DISCORD_TOKEN fehlt in .env")
        return

    bot = discord.Client(intents=discord.Intents.default())

    async with bot:
        await bot.login(TOKEN)
        # Alle globalen Commands löschen
        await bot.http.bulk_upsert_global_commands(bot.application_id, [])
        print("Alle globalen Slash-Commands wurden gelöscht.")
        print("Starte jetzt main.py – der Bot registriert nur noch die neuen Commands.")

asyncio.run(clear())
