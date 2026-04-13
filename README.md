# Parley

> *parley* (verb): to speak, to discuss — deine Stimme, dein Text.

Push-to-Talk mit LLM-Cleanup. Sprich einfach los — der Text landet automatisch in deiner Zwischenablage oder direkt im aktiven Fenster.

Läuft auf einem **Heimserver mit NVIDIA GPU** und ist von **jedem Gerät** (PC, Handy, Tablet) nutzbar.

## Features

- **Push-to-Talk** — Taste/Button halten, sprechen, loslassen
- **3 Modi** — Raw (unverändert), Cleanup (Füllwörter entfernt), Reformulieren (KI formuliert um)
- **Mehrsprachig** — Automatische Spracherkennung (Deutsch, Englisch, etc.)
- **Lernfähig** — Das System lernt deinen Stil durch Korrekturen (Few-Shot Learning)
- **Persönliches Wörterbuch** — Fachbegriffe die Whisper besser erkennen soll
- **Komplett lokal** — Keine Cloud, keine Daten werden rausgeschickt

## Architektur

```
Heimserver (Docker)          Clients
┌──────────────────┐         ┌─────────────┐
│ Nginx (HTTPS)    │◄────────│ Browser/PWA │
│ FastAPI + Whisper │         │ Desktop App │
│ Ollama (LLM)     │         │ Android App │
└──────────────────┘         └─────────────┘
```

## Schnellstart (Server)

### Voraussetzungen

- Linux-Server mit **NVIDIA GPU**
- [Docker](https://docs.docker.com/engine/install/) + [Docker Compose](https://docs.docker.com/compose/install/)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)

### Installation

```bash
git clone https://github.com/YOUR_USER/parley.git
cd parley

# Optional: Konfiguration anpassen
cp .env.example .env
nano .env

# Setup (generiert Zertifikate, baut Container, lädt Modelle)
chmod +x setup.sh
./setup.sh
```

Das war's. Öffne `https://SERVER-IP:7443` im Browser.

### Konfiguration (.env)

```bash
WHISPER_MODEL=large-v3     # oder: medium, small, tiny
OLLAMA_MODEL=qwen2.5:7b    # LLM für Cleanup/Reformulieren
HTTPS_PORT=7443            # HTTPS Port
HTTP_PORT=7800             # HTTP Port (redirect zu HTTPS)
```

## Clients

### Browser / PWA (alle Geräte)

Einfach `https://SERVER-IP:7443` öffnen. Auf Android: **Menü → Zum Startbildschirm hinzufügen** für eine App-ähnliche Erfahrung.

### Desktop (Windows)

Tray-App mit globalem Hotkey — funktioniert in jedem Programm.

```bash
cd desktop
pip install -r requirements.txt
python main.py
```

Standard-Hotkey: `Ctrl+Shift+Space` (halten zum Sprechen).

Konfiguration liegt in `~/.config/parley/config.json`:

```json
{
  "server_url": "https://192.168.1.100:7443",
  "hotkey": "<ctrl>+<shift>+space",
  "mode": "cleanup",
  "auto_paste": true
}
```

Als `.exe` bauen (kein Python nötig):

```bash
pip install pyinstaller
pyinstaller build.spec
```

### Android Overlay-App

Schwebender Mikrofon-Button über jeder App.

1. Projekt in Android Studio öffnen (`android/`)
2. Build → APK generieren
3. APK auf dem Handy installieren
4. Server-URL in den Einstellungen eintragen
5. Accessibility Service aktivieren

## API

```bash
# Transkription
curl -X POST https://localhost:7443/api/transcribe \
  -F "audio=@recording.wav" \
  -F "mode=cleanup" \
  --insecure

# Health Check
curl -k https://localhost:7443/api/health

# Glossar verwalten
curl -k https://localhost:7443/api/glossary
curl -k -X POST https://localhost:7443/api/glossary \
  -H "Content-Type: application/json" \
  -d '{"word": "Antigravity"}'

# Korrektur speichern
curl -k -X POST https://localhost:7443/api/correction \
  -H "Content-Type: application/json" \
  -d '{"original": "Das ist halt mega wichtig", "corrected": "Das hat hohe Priorität"}'
```

## Projektstruktur

```
parley/
├── server/              # FastAPI Backend
│   ├── main.py          # API Endpoints
│   ├── transcriber.py   # Whisper Integration
│   ├── cleanup.py       # Ollama LLM Processing
│   ├── personalization.py # Lernfähigkeit
│   └── Dockerfile
├── web/                 # PWA Frontend
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── sw.js
├── desktop/             # Windows Tray App
│   ├── main.py
│   └── requirements.txt
├── android/             # Android Overlay App
│   └── app/
├── nginx/               # Reverse Proxy
├── data/                # Personalisierungsdaten (persistent)
├── docker-compose.yml
├── setup.sh
└── README.md
```

## Security

Parley ist fuer den Einsatz im **lokalen Heimnetz** konzipiert. Folgende Punkte solltest du beachten:

- **Keine Authentifizierung:** Die API hat keinen Login, kein API-Key, keinen Auth-Mechanismus. Jeder im selben Netzwerk kann Anfragen senden. Wenn du den Server ins Internet stellst, nutze einen VPN-Tunnel (z.B. Tailscale oder WireGuard) oder baue eine Authentifizierung ein.
- **Self-Signed SSL:** HTTPS laeuft mit selbst-signierten Zertifikaten. Die Clients (Desktop, Android) deaktivieren dafuer bewusst die SSL-Verifikation. Das ist im Heimnetz akzeptabel, aber **kein Muster fuer Produktions-Apps**. Fuer den Einsatz ueber das Internet sollten richtige Zertifikate (z.B. Let's Encrypt) verwendet werden.
- **Komplett lokal:** Weder Audio noch Text verlassen dein Netzwerk. Alle Modelle (Whisper, Ollama) laufen auf deinem Server.

## Troubleshooting

**Browser zeigt Zertifikatswarnung**
Erwartet bei Self-Signed Certs. Klick auf "Erweitert" → "Trotzdem fortfahren".

**Mikrofon funktioniert nicht im Browser**
Browser brauchen HTTPS für Mikrofon-Zugriff. Stelle sicher, dass du über `https://` zugreifst.

**GPU wird nicht erkannt**
```bash
# Prüfen ob NVIDIA Runtime installiert ist
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

**Whisper-Modell Download dauert lange**
Beim ersten Start wird das Modell heruntergeladen (~3GB für large-v3). Fortschritt sichtbar mit:
```bash
docker compose logs -f parley
```
