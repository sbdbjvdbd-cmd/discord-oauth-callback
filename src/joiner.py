"""
Token-Joiner – fügt Discord User via OAuth2 Tokens zu einem Server hinzu.
Tokens werden aus assets/tokens.txt geladen (eine pro Zeile).
"""

import os
import logging
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

TOKENS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "tokens.txt")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")


def load_tokens() -> list[str]:
    """Lädt alle Tokens aus tokens.txt (leere Zeilen und Kommentare werden ignoriert)."""
    if not os.path.exists(TOKENS_FILE):
        return []
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    tokens = [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]
    return tokens


def count_tokens() -> int:
    return len(load_tokens())


def save_token(token: str) -> None:
    """Fügt einen neuen Token zur tokens.txt hinzu (kein Duplikat)."""
    existing = load_tokens()
    if token in existing:
        return
    os.makedirs(os.path.dirname(TOKENS_FILE), exist_ok=True)
    with open(TOKENS_FILE, "a", encoding="utf-8") as f:
        f.write(token + "\n")
    logger.info("Token gespeichert. Gesamt: %d", count_tokens())


def _join_single(token: str, guild_id: str, role_id: str | None) -> dict:
    """Versucht einen einzelnen Token dem Server hinzuzufügen. Gibt Ergebnis-Dict zurück."""
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    payload: dict = {"access_token": token}
    if role_id:
        payload["roles"] = [role_id]

    # User-ID aus Token holen
    try:
        with httpx.Client(timeout=10) as client:
            me = client.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if me.status_code != 200:
                return {"token": token[:20] + "...", "status": "invalid", "user": None}
            user_data = me.json()
            user_id = user_data.get("id")
            username = user_data.get("username", "?")

            # Zur Guild hinzufügen
            resp = client.put(
                f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}",
                json=payload,
                headers=headers,
            )
            if resp.status_code == 201:
                return {"token": token[:20] + "...", "status": "joined", "user": username}
            elif resp.status_code == 204:
                return {"token": token[:20] + "...", "status": "already_member", "user": username}
            elif resp.status_code == 403:
                return {"token": token[:20] + "...", "status": "forbidden", "user": username}
            else:
                return {"token": token[:20] + "...", "status": f"error_{resp.status_code}", "user": username}
    except Exception as exc:
        return {"token": token[:20] + "...", "status": f"exception: {exc}", "user": None}


def join_guild(
    guild_id: str,
    count: int = 2,
    role_id: str | None = None,
    max_workers: int = 5,
    delay: float = 0.5,
) -> dict:
    """
    Jointet `count` Tokens dem Server guild_id.
    Gibt zurück: {"joined": int, "already": int, "failed": int, "results": list}
    """
    tokens = load_tokens()
    if not tokens:
        return {"joined": 0, "already": 0, "failed": 0, "results": [], "total": 0}

    # Nur so viele nehmen wie angefragt
    tokens_to_use = tokens[:count]
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_join_single, token, guild_id, role_id): token
            for token in tokens_to_use
        }
        for future in as_completed(futures):
            results.append(future.result())

    joined  = sum(1 for r in results if r["status"] == "joined")
    already = sum(1 for r in results if r["status"] == "already_member")
    failed  = sum(1 for r in results if r["status"] not in ("joined", "already_member"))

    logger.info(
        "Join abgeschlossen — Guild: %s | Joined: %d | Already: %d | Failed: %d",
        guild_id, joined, already, failed,
    )
    return {
        "joined":  joined,
        "already": already,
        "failed":  failed,
        "results": results,
        "total":   len(tokens_to_use),
    }
