"""
TikTok Username Checker Service
Prüft ob ein TikTok-Username verfügbar ist.
Status: available | taken | unknown | invalid | ratelimited | error
"""
import re
import time
import logging
import httpx

logger = logging.getLogger(__name__)

# TikTok Username-Regeln: 2-24 Zeichen, a-z, 0-9, Unterstriche, kein __ oder trailing _
USERNAME_RE = re.compile(r'^[a-zA-Z0-9_]{2,24}$')

STATUS_AVAILABLE  = "available"
STATUS_TAKEN      = "taken"
STATUS_UNKNOWN    = "unknown"
STATUS_INVALID    = "invalid"
STATUS_RATELIMIT  = "ratelimited"
STATUS_ERROR      = "error"

# TikTok gibt bei existierenden Accounts HTTP 200 mit user-Daten zurück,
# bei nicht-existierenden einen Redirect oder 404.
TIKTOK_PROFILE_URL = "https://www.tiktok.com/@{username}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def is_valid_username(username: str) -> bool:
    """Prüft ob der Username den TikTok-Regeln entspricht."""
    if not USERNAME_RE.match(username):
        return False
    if username.startswith("_") or username.endswith("_"):
        return False
    if "__" in username:
        return False
    return True


def is_valid_4l(username: str) -> bool:
    """Prüft ob der Username genau 4 Zeichen lang und gültig ist."""
    return len(username) == 4 and is_valid_username(username)


async def check_username(username: str) -> dict:
    """
    Prüft einen TikTok-Username.
    Gibt zurück: {"status": str, "username": str, "response_ms": int}
    """
    username = username.strip().lstrip("@")

    if not is_valid_username(username):
        return {"status": STATUS_INVALID, "username": username, "response_ms": 0}

    url   = TIKTOK_PROFILE_URL.format(username=username)
    start = time.monotonic()

    try:
        async with httpx.AsyncClient(
            timeout=12,
            follow_redirects=False,
            headers=HEADERS,
        ) as client:
            resp = await client.get(url)

        elapsed = int((time.monotonic() - start) * 1000)

        if resp.status_code == 200:
            # Prüfe ob die Seite echten User-Content hat
            body = resp.text
            if (
                '"statusCode":10202' in body          # User not found
                or "Couldn't find this account" in body
                or '"webapp.user-detail":{"statusCode":10' in body
            ):
                return {"status": STATUS_AVAILABLE, "username": username, "response_ms": elapsed}

            if (
                f'"uniqueId":"{username}"' in body.lower()
                or f'"uniqueId":"{username.lower()}"' in body
                or '"user":{"id"' in body
            ):
                return {"status": STATUS_TAKEN, "username": username, "response_ms": elapsed}

            # Seite geladen aber unklar — sicher Unknown zurückgeben
            return {"status": STATUS_UNKNOWN, "username": username, "response_ms": elapsed}

        elif resp.status_code in (301, 302, 308):
            loc = resp.headers.get("location", "")
            if "login" in loc or "not-found" in loc or "404" in loc:
                return {"status": STATUS_AVAILABLE, "username": username, "response_ms": elapsed}
            return {"status": STATUS_UNKNOWN, "username": username, "response_ms": elapsed}

        elif resp.status_code == 404:
            return {"status": STATUS_AVAILABLE, "username": username, "response_ms": elapsed}

        elif resp.status_code == 429:
            return {"status": STATUS_RATELIMIT, "username": username, "response_ms": elapsed}

        else:
            logger.warning("Unexpected status %d for @%s", resp.status_code, username)
            return {"status": STATUS_UNKNOWN, "username": username, "response_ms": elapsed}

    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.warning("Timeout checking @%s", username)
        return {"status": STATUS_UNKNOWN, "username": username, "response_ms": elapsed}
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.error("Error checking @%s: %s", username, exc)
        return {"status": STATUS_ERROR, "username": username, "response_ms": elapsed}


def status_emoji(status: str) -> str:
    return {
        STATUS_AVAILABLE: "🟢",
        STATUS_TAKEN:     "🔴",
        STATUS_UNKNOWN:   "🟡",
        STATUS_INVALID:   "⚠️",
        STATUS_RATELIMIT: "🔵",
        STATUS_ERROR:     "🔧",
    }.get(status, "❓")


def status_label(status: str) -> str:
    return {
        STATUS_AVAILABLE: "AVAILABLE",
        STATUS_TAKEN:     "TAKEN",
        STATUS_UNKNOWN:   "UNKNOWN",
        STATUS_INVALID:   "INVALID",
        STATUS_RATELIMIT: "RATE LIMITED",
        STATUS_ERROR:     "ERROR",
    }.get(status, status.upper())
