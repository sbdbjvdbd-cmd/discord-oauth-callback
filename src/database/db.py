"""
Datenbank-Layer – SQLite via aiosqlite (async).
Tabellen:
  linked_accounts  – TikTok OAuth Tokens pro Discord User
  rate_limits      – Cooldown/Tageslimit Tracking
  username_cache   – Cache für Checker-Ergebnisse
"""
import os
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "data/app.db")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Erstellt alle Tabellen falls nicht vorhanden."""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS linked_accounts (
                discord_id         TEXT PRIMARY KEY,
                tiktok_open_id     TEXT UNIQUE NOT NULL,
                tiktok_username    TEXT,
                display_name       TEXT,
                avatar_url         TEXT,
                is_verified        INTEGER DEFAULT 0,
                access_token_enc   TEXT NOT NULL,
                refresh_token_enc  TEXT,
                token_expires_at   TEXT,
                scopes             TEXT,
                linked_at          TEXT NOT NULL,
                updated_at         TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_states (
                state       TEXT PRIMARY KEY,
                discord_id  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rate_limits (
                key         TEXT PRIMARY KEY,
                count       INTEGER DEFAULT 0,
                window_start TEXT NOT NULL,
                day_count   INTEGER DEFAULT 0,
                day_start   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS username_cache (
                username    TEXT PRIMARY KEY,
                status      TEXT NOT NULL,
                checked_at  TEXT NOT NULL
            );
        """)
    logger.info("Datenbank initialisiert: %s", DB_PATH)


# ── OAuth States ──────────────────────────────────────────────────────────────

def save_state(state: str, discord_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO oauth_states (state, discord_id, created_at) VALUES (?,?,?)",
            (state, discord_id, now),
        )


def pop_state(state: str) -> Optional[str]:
    """Gibt discord_id zurück und löscht den State."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT discord_id, created_at FROM oauth_states WHERE state=?", (state,)
        ).fetchone()
        if not row:
            return None
        # State darf max. 10 Minuten alt sein
        created = datetime.fromisoformat(row["created_at"])
        if datetime.now(timezone.utc) - created > timedelta(minutes=10):
            conn.execute("DELETE FROM oauth_states WHERE state=?", (state,))
            return None
        conn.execute("DELETE FROM oauth_states WHERE state=?", (state,))
        return row["discord_id"]


# ── Accounts ──────────────────────────────────────────────────────────────────

def upsert_account(
    discord_id: str,
    tiktok_open_id: str,
    tiktok_username: str,
    display_name: str,
    avatar_url: str,
    is_verified: bool,
    access_token_enc: str,
    refresh_token_enc: str,
    token_expires_at: str,
    scopes: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO linked_accounts
                (discord_id, tiktok_open_id, tiktok_username, display_name, avatar_url,
                 is_verified, access_token_enc, refresh_token_enc, token_expires_at,
                 scopes, linked_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(discord_id) DO UPDATE SET
                tiktok_open_id    = excluded.tiktok_open_id,
                tiktok_username   = excluded.tiktok_username,
                display_name      = excluded.display_name,
                avatar_url        = excluded.avatar_url,
                is_verified       = excluded.is_verified,
                access_token_enc  = excluded.access_token_enc,
                refresh_token_enc = excluded.refresh_token_enc,
                token_expires_at  = excluded.token_expires_at,
                scopes            = excluded.scopes,
                updated_at        = excluded.updated_at
        """, (
            discord_id, tiktok_open_id, tiktok_username, display_name, avatar_url,
            int(is_verified), access_token_enc, refresh_token_enc, token_expires_at,
            scopes, now, now,
        ))


def get_account(discord_id: str) -> Optional[dict]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM linked_accounts WHERE discord_id=?", (discord_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    # Token abgelaufen?
    if d.get("token_expires_at"):
        expires = datetime.fromisoformat(d["token_expires_at"])
        d["is_expired"] = datetime.now(timezone.utc) > expires
    else:
        d["is_expired"] = False
    return d


def delete_account(discord_id: str) -> bool:
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM linked_accounts WHERE discord_id=?", (discord_id,)
        )
    return cur.rowcount > 0


def update_tokens(discord_id: str, access_token_enc: str, refresh_token_enc: str, expires_at: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute("""
            UPDATE linked_accounts
            SET access_token_enc=?, refresh_token_enc=?, token_expires_at=?, updated_at=?
            WHERE discord_id=?
        """, (access_token_enc, refresh_token_enc, expires_at, now, discord_id))


# ── Rate Limits ───────────────────────────────────────────────────────────────

def check_rate_limit(key: str, per_minute: int, per_day: int) -> dict:
    """
    Prüft und inkrementiert das Rate Limit für einen Key.
    Gibt zurück: {"allowed": bool, "minute_remaining": int, "day_remaining": int}
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM rate_limits WHERE key=?", (key,)).fetchone()

        if row:
            row = dict(row)
            window_start = datetime.fromisoformat(row["window_start"])
            day_start    = datetime.fromisoformat(row["day_start"])

            # Minute-Fenster zurücksetzen
            if (now - window_start).total_seconds() >= 60:
                row["count"]        = 0
                row["window_start"] = now_iso

            # Tag-Fenster zurücksetzen
            if (now - day_start).total_seconds() >= 86400:
                row["day_count"] = 0
                row["day_start"] = now_iso
        else:
            row = {"key": key, "count": 0, "window_start": now_iso, "day_count": 0, "day_start": now_iso}

        allowed = row["count"] < per_minute and row["day_count"] < per_day

        if allowed:
            row["count"]     += 1
            row["day_count"] += 1

        conn.execute("""
            INSERT OR REPLACE INTO rate_limits (key, count, window_start, day_count, day_start)
            VALUES (?,?,?,?,?)
        """, (row["key"], row["count"], row["window_start"], row["day_count"], row["day_start"]))

    return {
        "allowed":           allowed,
        "minute_remaining":  max(0, per_minute - row["count"]),
        "day_remaining":     max(0, per_day    - row["day_count"]),
    }


# ── Username Cache ────────────────────────────────────────────────────────────

def get_cached(username: str, max_age_seconds: int = 300) -> Optional[str]:
    """Gibt gecachten Status zurück falls nicht zu alt."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT status, checked_at FROM username_cache WHERE username=?",
            (username.lower(),),
        ).fetchone()
    if not row:
        return None
    checked = datetime.fromisoformat(row["checked_at"])
    if (datetime.now(timezone.utc) - checked).total_seconds() > max_age_seconds:
        return None
    return row["status"]


def set_cache(username: str, status: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO username_cache (username, status, checked_at) VALUES (?,?,?)",
            (username.lower(), status, now),
        )
