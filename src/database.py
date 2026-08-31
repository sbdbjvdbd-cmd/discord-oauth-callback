"""
Datenbankmodul – SQLite, kein SQLAlchemy nötig.
Tabellen:
  - oauth_states           : CSRF-States für den OAuth-Flow
  - linked_accounts        : Verknüpfte TikTok-Accounts
  - pending_notifications  : Ausstehende Discord-Follow-up-Nachrichten
"""

import os
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from cryptography.fernet import Fernet
import base64
import hashlib

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Datenbankpfad
# ---------------------------------------------------------------------------
_DB_URL = os.getenv("DATABASE_URL", "sqlite:///data/app.db")
DB_PATH = _DB_URL.replace("sqlite:///", "").replace("sqlite://", "")


# ---------------------------------------------------------------------------
# Token-Verschlüsselung (Fernet / AES-128)
# ---------------------------------------------------------------------------
def _get_fernet() -> Fernet:
    secret = os.getenv("SECRET_KEY", "fallback-insecure-key-change-me")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_token(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_token(cipher: str) -> str:
    return _get_fernet().decrypt(cipher.encode()).decode()


# ---------------------------------------------------------------------------
# Verbindungs-Context-Manager
# ---------------------------------------------------------------------------
@contextmanager
def get_conn():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tabellen anlegen
# ---------------------------------------------------------------------------
def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS linked_accounts (
                discord_id          TEXT PRIMARY KEY,
                tiktok_open_id      TEXT UNIQUE NOT NULL,
                tiktok_username     TEXT,
                access_token_enc    TEXT NOT NULL,
                refresh_token_enc   TEXT,
                token_expires_at    TEXT,
                linked_at           TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_states (
                state        TEXT PRIMARY KEY,
                discord_id   TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_notifications (
                discord_id      TEXT PRIMARY KEY,
                channel_id      TEXT NOT NULL,
                message_id      TEXT,
                created_at      TEXT NOT NULL,
                notified        INTEGER NOT NULL DEFAULT 0
            );
        """)
    logger.info("Datenbank initialisiert: %s", DB_PATH)


# ---------------------------------------------------------------------------
# OAuth States (CSRF-Schutz)
# ---------------------------------------------------------------------------
def save_state(state: str, discord_id: str, ttl_minutes: int = 10, code_verifier: str = "") -> None:
    now        = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=ttl_minutes)).isoformat()
    with get_conn() as conn:
        try:
            conn.execute("ALTER TABLE oauth_states ADD COLUMN code_verifier TEXT DEFAULT ''")
        except Exception:
            pass
        conn.execute(
            "INSERT OR REPLACE INTO oauth_states (state, discord_id, created_at, expires_at, code_verifier) VALUES (?, ?, ?, ?, ?)",
            (state, discord_id, now.isoformat(), expires_at, code_verifier),
        )
    _cleanup_old_states()


def pop_state(state: str) -> dict | None:
    """Gibt discord_id + code_verifier zurück und löscht den State (one-time-use)."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT discord_id, expires_at, code_verifier FROM oauth_states WHERE state = ?", (state,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < now:
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            logger.warning("OAuth-State abgelaufen.")
            return None
        result = {"discord_id": row["discord_id"], "code_verifier": row["code_verifier"] or ""}
        conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    return result


def _cleanup_old_states():
    cutoff = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", (cutoff,))


# ---------------------------------------------------------------------------
# Pending Notifications (Discord Follow-up nach OAuth)
# ---------------------------------------------------------------------------
def save_pending_notification(discord_id: str, channel_id: str, message_id: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO pending_notifications
               (discord_id, channel_id, message_id, created_at, notified)
               VALUES (?, ?, ?, ?, 0)""",
            (discord_id, channel_id, message_id, now),
        )


def get_pending_notification(discord_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pending_notifications WHERE discord_id = ? AND notified = 0",
            (discord_id,),
        ).fetchone()
    return dict(row) if row else None


def mark_notification_sent(discord_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_notifications SET notified = 1 WHERE discord_id = ?",
            (discord_id,),
        )


def cleanup_old_notifications(ttl_minutes: int = 15) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=ttl_minutes)).isoformat()
    with get_conn() as conn:
        conn.execute("DELETE FROM pending_notifications WHERE created_at < ?", (cutoff,))


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
def upsert_account(
    discord_id: str,
    tiktok_open_id: str,
    tiktok_username: str | None,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
) -> dict:
    now        = datetime.now(timezone.utc).isoformat()
    expires_str = expires_at.isoformat() if expires_at else None
    access_enc  = encrypt_token(access_token)
    refresh_enc = encrypt_token(refresh_token) if refresh_token else None

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT linked_at FROM linked_accounts WHERE discord_id = ?", (discord_id,)
        ).fetchone()
        linked_at = existing["linked_at"] if existing else now

        conn.execute("""
            INSERT INTO linked_accounts
                (discord_id, tiktok_open_id, tiktok_username,
                 access_token_enc, refresh_token_enc,
                 token_expires_at, linked_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                tiktok_open_id    = excluded.tiktok_open_id,
                tiktok_username   = excluded.tiktok_username,
                access_token_enc  = excluded.access_token_enc,
                refresh_token_enc = excluded.refresh_token_enc,
                token_expires_at  = excluded.token_expires_at,
                updated_at        = excluded.updated_at
        """, (discord_id, tiktok_open_id, tiktok_username,
              access_enc, refresh_enc, expires_str, linked_at, now))

    return get_account_by_discord(discord_id)


def get_account_by_discord(discord_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM linked_accounts WHERE discord_id = ?", (discord_id,)
        ).fetchone()
    if not row:
        return None

    account = dict(row)
    try:
        account["access_token"]  = decrypt_token(account["access_token_enc"])
        account["refresh_token"] = decrypt_token(account["refresh_token_enc"]) if account["refresh_token_enc"] else None
    except Exception:
        account["access_token"]  = None
        account["refresh_token"] = None

    if account.get("token_expires_at"):
        expires = datetime.fromisoformat(account["token_expires_at"])
        account["is_expired"] = datetime.now(timezone.utc) >= expires
    else:
        account["is_expired"] = True

    return account


def delete_account(discord_id: str) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM linked_accounts WHERE discord_id = ?", (discord_id,)
        )
    return cursor.rowcount > 0
