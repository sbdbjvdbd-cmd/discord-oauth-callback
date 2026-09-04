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
import stripe
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

STRIPE_SECRET_KEY  = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLIC_KEY  = os.getenv("STRIPE_PUBLIC_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
BASE_URL           = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080").rsplit("/tiktok", 1)[0]

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Produkte
PRODUCTS = [
    {
        "id":          "tiktok_font",
        "name":        "TikTok Font Methode",
        "emoji":       "🎵",
        "description": "Lerne wie du mit TikTok-Fonts viral gehst und dein Profil hervorhebst.",
        "features":    ["Vollständige Anleitung", "Sofort-Download", "Lifetime Zugang"],
        "price":       1500,  # Cent = 15.00€
        "color":       "#fe2c55",
    },
    {
        "id":          "members_farm",
        "name":        "Members Farm",
        "emoji":       "👥",
        "description": "Methode um deinen Discord Server schnell mit aktiven Mitgliedern zu füllen.",
        "features":    ["Bewährte Strategie", "Schritt-für-Schritt", "Support inklusive"],
        "price":       799,   # Cent = 7.99€
        "color":       "#5865f2",
    },
    {
        "id":          "yt_farm",
        "name":        "YT Farm",
        "emoji":       "📺",
        "description": "Wachse deinen YouTube-Kanal schnell und organisch mit dieser Methode.",
        "features":    ["YouTube Strategie", "Monetarisierung", "Insider-Tipps"],
        "price":       999,   # Cent = 9.99€
        "color":       "#ff0000",
    },
]

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


@app.get("/tiktok{token}.txt")
def tiktok_verify_dynamic(token: str):
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(f"tiktok-developers-site-verification={token}")


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


# ---------------------------------------------------------------------------
# Shop & Stripe
# ---------------------------------------------------------------------------

@app.get("/shop")
async def shop(request: Request):
    cards = ""
    for p in PRODUCTS:
        features_html = "".join(f'<li>✓ {f}</li>' for f in p["features"])
        price_eur = f"{p['price'] / 100:.2f}".replace(".", ",")
        cards += f"""
        <div class="card">
          <div class="card-header" style="background:linear-gradient(135deg,{p['color']}22,{p['color']}44)">
            <span class="emoji">{p['emoji']}</span>
            <div class="badge" style="background:{p['color']}">NEU</div>
          </div>
          <div class="card-body">
            <h2>{p['name']}</h2>
            <p class="desc">{p['description']}</p>
            <ul class="features">{features_html}</ul>
            <div class="price-row">
              <span class="price">{price_eur}€</span>
              <span class="price-sub">einmalig</span>
            </div>
            <a class="buy-btn" href="/shop/checkout/{p['id']}" style="background:{p['color']}">
              Jetzt kaufen
            </a>
            <div class="payment-icons">
              <span title="Apple Pay">🍎</span>
              <span title="Google Pay">G</span>
              <span title="Kreditkarte">💳</span>
              <span title="PayPal">P</span>
              <span title="Klarna">K</span>
              <span title="SEPA">🏦</span>
            </div>
          </div>
        </div>"""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>zpynq Shop</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Inter',sans-serif;background:#0a0a0a;color:#fff;min-height:100vh}}

    /* Header */
    .header{{background:linear-gradient(135deg,#111 0%,#1a1a2e 100%);padding:60px 20px 40px;text-align:center;border-bottom:1px solid #222;position:relative;overflow:hidden}}
    .header::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,#fe2c5511 0%,transparent 60%);pointer-events:none}}
    .logo{{font-size:2.8rem;font-weight:800;background:linear-gradient(135deg,#fe2c55,#ff6b9d);-webkit-background-clip:text;-webkit-text-fill-color:transparent;letter-spacing:-1px}}
    .tagline{{color:#666;margin-top:8px;font-size:1rem}}
    .trust-badges{{display:flex;justify-content:center;gap:20px;margin-top:24px;flex-wrap:wrap}}
    .badge-item{{background:#1a1a1a;border:1px solid #333;border-radius:20px;padding:6px 14px;font-size:.8rem;color:#aaa;display:flex;align-items:center;gap:6px}}

    /* Grid */
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:24px;max-width:1100px;margin:50px auto;padding:0 20px}}

    /* Card */
    .card{{background:#111;border:1px solid #222;border-radius:20px;overflow:hidden;transition:transform .2s,box-shadow .2s}}
    .card:hover{{transform:translateY(-6px);box-shadow:0 20px 60px rgba(0,0,0,.5)}}
    .card-header{{padding:30px;text-align:center;position:relative}}
    .emoji{{font-size:3.5rem}}
    .badge{{position:absolute;top:12px;right:12px;padding:4px 10px;border-radius:20px;font-size:.7rem;font-weight:700;color:#fff}}
    .card-body{{padding:24px}}
    h2{{font-size:1.2rem;font-weight:700;margin-bottom:8px}}
    .desc{{color:#666;font-size:.88rem;line-height:1.6;margin-bottom:16px}}
    .features{{list-style:none;margin-bottom:20px}}
    .features li{{color:#aaa;font-size:.85rem;padding:4px 0;border-bottom:1px solid #1a1a1a}}
    .features li:last-child{{border:none}}
    .price-row{{display:flex;align-items:baseline;gap:8px;margin-bottom:16px}}
    .price{{font-size:2rem;font-weight:800}}
    .price-sub{{color:#555;font-size:.85rem}}
    .buy-btn{{display:block;text-align:center;color:#fff;font-weight:700;font-size:1rem;padding:14px;border-radius:12px;text-decoration:none;transition:opacity .2s,transform .1s}}
    .buy-btn:hover{{opacity:.85;transform:scale(1.02)}}
    .payment-icons{{display:flex;gap:8px;margin-top:14px;justify-content:center;flex-wrap:wrap}}
    .payment-icons span{{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:4px 10px;font-size:.8rem;cursor:default}}

    /* Footer */
    .footer{{text-align:center;padding:40px 20px;color:#444;font-size:.8rem;border-top:1px solid #1a1a1a;margin-top:40px}}
    .footer a{{color:#666;text-decoration:none}}
    .secure{{display:flex;align-items:center;justify-content:center;gap:8px;margin-bottom:8px;color:#555}}
  </style>
</head>
<body>
  <div class="header">
    <div class="logo">zpynq</div>
    <p class="tagline">Premium Methoden für deinen Erfolg</p>
    <div class="trust-badges">
      <div class="badge-item">🔒 SSL Verschlüsselt</div>
      <div class="badge-item">⚡ Sofort-Zugang</div>
      <div class="badge-item">💳 Sicher mit Stripe</div>
      <div class="badge-item">✅ Lifetime Zugang</div>
    </div>
  </div>

  <div class="grid">
    {cards}
  </div>

  <div class="footer">
    <div class="secure">🔒 Sichere Zahlung über Stripe — Deine Daten sind verschlüsselt</div>
    <a href="/privacy">Datenschutz</a> · © 2026 zpynq Shop
  </div>
</body>
</html>""")


@app.get("/shop/checkout/{product_id}")
@limiter.limit("20/minute")
async def checkout(request: Request, product_id: str):
    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if not product:
        return _page("Fehler", "<div class='icon'>❌</div><h1>Produkt nicht gefunden</h1>")

    if not STRIPE_SECRET_KEY:
        return _page("Fehler", "<div class='icon'>⚠️</div><h1>Zahlung nicht konfiguriert</h1><p>Stripe Key fehlt.</p>")

    try:
        session = stripe.checkout.Session.create(
            line_items=[{
                "price_data": {
                    "currency":     "eur",
                    "unit_amount":  product["price"],
                    "product_data": {
                        "name":        product["name"],
                        "description": product["description"],
                    },
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{BASE_URL}/shop/success?session_id={{CHECKOUT_SESSION_ID}}&product={product_id}",
            cancel_url=f"{BASE_URL}/shop/cancel",
            locale="de",
            allow_promotion_codes=True,
            automatic_tax={"enabled": False},
        )
        return RedirectResponse(session.url, status_code=303)
    except Exception as exc:
        logger.error("Stripe Checkout Fehler: %s", exc)
        return _page("Fehler", f"<div class='icon'>❌</div><h1>Zahlung fehlgeschlagen</h1><p>{exc}</p>")


@app.get("/shop/success")
async def shop_success(request: Request, session_id: str = Query(None), product: str = Query(None)):
    product_data = next((p for p in PRODUCTS if p["id"] == product), None)
    product_name = product_data["name"] if product_data else "Produkt"
    emoji = product_data["emoji"] if product_data else "✅"

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Zahlung erfolgreich – zpynq Shop</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Inter',sans-serif;background:#0a0a0a;color:#fff;display:flex;justify-content:center;align-items:center;min-height:100vh}}
    .box{{background:#111;border:1px solid #1a3a1a;border-radius:24px;padding:48px 40px;text-align:center;max-width:440px;width:90%}}
    .check{{font-size:4rem;margin-bottom:16px;animation:pop .4s ease}}
    @keyframes pop{{0%{{transform:scale(0)}}80%{{transform:scale(1.2)}}100%{{transform:scale(1)}}}}
    h1{{font-size:1.6rem;font-weight:800;color:#3fb950;margin-bottom:8px}}
    .product{{background:#1a2a1a;border:1px solid #2a4a2a;border-radius:12px;padding:16px;margin:20px 0}}
    .product-name{{font-size:1.1rem;font-weight:700}}
    p{{color:#888;font-size:.95rem;line-height:1.7;margin-top:12px}}
    .btn{{display:inline-block;background:#3fb950;color:#fff;font-weight:700;padding:14px 32px;border-radius:12px;text-decoration:none;margin-top:24px;font-size:1rem}}
    .btn:hover{{background:#2ea043}}
  </style>
</head>
<body>
  <div class="box">
    <div class="check">✅</div>
    <h1>Zahlung erfolgreich!</h1>
    <div class="product">
      <div style="font-size:2rem">{emoji}</div>
      <div class="product-name">{product_name}</div>
    </div>
    <p>Vielen Dank für deinen Kauf!<br>
       Du bekommst den Zugang per Discord-DM zugeschickt.<br>
       Tritt unserem Discord bei falls noch nicht passiert.</p>
    <a class="btn" href="/shop">Zurück zum Shop</a>
  </div>
</body>
</html>""")


@app.get("/shop/cancel")
async def shop_cancel(request: Request):
    return HTMLResponse("""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Abgebrochen – zpynq Shop</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Inter',sans-serif;background:#0a0a0a;color:#fff;display:flex;justify-content:center;align-items:center;min-height:100vh}
    .box{background:#111;border:1px solid #333;border-radius:24px;padding:48px 40px;text-align:center;max-width:440px;width:90%}
    h1{font-size:1.5rem;font-weight:800;color:#aaa;margin:16px 0 8px}
    p{color:#666;font-size:.95rem;line-height:1.7}
    .btn{display:inline-block;background:#222;border:1px solid #444;color:#fff;font-weight:600;padding:14px 32px;border-radius:12px;text-decoration:none;margin-top:24px}
    .btn:hover{background:#333}
  </style>
</head>
<body>
  <div class="box">
    <div style="font-size:3.5rem">😕</div>
    <h1>Zahlung abgebrochen</h1>
    <p>Kein Problem — du kannst jederzeit zurückkehren und den Kauf abschließen.</p>
    <a class="btn" href="/shop">Zurück zum Shop</a>
  </div>
</body>
</html>""")
