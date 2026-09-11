"""
Einstiegspunkt – startet OAuth-Server (FastAPI) und Discord-Bot gleichzeitig.
"""
import logging
import os
import threading

import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _start_bot():
    from src.bot import run
    try:
        run()
    except Exception as exc:
        logger.error("Discord Bot Fehler: %s", exc)


def main():
    # Bot in Hintergrund-Thread
    bot_thread = threading.Thread(target=_start_bot, daemon=True)
    bot_thread.start()
    logger.info("Discord Bot Thread gestartet.")

    # FastAPI im Hauptthread — Render Healthcheck erwartet HTTP-Antwort
    from src.oauth_server import app as fastapi_app
    port = int(os.getenv("PORT", os.getenv("SERVER_PORT", "8080")))
    logger.info("OAuth Server startet auf Port %d ...", port)

    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
