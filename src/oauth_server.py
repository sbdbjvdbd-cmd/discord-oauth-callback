"""
OAuth Callback Server (FastAPI)
Routen:
  GET  /health           – Healthcheck für Render
  GET  /tiktok/callback  – TikTok OAuth2 Callback
  GET  /privacy          – Datenschutzerklärung
  GET  /.well-known/tiktok.txt – TikTok Domain Verification
"""
import os
import logging
import secrets
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.middleware.sessions import SessionMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .database.db import init_db, pop_state, upsert_account
from .services.oauth.tiktok_oauth import exchange_code, get_user_info, parse_token_expiry
from .utils.crypto import encrypt
from .config.settings import SECRET_KEY, BASE_URL, DISCORD_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# PKCE Verifier store (shared mit bot commands via import)
from .commands.tiktok import _pkce_store

limiter = Limiter(key_func=get_remote_address)
app     = FastAPI(title="TikTok Toolkit OAuth Server", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=600)

TIKTOK_VERIFY_TOKEN = os.getenv("TIKTOK_VERIFY_TOKEN", "")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>zpynq TikTok Toolkit</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Inter',sans-serif;background:#0a0a0a;color:#fff;
         display:flex;justify-content:center;align-items:center;min-height:100vh}}
    .box{{background:#111;border:1px solid #222;border-radius:24px;
          padding:48px 40px;text-align:center;max-width:480px;width:90%;
          box-shadow:0 20px 60px rgba(0,0,0,.5)}}
    .icon{{font-size:4rem;margin-bottom:16px}}
    h1{{font-size:1.6rem;font-weight:800;margin-bottom:12px}}
    p{{color:#888;font-size:.95rem;line-height:1.7;margin-top:8px}}
    .brand{{font-size:.8rem;color:#444;margin-top:32px}}
    .brand span{{color:#FE2C55;font-weight:700}}
  </style>
</head>
<body>
  <div class="box">
    {body}
    <div class="brand">powered by <span>zpynq</span> TikTok Toolkit</div>
  </div>
</body>
</html>""")


async def _send_discord_dm(discord_id: str, username: str, display_name: str) -> None:
    """Sendet eine Discord-DM an den User nach erfolgreichem Login."""
    if not DISCORD_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # DM-Channel öffnen
            ch = await client.post(
                f"https://discord.com/api/v10/users/@me/channels",
                json={"recipient_id": discord_id},
                headers={
                    "Authorization": f"Bot {DISCORD_TOKEN}",
                    "Content-Type":  "application/json",
                },
            )
            if ch.status_code not in (200, 201):
                return
            channel_id = ch.json().get("id")
            if not channel_id:
                return

            # DM senden
            embed = {
                "title":       "✅ TikTok Account verbunden",
                "description": f"Dein TikTok-Account **@{username}** wurde erfolgreich mit deinem Discord-Account verknüpft.",
                "color":       0x2ECC71,
                "fields": [
                    {"name": "Username",      "value": f"`@{username}`",  "inline": True},
                    {"name": "Display Name",  "value": display_name,       "inline": True},
                ],
                "footer": {"text": "zpynq TikTok Toolkit • /tiktok connection zum Verwalten"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                json={"embeds": [embed]},
                headers={
                    "Authorization": f"Bot {DISCORD_TOKEN}",
                    "Content-Type":  "application/json",
                },
            )
    except Exception as exc:
        logger.error("Discord DM fehlgeschlagen: %s", exc)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "zpynq-tiktok-toolkit"}


@app.get("/.well-known/tiktok.txt")
async def tiktok_verify():
    return PlainTextResponse(TIKTOK_VERIFY_TOKEN or "tiktok-verification")


@app.get("/privacy")
async def privacy():
    return _page("Datenschutz", """
        <div class="icon">🔒</div>
        <h1>Datenschutzerklärung</h1>
        <p>Wir speichern ausschließlich die zur Funktion notwendigen Daten:</p>
        <p>• TikTok Open ID &amp; Username (verschlüsselt)</p>
        <p>• OAuth Access &amp; Refresh Tokens (verschlüsselt)</p>
        <p>• Discord User ID (zur Zuordnung)</p>
        <p style="margin-top:16px">Passwörter werden <strong>niemals</strong> gespeichert.</p>
        <p>Tokens werden sicher serverseitig gespeichert und können jederzeit über
        <code>/tiktok connection</code> → Disconnect widerrufen werden.</p>
    """)


@app.get("/tiktok/callback")
@limiter.limit("20/minute")
async def tiktok_callback(
    request:  Request,
    code:     str = Query(None),
    state:    str = Query(None),
    error:    str = Query(None),
    error_description: str = Query(None),
):
    # Fehler von TikTok
    if error:
        logger.warning("TikTok OAuth error: %s – %s", error, error_description)
        return _page("Abgebrochen", f"""
            <div class="icon">❌</div>
            <h1>Verbindung abgebrochen</h1>
            <p>{error_description or 'Du hast den Vorgang abgebrochen.'}</p>
            <p style="margin-top:16px">Du kannst dieses Fenster schließen.</p>
        """)

    if not code or not state:
        return _page("Fehler", """
            <div class="icon">⚠️</div>
            <h1>Ungültige Anfrage</h1>
            <p>Fehlende Parameter. Bitte erneut versuchen.</p>
        """)

    # State validieren → Discord ID holen
    discord_id = pop_state(state)
    if not discord_id:
        return _page("Fehler", """
            <div class="icon">⚠️</div>
            <h1>Ungültiger oder abgelaufener Link</h1>
            <p>Bitte starte den Login erneut mit <strong>/tiktok login</strong>.</p>
        """)

    # PKCE Verifier holen
    code_verifier = _pkce_store.pop(state, None)
    if not code_verifier:
        return _page("Fehler", """
            <div class="icon">⚠️</div>
            <h1>PKCE Verifier nicht gefunden</h1>
            <p>Bitte starte den Login erneut mit <strong>/tiktok login</strong>.</p>
        """)

    # Code gegen Token tauschen
    token_data = await exchange_code(code, code_verifier)
    if not token_data:
        return _page("Fehler", """
            <div class="icon">❌</div>
            <h1>Token-Austausch fehlgeschlagen</h1>
            <p>TikTok hat keinen gültigen Token zurückgegeben.</p>
            <p>Bitte versuche es erneut.</p>
        """)

    access_token  = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in    = token_data.get("expires_in", 86400)
    scopes        = token_data.get("scope", "")
    open_id       = token_data.get("open_id", "")

    if not access_token:
        return _page("Fehler", """
            <div class="icon">❌</div>
            <h1>Kein Access Token erhalten</h1>
            <p>Bitte versuche es erneut.</p>
        """)

    # User-Info abrufen
    user_info = await get_user_info(access_token)
    if not user_info:
        return _page("Fehler", """
            <div class="icon">❌</div>
            <h1>User-Info fehlgeschlagen</h1>
            <p>TikTok-Profildaten konnten nicht abgerufen werden.</p>
        """)

    username     = user_info.get("username", "")     or user_info.get("display_name", "")
    display_name = user_info.get("display_name", "") or username
    avatar_url   = user_info.get("avatar_url", "")
    is_verified  = bool(user_info.get("is_verified", False))
    tiktok_open_id = user_info.get("open_id", "") or open_id

    # Tokens verschlüsselt speichern
    upsert_account(
        discord_id        = discord_id,
        tiktok_open_id    = tiktok_open_id,
        tiktok_username   = username,
        display_name      = display_name,
        avatar_url        = avatar_url,
        is_verified       = is_verified,
        access_token_enc  = encrypt(access_token),
        refresh_token_enc = encrypt(refresh_token),
        token_expires_at  = parse_token_expiry(expires_in),
        scopes            = scopes,
    )
    logger.info("TikTok Account verbunden: @%s (Discord: %s)", username, discord_id)

    # Discord DM senden
    await _send_discord_dm(discord_id, username, display_name)

    return _page("Verbunden! 🎉", f"""
        <div class="icon">✅</div>
        <h1>TikTok Account verbunden!</h1>
        <p>Dein Account <strong>@{username}</strong> wurde erfolgreich mit Discord verknüpft.</p>
        <p style="margin-top:12px;color:#2ECC71;font-weight:600">
            Du hast eine Bestätigung per Discord-DM erhalten.
        </p>
        <p style="margin-top:16px;color:#555;font-size:.85rem">
            Du kannst dieses Fenster schließen.
        </p>
    """)


@app.on_event("startup")
async def startup():
    init_db()
    logger.info("OAuth Server gestartet. BASE_URL: %s", BASE_URL)
