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
SHOP_URL           = f"{_base}/shop"

ALLOWED_CHANNEL_ID = 1540453226174881853  # Nur dieser Kanal erlaubt
OWNER_ID           = 921422333808345118   # Server-Owner Discord ID
MAIN_SERVER_ID     = 1253800682415325214  # Haupt-Server ID

PRODUCTS_INFO = {
    "tiktok_font":   {"name": "TikTok Font Methode", "emoji": "🎵", "price": "15,00€"},
    "members_farm":  {"name": "Members Farm",         "emoji": "👥", "price": "7,99€"},
    "yt_farm":       {"name": "YT Farm",              "emoji": "📺", "price": "9,99€"},
}


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
# /shop – Zeigt das Kauf-Panel (nur Owner)
# ---------------------------------------------------------------------------
@bot.tree.command(name="shop", description="Zeigt das zpynq Shop-Panel im Channel.")
async def shop_command(interaction: discord.Interaction):
    # Nur Server-Owner darf das Panel posten
    if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ Kein Zugriff",
                description="Nur der Server-Owner kann das Shop-Panel posten.",
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🛍️ zpynq Shop",
        description=(
            "**Premium Methoden für deinen Erfolg**\n\n"
            "Klicke auf den Button um unsere Produkte zu kaufen.\n"
            "Sichere Zahlung über **Stripe** — Apple Pay, Google Pay & Karte."
        ),
        color=0xFE2C55,
    )
    embed.add_field(
        name="🎵 TikTok Font Methode",
        value="Lerne wie du mit TikTok-Fonts viral gehst.\n**15,00€**",
        inline=True,
    )
    embed.add_field(
        name="👥 Members Farm",
        value="Discord Server schnell mit Mitgliedern füllen.\n**7,99€**",
        inline=True,
    )
    embed.add_field(
        name="📺 YT Farm",
        value="YouTube-Kanal organisch wachsen lassen.\n**9,99€**",
        inline=True,
    )
    embed.add_field(
        name="🔒 Sichere Zahlung",
        value="Apple Pay · Google Pay · Kreditkarte · Klarna · SEPA",
        inline=False,
    )
    embed.set_footer(text="⚡ Sofort-Zugang nach Kauf · Lifetime Zugang")

    view = _ShopView(SHOP_URL)
    await interaction.response.send_message(embed=embed, view=view)


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
# Views / Buttons
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


class _ShopView(discord.ui.View):
    def __init__(self, shop_url: str):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="🛍️ Shop öffnen",
                url=shop_url,
                style=discord.ButtonStyle.link,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="🎵 TikTok Font — 15€",
                url=f"{shop_url}/checkout/tiktok_font",
                style=discord.ButtonStyle.link,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="👥 Members Farm — 7,99€",
                url=f"{shop_url}/checkout/members_farm",
                style=discord.ButtonStyle.link,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="📺 YT Farm — 9,99€",
                url=f"{shop_url}/checkout/yt_farm",
                style=discord.ButtonStyle.link,
            )
        )


# ---------------------------------------------------------------------------
# Ticket-System
# ---------------------------------------------------------------------------
class _TicketProductView(discord.ui.View):
    """Panel mit Produkt-Buttons zum Ticket öffnen."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎵 TikTok Font — 15€",   style=discord.ButtonStyle.primary,  custom_id="ticket_tiktok_font")
    async def btn_tiktok(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _open_ticket(interaction, "tiktok_font")

    @discord.ui.button(label="👥 Members Farm — 7,99€", style=discord.ButtonStyle.primary,  custom_id="ticket_members_farm")
    async def btn_members(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _open_ticket(interaction, "members_farm")

    @discord.ui.button(label="📺 YT Farm — 9,99€",      style=discord.ButtonStyle.primary,  custom_id="ticket_yt_farm")
    async def btn_yt(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _open_ticket(interaction, "yt_farm")


class _TicketCloseView(discord.ui.View):
    """Schließen-Button im Ticket-Kanal."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Ticket schließen", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Nur Owner oder der Kanal-Ersteller darf schließen
        if interaction.user.id != OWNER_ID and not interaction.channel.name.endswith(str(interaction.user.id)[-4:]):
            await interaction.response.send_message("❌ Nur der Owner kann Tickets schließen.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Ticket wird geschlossen...")
        await asyncio.sleep(3)
        await interaction.channel.delete(reason="Ticket geschlossen")


