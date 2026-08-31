"""
Einstiegspunkt – startet OAuth-Server und Discord-Bot gleichzeitig.
Verwendung: python main.py
"""

import asyncio
import logging
import os
import threading

import uvicorn
from dotenv import load_dotenv

load_dotenv()

from src.oauth_server import app as fastapi_app
from src.bot import run as run_bot

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _start_oauth_server():
    """Startet den FastAPI-OAuth-Server in einem eigenen Thread."""
    port = int(os.getenv("SERVER_PORT", "8080"))
    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )


def main():
    logger.info("Starte OAuth-Server und Discord-Bot...")

    # OAuth-Server in Hintergrund-Thread
    server_thread = threading.Thread(target=_start_oauth_server, daemon=True)
    server_thread.start()
    logger.info("OAuth-Server gestartet.")

    # Discord-Bot im Hauptthread (blockierend)
    run_bot()


if __name__ == "__main__":
    main()
