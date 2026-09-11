"""
TikTok Commands – alle /tiktok Subcommands
Alle Commands nur im erlaubten Kanal.
"""
import secrets
import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands

from ..database.db import (
    get_account, delete_account, save_state, check_rate_limit,
    get_cached, set_cache, update_tokens,
)
from ..services.oauth.tiktok_oauth import (
    build_auth_url, generate_pkce, revoke_token, get_user_info,
    refresh_access_token, parse_token_expiry,
)
from ..services.username.checker import (
    check_username, status_emoji, status_label,
    STATUS_AVAILABLE, STATUS_TAKEN, STATUS_UNKNOWN, STATUS_INVALID,
    is_valid_username, is_valid_4l,
)
from ..services.username.generator import generate_usernames, find_available_4l
from ..utils.crypto import encrypt, decrypt
from ..config.settings import (
    BASE_URL, RATE_USERNAME_CHECK, RATE_4L_FINDER,
    RATE_4L_FINDER_DAY, FINDER_MAX_CHECKS, ALLOWED_CHANNEL_ID,
)

logger = logging.getLogger(__name__)

_pkce_store: dict[str, str] = {}


# ── Channel Guard ─────────────────────────────────────────────────────────────

async def _check_channel(interaction: discord.Interaction) -> bool:
    """Prüft ob der Command im erlaubten Kanal ausgeführt wird."""
    if ALLOWED_CHANNEL_ID and interaction.channel_id != ALLOWED_CHANNEL_ID:
        embed = discord.Embed(
            title="❌ Falscher Kanal",
            description=f"Dieser Command kann nur in <#{ALLOWED_CHANNEL_ID}> genutzt werden.",
            color=0xE74C3C,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    return True


# ── Register ──────────────────────────────────────────────────────────────────

def register(tree: app_commands.CommandTree, guild: discord.Object | None) -> None:
    group = app_commands.Group(name="tiktok", description="🎵 TikTok Account & Username Tools")

    # ── /tiktok menu ──────────────────────────────────────────────────────────
    @group.command(name="menu", description="🎵 TikTok Toolkit Hauptmenü")
    async def cmd_menu(interaction: discord.Interaction):
        if not await _check_channel(interaction):
            return
        embed = discord.Embed(
            title="🎵 TikTok Toolkit",
            description="**Account & Username Tools**\n─────────────────────",
            color=0xFE2C55,
        )
        embed.add_field(name="🔎 Username", value=(
            "`/tiktok username` — Username prüfen\n"
            "`/tiktok 4l` — 4L Username prüfen\n"
            "`/tiktok generate` — Usernames generieren\n"
            "`/tiktok find` — 4L Finder (automatisch)"
        ), inline=True)
        embed.add_field(name="👤 Account", value=(
            "`/tiktok login` — TikTok verbinden\n"
            "`/tiktok account` — Mein Account\n"
            "`/tiktok connection` — Verbindung verwalten"
        ), inline=True)
        embed.add_field(name="📝 Support", value="`/tiktok appeal` — Appeal generieren", inline=False)
        embed.set_footer(text="zpynq TikTok Toolkit • Alle Tools an einem Ort")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /tiktok login ─────────────────────────────────────────────────────────
    @group.command(name="login", description="🔐 TikTok Account mit Discord verbinden")
    async def cmd_login(interaction: discord.Interaction):
        if not await _check_channel(interaction):
            return
        discord_id = str(interaction.user.id)
        existing   = get_account(discord_id)

        if existing and not existing.get("is_expired"):
            uname = existing.get("tiktok_username") or "Unbekannt"
            embed = discord.Embed(
                title="✅ Bereits verbunden",
                description=f"Dein TikTok **@{uname}** ist bereits verknüpft.\nNutze `/tiktok connection` zum Verwalten.",
                color=0x2ECC71,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        state              = secrets.token_urlsafe(32)
        verifier, chall    = generate_pkce()
        _pkce_store[state] = verifier
        save_state(state, discord_id)
        auth_url = build_auth_url(state, chall)

        embed = discord.Embed(
            title="🔐 TikTok Account verbinden",
            description=(
                "Klicke den Button um deinen TikTok-Account **sicher** zu verbinden.\n\n"
                "Du wirst zur **offiziellen TikTok-Seite** weitergeleitet.\n"
                "Wir erhalten **niemals** dein Passwort."
            ),
            color=0xFE2C55,
        )
        embed.add_field(name="Ablauf", value=(
            "1. Button klicken\n"
            "2. Bei TikTok einloggen\n"
            "3. Berechtigungen bestätigen\n"
            "4. ✅ Fertig"
        ), inline=False)
        embed.set_footer(text="Link läuft in 10 Minuten ab • Nur für dich sichtbar")
        await interaction.response.send_message(embed=embed, view=_LoginView(auth_url), ephemeral=True)

    # ── /tiktok account ───────────────────────────────────────────────────────
    @group.command(name="account", description="👤 Verbundenen TikTok-Account anzeigen")
    async def cmd_account(interaction: discord.Interaction):
        if not await _check_channel(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        account    = get_account(discord_id)

        if not account:
            embed = discord.Embed(
                title="❌ Kein Account verbunden",
                description="Verbinde deinen TikTok-Account mit `/tiktok login`.",
                color=0xE74C3C,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if account.get("is_expired") and account.get("refresh_token_enc"):
            await _try_refresh(discord_id, account)
            account = get_account(discord_id)

        access_token = decrypt(account.get("access_token_enc", ""))
        user_info    = None
        if access_token and not account.get("is_expired"):
            user_info = await get_user_info(access_token)

        uname       = account.get("tiktok_username") or "Unbekannt"
        display     = account.get("display_name") or uname
        is_verified = bool(account.get("is_verified"))
        is_expired  = account.get("is_expired", False)
        linked_at   = account.get("linked_at", "")[:19].replace("T", " ") + " UTC"
        status_txt  = "⚠️ Abgelaufen" if is_expired else "🟢 Aktiv"
        color       = 0xE74C3C if is_expired else 0x2ECC71

        embed = discord.Embed(title="👤 Verbundener TikTok-Account", color=color)
        if account.get("avatar_url"):
            embed.set_thumbnail(url=account["avatar_url"])
        embed.add_field(name="Username",      value=f"`@{uname}`",             inline=True)
        embed.add_field(name="Display Name",  value=display,                   inline=True)
        embed.add_field(name="Verifiziert",   value="✅" if is_verified else "❌", inline=True)
        embed.add_field(name="Verbindung",    value=status_txt,                inline=True)
        embed.add_field(name="Seit",          value=linked_at,                 inline=True)

        if user_info:
            followers = user_info.get("follower_count", "?")
            following = user_info.get("following_count", "?")
            likes     = user_info.get("likes_count", "?")
            embed.add_field(
                name="📊 Stats",
                value=f"👥 {followers} Follower • 🫂 {following} Following • ❤️ {likes} Likes",
                inline=False,
            )

        await interaction.followup.send(embed=embed, view=_ConnectionView(discord_id), ephemeral=True)

    # ── /tiktok connection ────────────────────────────────────────────────────
    @group.command(name="connection", description="🔗 TikTok-Verbindung verwalten")
    async def cmd_connection(interaction: discord.Interaction):
        if not await _check_channel(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        account    = get_account(discord_id)

        if not account:
            await interaction.followup.send(embed=discord.Embed(
                title="❌ Kein Account verbunden",
                description="Verbinde deinen TikTok-Account mit `/tiktok login`.",
                color=0xE74C3C,
            ), ephemeral=True)
            return

        uname      = account.get("tiktok_username") or "Unbekannt"
        is_expired = account.get("is_expired", False)
        scopes     = account.get("scopes", "Unbekannt")
        linked_at  = account.get("linked_at", "")[:19].replace("T", " ") + " UTC"
        expires_at = (account.get("token_expires_at") or "")[:19].replace("T", " ") + " UTC"

        embed = discord.Embed(title="🔗 TikTok Verbindung", color=0xE74C3C if is_expired else 0x2ECC71)
        embed.add_field(name="Account",        value=f"`@{uname}`",                             inline=True)
        embed.add_field(name="Status",         value="⚠️ Abgelaufen" if is_expired else "🟢 Aktiv", inline=True)
        embed.add_field(name="Verbunden seit", value=linked_at,                                 inline=False)
        embed.add_field(name="Token läuft ab", value=expires_at,                                inline=True)
        embed.add_field(name="Berechtigungen", value=f"`{scopes}`",                             inline=False)

        await interaction.followup.send(embed=embed, view=_ConnectionView(discord_id), ephemeral=True)

    # ── /tiktok username ──────────────────────────────────────────────────────
    @group.command(name="username", description="🔎 TikTok Username prüfen")
    @app_commands.describe(username="TikTok-Username (ohne @)")
    async def cmd_username(interaction: discord.Interaction, username: str):
        if not await _check_channel(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        rl = check_rate_limit(f"username:{interaction.user.id}", per_minute=RATE_USERNAME_CHECK, per_day=500)
        if not rl["allowed"]:
            await interaction.followup.send(embed=discord.Embed(
                title="🔵 Rate Limited",
                description=f"Bitte warte kurz. Limit: {RATE_USERNAME_CHECK} Checks/Minute.",
                color=0x3498DB,
            ), ephemeral=True)
            return

        username   = username.strip().lstrip("@")
        cached     = get_cached(username)
        from_cache = cached is not None
        if cached:
            result = {"status": cached, "username": username, "response_ms": 0}
        else:
            result = await check_username(username)
            if result["status"] not in (STATUS_UNKNOWN,):
                set_cache(username, result["status"])

        status = result["status"]
        embed  = discord.Embed(title="🔎 TikTok Username Check", color=_status_color(status))
        embed.add_field(name="Username", value=f"`@{username}`",                         inline=True)
        embed.add_field(name="Status",   value=f"{status_emoji(status)} **{status_label(status)}**", inline=True)
        embed.set_footer(text="Aus Cache" if from_cache else f"Response: {result['response_ms']}ms")

        await interaction.followup.send(embed=embed, view=_CheckAgainView(username, "username"), ephemeral=True)

    # ── /tiktok 4l ────────────────────────────────────────────────────────────
    @group.command(name="4l", description="🔤 4L TikTok-Username prüfen")
    @app_commands.describe(username="Genau 4 Zeichen langer TikTok-Username")
    async def cmd_4l(interaction: discord.Interaction, username: str):
        if not await _check_channel(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        username = username.strip().lstrip("@")

        if not is_valid_4l(username):
            await interaction.followup.send(embed=discord.Embed(
                title="⚠️ Ungültiger 4L Username",
                description="Genau **4 Zeichen**, nur `a-z`, `0-9`, `_` (nicht am Anfang/Ende, kein `__`).",
                color=0xF39C12,
            ), ephemeral=True)
            return

        rl = check_rate_limit(f"4l:{interaction.user.id}", per_minute=RATE_USERNAME_CHECK, per_day=200)
        if not rl["allowed"]:
            await interaction.followup.send(embed=discord.Embed(
                title="🔵 Rate Limited", description="Bitte warte kurz.", color=0x3498DB,
            ), ephemeral=True)
            return

        cached = get_cached(username)
        if cached:
            result = {"status": cached, "username": username, "response_ms": 0}
        else:
            result = await check_username(username)
            if result["status"] not in (STATUS_UNKNOWN,):
                set_cache(username, result["status"])

        status = result["status"]
        embed  = discord.Embed(title="🔤 TikTok 4L Checker", color=_status_color(status))
        embed.add_field(name="Username", value=f"`@{username}`",                              inline=True)
        embed.add_field(name="Status",   value=f"{status_emoji(status)} **{status_label(status)}**", inline=True)
        embed.set_footer(text=f"Response: {result['response_ms']}ms")

        await interaction.followup.send(embed=embed, view=_CheckAgainView(username, "4l"), ephemeral=True)

    # ── /tiktok generate ──────────────────────────────────────────────────────
    @group.command(name="generate", description="✨ TikTok Usernames generieren")
    @app_commands.describe(
        length="Länge (2-16, Standard: 4)",
        letters="Buchstaben a-z",
        numbers="Zahlen 0-9",
        underscore="Unterstriche _",
        prefix="Prefix (optional)",
        suffix="Suffix (optional)",
    )
    async def cmd_generate(
        interaction: discord.Interaction,
        length: int = 4,
        letters: bool = True,
        numbers: bool = True,
        underscore: bool = False,
        prefix: str = "",
        suffix: str = "",
    ):
        if not await _check_channel(interaction):
            return

        length = max(2, min(16, length))
        names  = generate_usernames(
            length=length, use_letters=letters, use_numbers=numbers,
            use_underscore=underscore, prefix=prefix.strip(),
            suffix=suffix.strip(), count=10,
        )

        if not names:
            await interaction.response.send_message(embed=discord.Embed(
                title="❌ Keine Usernames generiert",
                description="Bitte andere Parameter wählen.",
                color=0xE74C3C,
            ), ephemeral=True)
            return

        embed = discord.Embed(
            title="✨ Generierte Usernames",
            description="\n".join(f"`{n}`" for n in names),
            color=0x9B59B6,
        )
        chars = [x for x, y in [("a-z", letters), ("0-9", numbers), ("_", underscore)] if y]
        embed.set_footer(text=f"Länge: {length} • Zeichen: {', '.join(chars)}")

        await interaction.response.send_message(embed=embed, view=_GenerateResultView(names), ephemeral=True)

    # ── /tiktok find ──────────────────────────────────────────────────────────
    @group.command(name="find", description="🔍 Freie 4L TikTok-Namen automatisch suchen")
    async def cmd_find(interaction: discord.Interaction):
        if not await _check_channel(interaction):
            return

        rl = check_rate_limit(
            f"finder:{interaction.user.id}",
            per_minute=RATE_4L_FINDER,
            per_day=RATE_4L_FINDER_DAY,
        )
        if not rl["allowed"]:
            await interaction.response.send_message(embed=discord.Embed(
                title="🔵 Rate Limited",
                description=f"Tageslimit erreicht. Verbleibend: **{rl['day_remaining']}** Suchen.",
                color=0x3498DB,
            ), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Live-Status während der Suche
        progress_embed = discord.Embed(
            title="🔍 Suche läuft...",
            description="Suche nach freien 4L TikTok-Namen...\nDas dauert ca. 20-30 Sekunden.",
            color=0xFE2C55,
        )
        progress_embed.add_field(name="⏳ Status", value="Wird geprüft...", inline=False)
        await interaction.followup.send(embed=progress_embed, ephemeral=True)

        available = []
        checked   = 0
        max_checks = min(FINDER_MAX_CHECKS, 25)

        async for result in find_available_4l(
            use_letters=True,
            use_numbers=True,
            use_underscore=False,
            max_checks=max_checks,
            delay=0.9,
        ):
            checked += 1
            if result["status"] == STATUS_AVAILABLE:
                available.append(result["username"])

            # Alle 5 Checks live updaten
            if checked % 5 == 0 or checked == max_checks:
                upd = discord.Embed(
                    title="🔍 Suche läuft...",
                    description=f"Geprüft: **{checked}/{max_checks}** • Gefunden: **{len(available)}**",
                    color=0xFE2C55,
                )
                if available:
                    upd.add_field(
                        name="🟢 Bisher gefunden",
                        value="\n".join(f"`@{n}`" for n in available),
                        inline=False,
                    )
                await interaction.edit_original_response(embed=upd)

        # Ergebnis
        if not available:
            result_embed = discord.Embed(
                title="🔍 4L Finder — Kein Ergebnis",
                description=f"In {checked} Checks keine freien Namen gefunden.\nVersuche es nochmal!",
                color=0xE74C3C,
            )
        else:
            lines = "\n".join(f"🟢 `@{n}`" for n in available)
            result_embed = discord.Embed(
                title=f"🔍 4L Finder — {len(available)} freie Namen gefunden!",
                description=lines,
                color=0x2ECC71,
            )
            result_embed.add_field(
                name="ℹ️ Hinweis",
                value="Diese Namen waren zum Zeitpunkt der Prüfung frei.\nSchnell handeln — sie können jederzeit vergeben werden!",
                inline=False,
            )

        result_embed.set_footer(text=f"Geprüft: {checked} Namen • Tageslimit: {rl['day_remaining']} Suchen übrig")
        await interaction.edit_original_response(embed=result_embed)

    # ── /tiktok appeal ────────────────────────────────────────────────────────
    @group.command(name="appeal", description="📝 TikTok Appeal-Text generieren")
    async def cmd_appeal(interaction: discord.Interaction):
        if not await _check_channel(interaction):
            return
        await interaction.response.send_modal(_AppealModal())

    tree.add_command(group, guild=guild)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _status_color(status: str) -> int:
    return {
        "available":   0x2ECC71,
        "taken":       0xE74C3C,
        "unknown":     0xF39C12,
        "invalid":     0xF39C12,
        "ratelimited": 0x3498DB,
        "error":       0x95A5A6,
    }.get(status, 0x95A5A6)


async def _try_refresh(discord_id: str, account: dict) -> bool:
    refresh_enc = account.get("refresh_token_enc", "")
    if not refresh_enc:
        return False
    refresh_token = decrypt(refresh_enc)
    if not refresh_token:
        return False
    data = await refresh_access_token(refresh_token)
    if not data:
        return False
    update_tokens(
        discord_id,
        encrypt(data.get("access_token", "")),
        encrypt(data.get("refresh_token", "")),
        parse_token_expiry(data.get("expires_in", 86400)),
    )
    return True


# ── Views ─────────────────────────────────────────────────────────────────────

class _LoginView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=600)
        self.add_item(discord.ui.Button(label="🔐 Mit TikTok verbinden", url=url, style=discord.ButtonStyle.link))


class _ConnectionView(discord.ui.View):
    def __init__(self, discord_id: str):
        super().__init__(timeout=300)
        self.discord_id = discord_id

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.discord_id:
            await interaction.response.send_message("❌ Nicht dein Account.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        account = get_account(self.discord_id)
        if account and account.get("is_expired"):
            ok  = await _try_refresh(self.discord_id, account)
            msg = "✅ Token erneuert." if ok else "❌ Fehlgeschlagen. Bitte erneut `/tiktok login`."
        else:
            msg = "✅ Verbindung ist aktuell."
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="🔓 Disconnect", style=discord.ButtonStyle.danger)
    async def disconnect_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.discord_id:
            await interaction.response.send_message("❌ Nicht dein Account.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        account = get_account(self.discord_id)
        if account:
            token = decrypt(account.get("access_token_enc", ""))
            if token:
                await revoke_token(token)
            delete_account(self.discord_id)
        await interaction.followup.send(embed=discord.Embed(
            title="✅ Verbindung getrennt",
            description="Dein TikTok-Account wurde getrennt und alle Tokens gelöscht.",
            color=0x2ECC71,
        ), ephemeral=True)


class _CheckAgainView(discord.ui.View):
    def __init__(self, username: str, mode: str):
        super().__init__(timeout=120)
        self.username = username
        self.mode     = mode

    @discord.ui.button(label="🔄 Erneut prüfen", style=discord.ButtonStyle.secondary)
    async def recheck(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        result = await check_username(self.username)
        status = result["status"]
        title  = "🔤 TikTok 4L Checker" if self.mode == "4l" else "🔎 TikTok Username Check"
        embed  = discord.Embed(title=title, color=_status_color(status))
        embed.add_field(name="Username", value=f"`@{self.username}`",                             inline=True)
        embed.add_field(name="Status",   value=f"{status_emoji(status)} **{status_label(status)}**", inline=True)
        embed.set_footer(text=f"Response: {result['response_ms']}ms")
        await interaction.followup.send(embed=embed, ephemeral=True)


class _GenerateResultView(discord.ui.View):
    def __init__(self, names: list[str]):
        super().__init__(timeout=300)
        options = [discord.SelectOption(label=f"@{n}", value=n) for n in names[:25]]
        select  = discord.ui.Select(placeholder="Username direkt prüfen...", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        username = interaction.data["values"][0]
        await interaction.response.defer(ephemeral=True)
        result = await check_username(username)
        status = result["status"]
        embed  = discord.Embed(title="🔎 Username Check", color=_status_color(status))
        embed.add_field(name="Username", value=f"`@{username}`",                              inline=True)
        embed.add_field(name="Status",   value=f"{status_emoji(status)} **{status_label(status)}**", inline=True)
        embed.set_footer(text=f"Response: {result['response_ms']}ms")
        await interaction.followup.send(embed=embed, ephemeral=True)


# ── Appeal Modal ──────────────────────────────────────────────────────────────

class _AppealModal(discord.ui.Modal, title="📝 TikTok Appeal Generator"):
    username    = discord.ui.TextInput(label="TikTok Username", placeholder="@deinusername", max_length=30)
    problem     = discord.ui.TextInput(label="Problem", placeholder="z.B. Account gesperrt, shadowban", max_length=100)
    language    = discord.ui.TextInput(label="Sprache", placeholder="Deutsch / English", default="Deutsch", max_length=20)
    description = discord.ui.TextInput(
        label="Kurze Beschreibung",
        placeholder="Was ist passiert? Wann?",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uname   = str(self.username).lstrip("@")
        problem = str(self.problem)
        lang    = str(self.language).strip().lower()
        desc    = str(self.description)
        is_de   = "deutsch" in lang or lang == "de"

        if is_de:
            text = (
                f"Sehr geehrtes TikTok-Support-Team,\n\n"
                f"ich wende mich bezüglich meines Accounts @{uname} an Sie.\n\n"
                f"Problem: {problem}\n\n{desc}\n\n"
                f"Ich habe stets versucht, die Community-Richtlinien einzuhalten. "
                f"Ich bitte Sie, meinen Account zu überprüfen und die Einschränkungen aufzuheben.\n\n"
                f"Mit freundlichen Grüßen,\n@{uname}"
            )
        else:
            text = (
                f"Dear TikTok Support Team,\n\n"
                f"I am writing regarding my account @{uname}.\n\n"
                f"Issue: {problem}\n\n{desc}\n\n"
                f"I have always tried to comply with TikTok's Community Guidelines. "
                f"I kindly ask you to review my account and lift any restrictions.\n\n"
                f"Best regards,\n@{uname}"
            )

        embed = discord.Embed(
            title="📝 Appeal-Text",
            description=f"```\n{text[:3900]}\n```",
            color=0x9B59B6,
        )
        embed.set_footer(text="Kopiere diesen Text und sende ihn an TikTok Support")
        await interaction.followup.send(embed=embed, ephemeral=True)
