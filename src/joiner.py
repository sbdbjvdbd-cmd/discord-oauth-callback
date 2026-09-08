"""
Token-Joiner – Discord User-Tokens joinen sich selbst via OAuth2 einem Server hinzu.
Tokens werden aus data/tokens.txt geladen (eine pro Zeile).
"""

import os
import time
import logging
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

TOKENS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tokens.txt")
DISCORD_BOT_TOKEN  = os.getenv("DISCORD_TOKEN", "")
DISCORD_CLIENT_ID  = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
BASE_URL = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080").rsplit("/tiktok", 1)[0]
REDIRECT_URI = f"{BASE_URL}/discord/callback"

# Discord baut number – wird beim Start geholt
try:
    _build = int(httpx.get(
        "https://raw.githubusercontent.com/EffeDiscord/discord-api/main/fetch",
        timeout=5
    ).json()["client_build_number"])
except Exception:
    _build = 179882

X_SUPER_PROPERTIES = "eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiRGlzY29yZCBDbGllbnQiLCJyZWxlYXNlX2NoYW5uZWwiOiJzdGFibGUiLCJjbGllbnRfdmVyc2lvbiI6IjEuMC45MDExIiwib3NfdmVyc2lvbiI6IjEwLjAuMTkwNDUiLCJvc19hcmNoIjoieDY0Iiwic3lzdGVtX2xvY2FsZSI6ImVuLVVTIiwiY2xpZW50X2J1aWxkX251bWJlciI6MTc5ODgyLCJuYXRpdmVfYnVpbGRfbnVtYmVyIjozMDMwNiwiY2xpZW50X2V2ZW50X3NvdXJjZSI6bnVsbCwiZGVzaWduX2lkIjowfQ=="


def _make_headers(token: str) -> dict:
    return {
        "accept-encoding":   "gzip, deflate, br",
        "accept-language":   "en-US",
        "authorization":     token,
        "referer":           "https://discord.com/channels/@me",
        "sec-fetch-dest":    "empty",
        "sec-fetch-mode":    "cors",
        "sec-fetch-site":    "same-origin",
        "user-agent":        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9011 Chrome/91.0.4472.164 Electron/13.6.6 Safari/537.36",
        "x-debug-options":   "bugReporterEnabled",
        "x-discord-locale":  "en-US",
        "x-super-properties": X_SUPER_PROPERTIES,
    }


def load_tokens() -> list[str]:
    """Lädt alle Tokens aus data/tokens.txt."""
    if not os.path.exists(TOKENS_FILE):
        os.makedirs(os.path.dirname(TOKENS_FILE), exist_ok=True)
        open(TOKENS_FILE, "w").close()
        return []
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def save_token(token: str) -> None:
    """Fügt einen neuen Token zur tokens.txt hinzu (kein Duplikat)."""
    existing = load_tokens()
    if token in existing:
        return
    os.makedirs(os.path.dirname(TOKENS_FILE), exist_ok=True)
    with open(TOKENS_FILE, "a", encoding="utf-8") as f:
        f.write(token + "\n")
    logger.info("Token gespeichert. Gesamt: %d", count_tokens())


def count_tokens() -> int:
    return len(load_tokens())


def _join_single(token: str, guild_id: str) -> dict:
    """Ein Token führt den OAuth2-Flow durch und jointet die Guild."""
    headers = _make_headers(token)
    try:
        with httpx.Client(timeout=15) as client:
            # Schritt 1: OAuth2 Authorize — Token autorisiert sich selbst
            resp = client.post(
                "https://discord.com/api/v9/oauth2/authorize",
                headers=headers,
                params={
                    "client_id":     DISCORD_CLIENT_ID,
                    "response_type": "code",
                    "redirect_uri":  REDIRECT_URI,
                    "scope":         "identify guilds.join",
                },
                json={"permissions": "0", "authorize": True},
            )

            if resp.status_code == 401:
                return {"token": token[:20] + "...", "status": "invalid_token", "user": None}
            if resp.status_code == 429:
                return {"token": token[:20] + "...", "status": "ratelimited", "user": None}
            if resp.status_code != 200:
                return {"token": token[:20] + "...", "status": f"auth_error_{resp.status_code}", "user": None}

            location = resp.json().get("location")
            if not location:
                return {"token": token[:20] + "...", "status": "no_location", "user": None}

            # Schritt 2: Code aus der Redirect-URL holen
            code_resp = client.get(location, follow_redirects=False)
            redirect_url = code_resp.headers.get("location", location)

            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(redirect_url)
            code = parse_qs(parsed.query).get("code", [None])[0]

            if not code:
                return {"token": token[:20] + "...", "status": "no_code", "user": None}

            # Schritt 3: Code gegen Access Token tauschen
            token_resp = client.post(
                "https://discord.com/api/v10/oauth2/token",
                data={
                    "client_id":     DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type":    "authorization_code",
                    "code":          code,
                    "redirect_uri":  REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_resp.status_code != 200:
                return {"token": token[:20] + "...", "status": f"token_error_{token_resp.status_code}", "user": None}

            access_token = token_resp.json().get("access_token")

            # Schritt 4: User-Info holen
            me = client.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_data = me.json()
            user_id  = user_data.get("id")
            username = user_data.get("username", "?")

            # Schritt 5: User zur Guild hinzufügen via Bot-Token
            join = client.put(
                f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}",
                json={"access_token": access_token},
                headers={
                    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                    "Content-Type":  "application/json",
                },
            )

            if join.status_code == 201:
                return {"token": token[:20] + "...", "status": "joined", "user": username}
            elif join.status_code == 204:
                return {"token": token[:20] + "...", "status": "already_member", "user": username}
            else:
                return {"token": token[:20] + "...", "status": f"join_error_{join.status_code}", "user": username}

    except Exception as exc:
        return {"token": token[:20] + "...", "status": f"exception: {exc}", "user": None}


def join_guild(
    guild_id: str,
    count: int = 2,
    role_id: str | None = None,
    max_workers: int = 2,
    delay: float = 0.5,
) -> dict:
    """Jointet `count` Tokens dem Server guild_id."""
    tokens = load_tokens()
    if not tokens:
        return {"joined": 0, "already": 0, "failed": 0, "results": [], "total": 0}

    tokens_to_use = tokens[:count]
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for i, token in enumerate(tokens_to_use):
            if i > 0:
                time.sleep(delay)  # Rate-Limit vermeiden
            futures.append(executor.submit(_join_single, token, guild_id))

        for future in as_completed(futures):
            results.append(future.result())

    joined  = sum(1 for r in results if r["status"] == "joined")
    already = sum(1 for r in results if r["status"] == "already_member")
    failed  = sum(1 for r in results if r["status"] not in ("joined", "already_member"))

    logger.info("Join — Guild: %s | ✅ %d | ℹ️ %d | ❌ %d", guild_id, joined, already, failed)
    return {"joined": joined, "already": already, "failed": failed, "results": results, "total": len(tokens_to_use)}
