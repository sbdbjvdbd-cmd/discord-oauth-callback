"""
Zentrale Konfiguration – alle Werte kommen aus Environment Variables.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Discord ──────────────────────────────────────────────────────────────────
DISCORD_TOKEN         = os.getenv("DISCORD_TOKEN", "")
DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
GUILD_ID              = int(os.getenv("GUILD_ID", "0") or 0)

# ── TikTok OAuth ─────────────────────────────────────────────────────────────
TIKTOK_CLIENT_KEY     = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET  = os.getenv("TIKTOK_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI    = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080/tiktok/callback")
BASE_URL              = OAUTH_REDIRECT_URI.rsplit("/tiktok/callback", 1)[0].rstrip("/")

# ── Server ───────────────────────────────────────────────────────────────────
SECRET_KEY            = os.getenv("SECRET_KEY", "change-me")
SERVER_PORT           = int(os.getenv("PORT", os.getenv("SERVER_PORT", "8080")))
DATABASE_URL          = os.getenv("DATABASE_URL", "sqlite:///data/app.db")
DATABASE_PATH         = os.getenv("DATABASE_PATH", "data/app.db")

# ── Rate Limits ───────────────────────────────────────────────────────────────
RATE_USERNAME_CHECK   = int(os.getenv("RATE_USERNAME_CHECK",  "10"))   # pro Minute
RATE_4L_FINDER        = int(os.getenv("RATE_4L_FINDER",        "1"))    # pro Minute
RATE_4L_FINDER_DAY    = int(os.getenv("RATE_4L_FINDER_DAY",   "20"))   # pro Tag
FINDER_MAX_CHECKS     = int(os.getenv("FINDER_MAX_CHECKS",    "30"))   # max Checks pro Aufruf
FINDER_MAX_PARALLEL   = int(os.getenv("FINDER_MAX_PARALLEL",   "3"))    # parallele Checks

# ── Bot ───────────────────────────────────────────────────────────────────────
ALLOWED_CHANNEL_ID    = int(os.getenv("ALLOWED_CHANNEL_ID", "0") or 0)
