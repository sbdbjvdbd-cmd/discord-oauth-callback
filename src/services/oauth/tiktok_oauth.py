"""
TikTok OAuth 2.0 Service
Handles: Authorization URL, Token Exchange, Token Refresh, Token Revoke, User Info
"""
import os
import secrets
import hashlib
import base64
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TIKTOK_AUTH_URL     = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL    = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_REVOKE_URL   = "https://open.tiktokapis.com/v2/oauth/revoke/"
TIKTOK_USERINFO_URL = "https://open.tiktokapis.com/v2/user/info/"

CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
REDIRECT_URI  = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080/tiktok/callback")

SCOPES = "user.info.basic,user.info.profile,user.info.stats"


def generate_pkce() -> tuple[str, str]:
    """Erzeugt PKCE code_verifier und code_challenge (S256)."""
    verifier  = secrets.token_urlsafe(64)
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(state: str, code_challenge: str) -> str:
    """Baut die TikTok Authorization URL."""
    from urllib.parse import urlencode
    params = urlencode({
        "client_key":             CLIENT_KEY,
        "response_type":          "code",
        "scope":                  SCOPES,
        "redirect_uri":           REDIRECT_URI,
        "state":                  state,
        "code_challenge":         code_challenge,
        "code_challenge_method":  "S256",
    })
    return f"{TIKTOK_AUTH_URL}?{params}"


async def exchange_code(code: str, code_verifier: str) -> Optional[dict]:
    """Tauscht Authorization Code gegen Access/Refresh Token."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                TIKTOK_TOKEN_URL,
                data={
                    "client_key":     CLIENT_KEY,
                    "client_secret":  CLIENT_SECRET,
                    "code":           code,
                    "grant_type":     "authorization_code",
                    "redirect_uri":   REDIRECT_URI,
                    "code_verifier":  code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            logger.error("Token exchange failed: %d %s", resp.status_code, resp.text)
            return None
        data = resp.json()
        if data.get("error"):
            logger.error("Token exchange error: %s", data)
            return None
        return data
    except Exception as exc:
        logger.error("Token exchange exception: %s", exc)
        return None


async def refresh_access_token(refresh_token: str) -> Optional[dict]:
    """Erneuert den Access Token via Refresh Token."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                TIKTOK_TOKEN_URL,
                data={
                    "client_key":     CLIENT_KEY,
                    "client_secret":  CLIENT_SECRET,
                    "grant_type":     "refresh_token",
                    "refresh_token":  refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            logger.error("Token refresh failed: %d", resp.status_code)
            return None
        data = resp.json()
        if data.get("error"):
            logger.error("Token refresh error: %s", data)
            return None
        return data
    except Exception as exc:
        logger.error("Token refresh exception: %s", exc)
        return None


async def revoke_token(token: str) -> bool:
    """Widerruft einen Token über den offiziellen TikTok Revoke-Endpunkt."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                TIKTOK_REVOKE_URL,
                data={
                    "client_key":    CLIENT_KEY,
                    "client_secret": CLIENT_SECRET,
                    "token":         token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        return resp.status_code == 200
    except Exception as exc:
        logger.error("Token revoke exception: %s", exc)
        return False


async def get_user_info(access_token: str) -> Optional[dict]:
    """Ruft User-Informationen über die TikTok User Info API ab."""
    fields = "open_id,union_id,avatar_url,display_name,bio_description,profile_deep_link,is_verified,username,follower_count,following_count,likes_count,video_count"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                TIKTOK_USERINFO_URL,
                params={"fields": fields},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            logger.error("User info failed: %d", resp.status_code)
            return None
        data = resp.json()
        if data.get("error", {}).get("code") != "ok":
            logger.error("User info error: %s", data)
            return None
        return data.get("data", {}).get("user", {})
    except Exception as exc:
        logger.error("User info exception: %s", exc)
        return None


def parse_token_expiry(expires_in: int) -> str:
    """Berechnet absoluten Ablauf-Zeitstempel aus expires_in (Sekunden)."""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return expires_at.isoformat()
