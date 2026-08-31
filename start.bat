@echo off
title TikTok Discord OAuth Bot
echo.
echo  ================================================
echo   TikTok + Discord OAuth Login System
echo  ================================================
echo.

cd /d "%~dp0"

:: Prüfen ob .env existiert
if not exist ".env" (
    echo  FEHLER: .env Datei nicht gefunden!
    echo  Kopiere .env.example zu .env und trage deine Werte ein.
    echo.
    pause
    exit /b 1
)

:: Abhängigkeiten installieren falls nötig
pip show discord.py >nul 2>&1
if errorlevel 1 (
    echo  Installiere Abhängigkeiten...
    pip install -r requirements.txt
    echo.
)

echo  Starte Bot und OAuth-Server...
echo.
python main.py

echo.
echo  Anwendung beendet. Fenster schliesst in 5 Sekunden...
timeout /t 5
