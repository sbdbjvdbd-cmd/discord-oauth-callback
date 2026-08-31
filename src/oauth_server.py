"""
OAuth-Callback-Server (FastAPI)
Routen:
  GET  /health          – Healthcheck
  GET  /login           – Login-Seite mit TikTok-Button
  GET  /tiktok/callback – TikTok OAuth Callback, Token-Austausch, Discord-DM
"""

import os
import secrets
import hashlib
import base64
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .database import init_db, save_state, pop_state, upsert_account

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
CLIENT_KEY       = os.getenv("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET    = os.getenv("TIKTOK_CLIENT_SECRET", "")
REDIRECT_URI     = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080/tiktok/callback")
FLASK_SECRET     = os.getenv("SECRET_KEY", "change-me-to-random-secret")
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN", "")

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL     = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"

# ---------------------------------------------------------------------------
# Rate-Limiter & App
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="TikTok OAuth Server", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SessionMiddleware, secret_key=FLASK_SECRET)


@app.on_event("startup")
def startup():
    init_db()
    if not CLIENT_KEY or not CLIENT_SECRET:
        logger.warning("TIKTOK_CLIENT_KEY oder TIKTOK_CLIENT_SECRET fehlen in .env!")


# ---------------------------------------------------------------------------
# HTML-Seiten
# ---------------------------------------------------------------------------
def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',Arial,sans-serif;background:#111;color:#fff;
         display:flex;justify-content:center;align-items:center;min-height:100vh}}
    .box{{background:#222;width:380px;max-width:92%;margin:auto;padding:36px 32px;
          border-radius:15px;text-align:center}}
    h1{{font-size:1.4rem;margin-bottom:12px}}
    p{{color:#aaa;font-size:.95rem;line-height:1.6;margin-bottom:8px}}
    .btn{{display:inline-block;background:#fe2c55;color:#fff;font-weight:600;
          font-size:1rem;padding:14px 28px;border-radius:10px;text-decoration:none;
          margin-top:20px;transition:background .2s}}
    .btn:hover{{background:#e0143a}}
    .icon{{font-size:3rem;margin-bottom:16px}}
    .timer{{margin-top:18px;color:#666;font-size:.8rem}}
    #cd{{color:#f0883e;font-weight:600}}
  </style>
</head>
<body><div class="box">{body}</div></body>
</html>""")


# ---------------------------------------------------------------------------
# Routen
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/privacy")
def privacy():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Datenschutzerklärung</title>
  <style>
    body{font-family:'Segoe UI',Arial,sans-serif;background:#111;color:#fff;max-width:700px;margin:40px auto;padding:0 20px}
    h1{color:#fe2c55}h2{color:#aaa;font-size:1.1rem;margin-top:24px}p{color:#ccc;line-height:1.7}
  </style>
</head>
<body>
  <h1>Datenschutzerklärung</h1>
  <p>Diese Anwendung verbindet TikTok-Accounts mit Discord über das offizielle TikTok OAuth 2.0 Verfahren.</p>
  <h2>Welche Daten werden gespeichert?</h2>
  <p>Wir speichern ausschließlich: TikTok Open ID, TikTok Anzeigename, Discord User ID sowie das OAuth Access Token und Refresh Token.</p>
  <h2>Was wird NICHT gespeichert?</h2>
  <p>Wir speichern weder Passwörter noch Session-Daten, private Nachrichten, Videos oder sonstige persönliche Daten.</p>
  <h2>Wofür werden die Daten verwendet?</h2>
  <p>Ausschließlich zur Verknüpfung deines TikTok-Accounts mit deinem Discord-Account innerhalb des Servers.</p>
  <h2>Löschung</h2>
  <p>Du kannst deine Daten jederzeit mit dem Befehl <strong>/tiktok trennen</strong> im Discord löschen.</p>
  <h2>Kontakt</h2>
  <p>Bei Fragen wende dich an den Server-Administrator.</p>
</body>
</html>""")


@app.get("/.well-known/tiktok.txt")
def tiktok_verify():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("tiktok-developers-site-verification=tkAk1s4951Jyy5571zurMO2FXA99ZDmj")


@app.get("/tiktokz1WwzF3N59ESsZr2E3pt5hF58SirsgTN.txt")
def tiktok_verify2():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("tiktok-developers-site-verification=tkAk1s4951Jyy5571zurMO2FXA99ZDmj")


@app.get("/login")
@limiter.limit("10/minute")
async def login(
    request: Request,
    discord_id: str = Query(..., min_length=1, max_length=32),
):
    if not CLIENT_KEY:
        return _page("Fehler", """
            <div class="icon">⚠️</div>
            <h1>Nicht konfiguriert</h1>
            <p>TIKTOK_CLIENT_KEY fehlt in der .env Datei.</p>
        """)

    state = secrets.token_urlsafe(32)

    # PKCE: code_verifier + code_challenge generieren
    code_verifier  = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    save_state(state=state, discord_id=discord_id, ttl_minutes=10, code_verifier=code_verifier)

    from urllib.parse import urlencode
    params = urlencode({
        "client_key":            CLIENT_KEY,
        "response_type":         "code",
        "scope":                 "user.info.basic",
        "redirect_uri":          REDIRECT_URI,
        "state":                 state,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{AUTHORIZE_URL}?{params}"

    logger.info("OAuth-Flow gestartet (Discord-ID: [REDACTED])")

    return _page("TikTok verbinden", f"""
        <div class="icon">🎵</div>
        <h1>TikTok Account verbinden</h1>
        <p>Melde dich mit deinem TikTok-Account an.<br>
           Wir speichern weder dein Passwort noch deine Session-Daten.</p>
        <a class="btn" id="loginBtn" href="{auth_url}">Mit TikTok anmelden</a>
        <p class="timer">Dieser Link läuft in <span id="cd">10:00</span> ab</p>
        <script>
          // Auf Handys: versuche zuerst die TikTok-App zu öffnen, fallback auf Browser
          var isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
          if (isMobile) {{
            var authUrl = {auth_url!r};
            var btn = document.getElementById('loginBtn');

            // TikTok App-Intent (Android) / Universal Link (iOS)
            // TikTok öffnet bei mobilen Geräten den OAuth-Flow automatisch in der App
            // wenn die App installiert ist – wir triggern das sofort beim Laden
            btn.addEventListener('click', function(e) {{
              e.preventDefault();
              // Versuche App zu öffnen; nach 2s Fallback auf Browser
              var appOpened = false;
              var timer = setTimeout(function() {{
                if (!appOpened) window.location.href = authUrl;
              }}, 1500);
              window.addEventListener('blur', function() {{
                appOpened = true;
                clearTimeout(timer);
              }});
              // TikTok App Deep Link
              window.location.href = 'snssdk1233://aweme/webview?url=' + encodeURIComponent(authUrl);
            }});
          }}

          let s=600;
          const e=document.getElementById('cd');
          const t=setInterval(()=>{{
            s--;
            if(s<=0){{clearInterval(t);e.textContent='abgelaufen';return;}}
            e.textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');
          }},1000);
        </script>
    """)


@app.get("/tiktok/callback")
@limiter.limit("20/minute")
async def tiktok_callback(
    request: Request,
    code:              Optional[str] = Query(None),
    state:             Optional[str] = Query(None),
    error:             Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    # Fehler von TikTok
    if error:
        logger.warning("TikTok OAuth Fehler: %s", error)
        return _page("Abgebrochen", f"""
            <div class="icon">❌</div>
            <h1>Login abgebrochen</h1>
            <p>{error_description or error}</p>
            <p>Starte den Vorgang erneut mit <strong>/tiktok login</strong> im Discord.</p>
        """)

    if not code or not state:
        return _page("Fehler", """
            <div class="icon">❌</div>
            <h1>Ungültige Anfrage</h1>
            <p>Parameter fehlen. Starte den Vorgang erneut im Discord.</p>
        """)

    # CSRF-State prüfen (one-time-use)
    state_data = pop_state(state)
    if not state_data:
        return _page("Fehler", """
            <div class="icon">⏱️</div>
            <h1>Link ungültig oder abgelaufen</h1>
            <p>Dieser Link wurde bereits verwendet oder ist abgelaufen.<br>
               Starte den Vorgang erneut mit <strong>/tiktok login</strong>.</p>
        """)
    discord_id    = state_data["discord_id"]
    code_verifier = state_data["code_verifier"]

    # Code gegen Token tauschen
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                TOKEN_URL,
                headers={
                    "Content-Type":  "application/x-www-form-urlencoded",
                    "Cache-Control": "no-cache",
                },
                data={
                    "client_key":     CLIENT_KEY,
                    "client_secret":  CLIENT_SECRET,
                    "code":           code,
                    "grant_type":     "authorization_code",
                    "redirect_uri":   REDIRECT_URI,
                    "code_verifier":  code_verifier,
                },
            )
            resp.raise_for_status()
            token_data = resp.json()
    except Exception as exc:
        logger.error("Token-Austausch fehlgeschlagen: %s", exc)
        return _page("Fehler", """
            <div class="icon">❌</div>
            <h1>Verbindungsfehler</h1>
            <p>Token-Austausch mit TikTok fehlgeschlagen.<br>Bitte später erneut versuchen.</p>
        """)

    if token_data.get("error"):
        return _page("Fehler", f"""
            <div class="icon">❌</div>
            <h1>TikTok Fehler</h1>
            <p>{token_data.get('error_description', token_data.get('error'))}</p>
        """)

    access_token  = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in    = token_data.get("expires_in", 86400)
    open_id       = token_data.get("open_id")

    if not access_token or not open_id:
        return _page("Fehler", """
            <div class="icon">❌</div>
            <h1>Kein Token erhalten</h1>
            <p>TikTok hat keinen gültigen Token zurückgegeben.</p>
        """)

    # Benutzerprofil abrufen
    display_name = await _fetch_username(access_token)

    # Account speichern
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    upsert_account(
        discord_id=discord_id,
        tiktok_open_id=open_id,
        tiktok_username=display_name,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )

    # Discord-DM senden
    await _notify_discord(discord_id, display_name or "unbekannt")

    logger.info("Account verknüpft (Discord: [REDACTED], TikTok: [REDACTED])")

    return _page("Erfolgreich!", f"""
        <div class="icon">✅</div>
        <h1>Erfolgreich eingeloggt!</h1>
        <p>TikTok-Account:</p>
        <h2 style="color:#3fb950;margin:12px 0">@{display_name or 'unbekannt'}</h2>
        <p>Dein Discord-Bot wurde bereits benachrichtigt.<br>
           Du kannst dieses Fenster schließen.</p>
    """)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

async def _fetch_username(access_token: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                USER_INFO_URL,
                params={"fields": "open_id,display_name,avatar_url"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            user = resp.json().get("data", {}).get("user", {})
            return user.get("display_name") or user.get("username")
    except Exception as exc:
        logger.warning("Username abrufen fehlgeschlagen: %s", exc)
        return None


async def _notify_discord(discord_id: str, display_name: str) -> None:
    if not DISCORD_TOKEN:
        logger.warning("DISCORD_TOKEN fehlt – keine DM möglich.")
        return

    headers = {
        "Authorization": f"Bot {DISCORD_TOKEN}",
        "Content-Type":  "application/json",
    }
    embed = {
        "title":       "✅ TikTok erfolgreich verbunden!",
        "description": f"Du hast dich erfolgreich als **@{display_name}** eingeloggt.",
        "color":       0x3FB950,
        "footer":      {"text": "Keine Passwörter oder Session-Daten wurden gespeichert."},
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            dm = await client.post(
                "https://discord.com/api/v10/users/@me/channels",
                json={"recipient_id": discord_id},
                headers=headers,
            )
            dm.raise_for_status()
            channel_id = dm.json()["id"]

            await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                json={"embeds": [embed]},
                headers=headers,
            )
            logger.info("Discord-DM gesendet (Discord-ID: [REDACTED])")
    except Exception as exc:
        logger.error("Discord-DM fehlgeschlagen: %s", exc)
