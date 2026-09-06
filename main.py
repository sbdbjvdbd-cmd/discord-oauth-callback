"""
Einstiegspunkt – startet OAuth-Server und Discord-Bot gleichzeitig.
Verwendung: python main.py
"""

import logging
import os
import threading
import time

import uvicorn
from dotenv import load_dotenv

load_dotenv()

from src.oauth_server import app as fastapi_app
from src.bot import run as run_bot

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _start_discord_bot():
    """Startet den Discord-Bot in einem eigenen Thread."""
    try:
        logger.info("Discord-Bot wird gestartet...")
        run_bot()
    except Exception as exc:
        logger.error("Discord-Bot Fehler: %s", exc)


def main():
    logger.info("Starte OAuth-Server und Discord-Bot...")

    # Discord-Bot in Hintergrund-Thread starten
    bot_thread = threading.Thread(target=_start_discord_bot, daemon=True)
    bot_thread.start()
    logger.info("Discord-Bot-Thread gestartet.")

    # Render setzt PORT automatisch – darauf hören damit Healthcheck funktioniert
    port = int(os.getenv("PORT", os.getenv("SERVER_PORT", "8080")))
    logger.info("OAuth-Server startet auf Port %d ...", port)

    # FastAPI/uvicorn im Hauptthread – Render's Healthcheck erwartet das hier
    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
