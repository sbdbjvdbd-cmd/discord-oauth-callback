# TikTok + Discord OAuth Login System

Verbindet TikTok-Accounts sicher mit Discord über den offiziellen TikTok OAuth 2.0 Flow.

## Projektstruktur

```
tiktok-username-checker-main/
├── src/
│   ├── __init__.py
│   ├── bot.py           # Discord Bot (Slash Commands)
│   ├── database.py      # SQLAlchemy Modelle & DB-Hilfsfunktionen
│   └── oauth_server.py  # FastAPI OAuth-Callback-Server
├── data/                # SQLite Datenbank (automatisch erstellt)
├── main.py              # Einstiegspunkt (startet Bot + Server)
├── requirements.txt
├── .env                 # Deine Konfiguration (nicht committen!)
├── .env.example         # Vorlage
└── start.bat            # Windows Start-Skript
```

## Ablauf

```
Nutzer: /tiktok verbinden
    ↓
Discord-Bot sendet privaten Login-Link
    ↓
Nutzer klickt → offizielle TikTok-Login-Seite
    ↓
Nutzer autorisiert App
    ↓
TikTok → OAuth-Callback → /callback
    ↓
Token-Austausch, State-Validierung (CSRF)
    ↓
Account verschlüsselt in DB gespeichert
    ↓
Discord-Bot: "✅ TikTok-Account verbunden"
```

---

## Installation

### 1. Python installieren

Python 3.11+ wird benötigt: https://www.python.org/downloads/

### 2. Abhängigkeiten installieren

```bash
pip install -r requirements.txt
```

### 3. Environment Variables konfigurieren

Kopiere `.env.example` zu `.env` und fülle alle Werte aus:

```bash
copy .env.example .env
```

#### SECRET_KEY generieren:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Discord Bot einrichten

1. Gehe zu https://discord.com/developers/applications
2. Klicke **New Application**
3. Unter **Bot** → **Add Bot** → Token kopieren → in `.env` als `DISCORD_TOKEN` eintragen
4. Unter **OAuth2** → Client ID kopieren → `DISCORD_CLIENT_ID`
5. Bot auf deinen Server einladen:
   - OAuth2 → URL Generator
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Use Slash Commands`

---

## TikTok Developer App einrichten

1. Gehe zu https://developers.tiktok.com/
2. Erstelle eine neue App
3. Unter **Login Kit** aktivieren
4. **Client Key** und **Client Secret** kopieren → in `.env` eintragen
5. **Redirect URI** eintragen:
   - Lokal (Entwicklung): `http://localhost:8080/callback`
   - Produktion: `https://deine-domain.com/callback`

> **Wichtig:** Die `OAUTH_REDIRECT_URI` in der `.env` muss **exakt** mit der im TikTok Developer Portal eingetragenen URL übereinstimmen.

---

## Für Produktion: ngrok (lokales Testing)

Um TikTok OAuth lokal zu testen, braucht man eine öffentlich erreichbare URL:

```bash
# ngrok installieren: https://ngrok.com/download
ngrok http 8080
```

Die generierte URL (z.B. `https://abc123.ngrok-free.app`) als `OAUTH_REDIRECT_URI` eintragen:
```
OAUTH_REDIRECT_URI=https://abc123.ngrok-free.app/callback
```

Dieselbe URL auch im TikTok Developer Portal eintragen.

---

## Bot starten

```bash
# Windows (Doppelklick)
start.bat

# Oder direkt:
python main.py
```

---

## Slash Commands

| Command | Beschreibung |
|---|---|
| `/tiktok verbinden` | Startet den TikTok OAuth-Flow |
| `/tiktok status` | Zeigt den verbundenen TikTok-Account |
| `/tiktok trennen` | Hebt die Verknüpfung auf |

---

## Sicherheitsmerkmale

- OAuth `state` Parameter gegen CSRF-Angriffe
- Tokens **verschlüsselt** in der Datenbank (AES via Fernet)
- Tokens **niemals** in Logs, Nachrichten oder URLs
- Alle Secrets ausschließlich über Environment Variables
- Rate-Limiting auf allen OAuth-Endpunkten
- Alle Antworten des Bots sind `ephemeral` (nur für den Nutzer sichtbar)
- Keine Passwörter, Cookies oder Session-Tokens werden jemals abgefragt

---

## Environment Variables Übersicht

| Variable | Beschreibung | Beispiel |
|---|---|---|
| `DISCORD_TOKEN` | Discord Bot Token | `MTUy...` |
| `DISCORD_CLIENT_ID` | Discord App Client ID | `1234567890` |
| `TIKTOK_CLIENT_KEY` | TikTok App Client Key | `awja5...` |
| `TIKTOK_CLIENT_SECRET` | TikTok App Client Secret | `abc123...` |
| `OAUTH_REDIRECT_URI` | Vollständige Callback-URL | `https://domain.com/callback` |
| `SERVER_PORT` | Port des OAuth-Servers | `8080` |
| `SECRET_KEY` | Verschlüsselungsschlüssel (32 Byte hex) | `a1b2c3...` |
| `DATABASE_URL` | SQLAlchemy Datenbank-URL | `sqlite:///data/app.db` |