async def _open_ticket(interaction: discord.Interaction, product_id: str):
    """Erstellt einen privaten Ticket-Kanal für den User."""
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ Nur auf Servern verfügbar.", ephemeral=True)
        return

    product = PRODUCTS_INFO.get(product_id, {"name": product_id, "emoji": "🛍️", "price": "?"})

    # Prüfen ob User bereits ein offenes Ticket hat
    existing = discord.utils.get(guild.channels, name=f"ticket-{interaction.user.name.lower()[:15]}-{product_id[:6]}")
    if existing:
        await interaction.response.send_message(
            f"❌ Du hast bereits ein offenes Ticket: {existing.mention}", ephemeral=True
        )
        return

    # Ticket-Kategorie finden oder erstellen
    category = discord.utils.get(guild.categories, name="🎫 Tickets")
    if not category:
        category = await guild.create_category(
            "🎫 Tickets",
            overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)},
        )

    # Berechtigungen: nur User + Owner sehen den Kanal
    overwrites = {
        guild.default_role:                    discord.PermissionOverwrite(view_channel=False),
        interaction.user:                      discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me:                              discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    # Owner hinzufügen falls im Server
    owner_member = guild.get_member(OWNER_ID)
    if owner_member:
        overwrites[owner_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)

    channel_name = f"ticket-{interaction.user.name.lower()[:15]}-{product_id[:6]}"
    ticket_channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"Ticket von {interaction.user} | Produkt: {product['name']}",
    )

    # Willkommensnachricht im Ticket
    embed = discord.Embed(
        title=f"{product['emoji']} Ticket — {product['name']}",
        description=(
            f"Hey {interaction.user.mention}! 👋\n\n"
            f"Du möchtest **{product['name']}** für **{product['price']}** kaufen.\n\n"
            f"Der Owner <@{OWNER_ID}> wird sich gleich bei dir melden.\n"
            "Bitte warte kurz und beschreibe kurz was du möchtest."
        ),
        color=0xFE2C55,
    )
    embed.add_field(name="Produkt", value=f"{product['emoji']} {product['name']}", inline=True)
    embed.add_field(name="Preis",   value=product['price'],                         inline=True)
    embed.set_footer(text="Nur du und der Owner können diesen Kanal sehen.")

    await ticket_channel.send(
        content=f"{interaction.user.mention} <@{OWNER_ID}>",
        embed=embed,
        view=_TicketCloseView(),
    )

    await interaction.response.send_message(
        f"✅ Dein Ticket wurde erstellt: {ticket_channel.mention}", ephemeral=True
    )
    logger.info("Ticket erstellt: %s für %s", channel_name, interaction.user)


# ---------------------------------------------------------------------------
# /ticket – Zeigt das Ticket-Panel (nur Owner)
# ---------------------------------------------------------------------------
@bot.tree.command(name="ticket", description="Zeigt das Kauf-Ticket-Panel im Channel.")
async def ticket_command(interaction: discord.Interaction):
    if not interaction.guild or interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="❌ Kein Zugriff",
                description="Nur der Owner kann das Ticket-Panel posten.",
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🛍️ zpynq Shop — Produkte kaufen",
        description=(
            "**Wähle ein Produkt aus um ein Ticket zu öffnen.**\n\n"
            "Ein privater Kanal wird für dich erstellt wo du direkt mit uns kommunizieren kannst.\n\n"
            "💳 Zahlung per PayPal, Überweisung oder nach Absprache."
        ),
        color=0xFE2C55,
    )
    embed.add_field(name="🎵 TikTok Font Methode", value="Viral gehen mit TikTok-Fonts\n**15,00€**", inline=True)
    embed.add_field(name="👥 Members Farm",         value="Discord schnell wachsen lassen\n**7,99€**",  inline=True)
    embed.add_field(name="📺 YT Farm",              value="YouTube organisch wachsen\n**9,99€**",       inline=True)
    embed.set_footer(text="🔒 Privates Ticket • Nur du und der Owner sehen den Kanal")

    await interaction.response.send_message(embed=embed, view=_TicketProductView())


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    # Persistent Views registrieren damit Buttons nach Neustart funktionieren
    bot.add_view(_TicketProductView())
    bot.add_view(_TicketCloseView())
    try:
        # Global sync — Commands in allen Servern sichtbar
        synced = await bot.tree.sync()
        names = [c.name for c in synced]
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
