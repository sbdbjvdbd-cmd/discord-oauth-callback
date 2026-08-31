"""
Discord Bot – TikTok OAuth Login System
Slash Commands:
  /tiktok login   – Startet den TikTok OAuth-Flow
  /tiktok status  – Zeigt verbundenen TikTok-Account
  /tiktok trennen – Hebt die Verknüpfung auf
  /claim          – TikTok-Username ändern (sofern API es erlaubt)
"""

import os
import logging
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from .database import init_db, get_account_by_discord, delete_account

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
DISCORD_TOKEN      = os.getenv("DISCORD_TOKEN", "")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080/tiktok/callback")
_base              = OAUTH_REDIRECT_URI.replace("/tiktok/callback", "").rstrip("/")
OAUTH_LOGIN_URL    = f"{_base}/login"

ALLOWED_CHANNEL_ID = 1540453226174881853  # Nur dieser Kanal erlaubt


# ---------------------------------------------------------------------------
# Channel-Check (Owner darf überall)
# ---------------------------------------------------------------------------
async def _check_channel(interaction: discord.Interaction) -> bool:
    if interaction.guild and interaction.user.id == interaction.guild.owner_id:
        return True
    if interaction.channel_id != ALLOWED_CHANNEL_ID:
        embed = discord.Embed(
            title="❌ Falscher Kanal",
            description=f"Dieser Command kann nur in <#{ALLOWED_CHANNEL_ID}> genutzt werden.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    return True


# ---------------------------------------------------------------------------
# Bot Setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tiktok_group = app_commands.Group(name="tiktok", description="TikTok Account verwalten")
bot.tree.add_command(tiktok_group)


# ---------------------------------------------------------------------------
# /tiktok login
# ---------------------------------------------------------------------------
@tiktok_group.command(name="login", description="Verbinde deinen TikTok-Account mit Discord.")
async def tiktok_login(interaction: discord.Interaction):
    if not await _check_channel(interaction):
        return

    discord_id = str(interaction.user.id)

    existing = get_account_by_discord(discord_id)
    if existing and not existing.get("is_expired"):
        username = existing.get("tiktok_username") or "Unbekannt"
        embed = discord.Embed(
            title="Bereits verbunden",
            description=(
                f"Dein TikTok-Account **@{username}** ist bereits verknüpft.\n"
                "Nutze `/tiktok trennen` um die Verbindung zu lösen."
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    login_url = f"{OAUTH_LOGIN_URL}?discord_id={discord_id}"

    embed = discord.Embed(
        title="🎵 TikTok Account verbinden",
        description=(
            "Klicke den Button unten um deinen TikTok-Account sicher zu verbinden.\n\n"
            "Du wirst zur **offiziellen TikTok-Seite** weitergeleitet.\n"
            "Wir erhalten **niemals** dein Passwort oder deine Session-Daten."
        ),
        color=0xFE2C55,
    )
    embed.add_field(
        name="Ablauf",
        value=(
            "1. Klicke auf **TikTok verbinden**\n"
            "2. Melde dich auf der offiziellen TikTok-Seite an\n"
            "3. Du erhältst eine **Discord-DM** zur Bestätigung"
        ),
        inline=False,
    )
    embed.set_footer(text="Der Link ist nur für dich sichtbar • Läuft in 10 Minuten ab")

    view = _LoginView(login_url)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    logger.info("OAuth-Login-Link gesendet (Discord-ID: [REDACTED])")


# ---------------------------------------------------------------------------
# /tiktok status
# ---------------------------------------------------------------------------
@tiktok_group.command(name="status", description="Zeigt deinen verbundenen TikTok-Account.")
async def tiktok_status(interaction: discord.Interaction):
    if not await _check_channel(interaction):
        return

    discord_id = str(interaction.user.id)
    account    = get_account_by_discord(discord_id)

    if not account:
        embed = discord.Embed(
            title="Kein Account verbunden",
            description="Du hast noch keinen TikTok-Account verknüpft.\nNutze `/tiktok login`.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    is_expired  = account.get("is_expired", True)
    status_text = "⚠️ Abgelaufen – bitte erneut verbinden" if is_expired else "✅ Aktiv"
    color       = discord.Color.red() if is_expired else discord.Color.green()
    username    = account.get("tiktok_username") or "Unbekannt"
    linked_at   = account.get("linked_at", "Unbekannt")

    if linked_at != "Unbekannt":
        linked_at = linked_at[:19].replace("T", " ") + " UTC"

    embed = discord.Embed(title="Verbundener TikTok-Account", color=color)
    embed.add_field(name="Username",       value=f"@{username}", inline=True)
    embed.add_field(name="Token-Status",   value=status_text,    inline=True)
    embed.add_field(name="Verbunden seit", value=linked_at,      inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /tiktok trennen
# ---------------------------------------------------------------------------
@tiktok_group.command(name="trennen", description="Hebt die Verknüpfung mit deinem TikTok-Account auf.")
async def tiktok_trennen(interaction: discord.Interaction):
    if not await _check_channel(interaction):
        return

    discord_id = str(interaction.user.id)
    deleted    = delete_account(discord_id)

    if not deleted:
        embed = discord.Embed(
            title="Kein Account verbunden",
            description="Du hast keinen TikTok-Account verknüpft.",
            color=discord.Color.orange(),
        )
    else:
        logger.info("Account-Verknüpfung aufgehoben (Discord-ID: [REDACTED])")
        embed = discord.Embed(
            title="✅ Verbindung getrennt",
            description=(
                "Dein TikTok-Account wurde erfolgreich getrennt.\n"
                "Alle gespeicherten Token wurden gelöscht."
            ),
            color=discord.Color.green(),
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /claim – TikTok-Username ändern
# ---------------------------------------------------------------------------
@bot.tree.command(name="claim", description="Ändere deinen TikTok-Benutzernamen.")
@app_commands.describe(username="Der gewünschte neue TikTok-Benutzername")
async def claim(interaction: discord.Interaction, username: str):
    if not await _check_channel(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    discord_id = str(interaction.user.id)
    account    = get_account_by_discord(discord_id)

    # Kein Account verbunden
    if not account:
        embed = discord.Embed(
            title="❌ Kein TikTok-Account verbunden",
            description="Bitte verbinde zuerst deinen TikTok-Account mit `/tiktok login`.",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # Token abgelaufen
    if account.get("is_expired"):
        embed = discord.Embed(
            title="⚠️ Token abgelaufen",
            description="Deine TikTok-Verbindung ist abgelaufen.\nBitte verbinde dich erneut mit `/tiktok login`.",
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # Username validieren
    username = username.strip().lstrip("@")
    if not username or len(username) < 2 or len(username) > 24:
        embed = discord.Embed(
            title="❌ Ungültiger Benutzername",
            description="Der Benutzername muss zwischen 2 und 24 Zeichen lang sein.",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # TikTok erlaubt Username-Änderung nicht über die öffentliche API
    embed = discord.Embed(
        title="❌ Nicht verfügbar",
        description=(
            "TikTok erlaubt das Ändern des Benutzernamens **nicht** über die öffentliche API.\n\n"
            "Bitte ändere deinen Benutzernamen direkt in der **TikTok-App**:\n"
            "Profil → Profil bearbeiten → Benutzername"
        ),
        color=discord.Color.red(),
    )
    embed.add_field(
        name="Dein aktueller Account",
        value=f"@{account.get('tiktok_username') or 'Unbekannt'}",
        inline=False,
    )
    embed.set_footer(text="Diese Einschränkung wird von TikTok vorgegeben, nicht vom Bot.")
    await interaction.followup.send(embed=embed, ephemeral=True)
    logger.info("/claim ausgeführt – TikTok API unterstützt keine Username-Änderung")


# ---------------------------------------------------------------------------
# Login-Button
# ---------------------------------------------------------------------------
class _LoginView(discord.ui.View):
    def __init__(self, login_url: str):
        super().__init__(timeout=600)
        self.add_item(
            discord.ui.Button(
                label="TikTok verbinden",
                url=login_url,
                style=discord.ButtonStyle.link,
                emoji="🔗",
            )
        )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        names  = [c.name for c in synced]
        logger.info("Bot online: %s | Commands: %s", bot.user, names)
        print(f"✅ Bot online als {bot.user}")
        print(f"   Commands: {names}")
    except Exception as exc:
        logger.error("Fehler beim Synchronisieren: %s", exc)


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------
def run():
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN fehlt in der .env Datei!")
        raise SystemExit(1)
    init_db()
    bot.run(DISCORD_TOKEN, log_handler=None)
