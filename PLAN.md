# Parley: Plan

## Context

Push-to-Talk Tool (Parley), das von allen Geräten (Windows PC, Android Handy, Tablet) nutzbar ist. Die gesamte Verarbeitung (Transkription + LLM-Cleanup) läuft auf einem Linux-Heimserver mit NVIDIA GPU. Inspiration: Handy & Ghost Pepper, aber als Client-Server-Architektur.

---

## Architektur-Übersicht

```
┌─────────────────────────────────────────────────┐
│              Heimserver (Linux + NVIDIA GPU)      │
│                                                   │
│  ┌─────────────┐    ┌──────────────────────────┐ │
│  │ FastAPI      │    │ faster-whisper            │ │
│  │ REST API     │───►│ (Transkription, CUDA)    │ │
│  │              │    └──────────────────────────┘ │
│  │ POST         │    ┌──────────────────────────┐ │
│  │ /transcribe  │───►│ Ollama + LLM             │ │
│  │              │    │ (Text-Cleanup)            │ │
│  └──────┬───────┘    └──────────────────────────┘ │
│         │                                         │
│  ┌──────┴───────┐                                 │
│  │ Web-App      │  ◄── PWA (statische Dateien)    │
│  │ (Frontend)   │      wird vom selben Server     │
│  │              │      ausgeliefert               │
│  └──────────────┘                                 │
└─────────────────────────────────────────────────┘
         ▲              ▲              ▲
         │              │              │
    ┌────┴───┐    ┌────┴───┐    ┌────┴───┐
    │Windows │    │Android │    │Tablet  │
    │Browser │    │Browser │    │Browser │
    │ (PWA)  │    │ (PWA)  │    │ (PWA)  │
    └────────┘    └────────┘    └────────┘
```

---

## Komponenten

### 1. Server-Backend (Python + FastAPI)

**Warum FastAPI:** Schnell, async, einfach, guter Python-Ökosystem-Fit für ML.

**Endpunkte:**
- `POST /api/transcribe` — nimmt Audio-Datei (WebM/WAV) + `mode` Parameter, gibt JSON zurück:
  ```json
  {
    "raw_text": "Ähm, also ich wollte halt sagen, dass das Projekt...",
    "processed_text": "Das Projekt sollte bis nächste Woche fertig sein.",
    "mode": "rephrase",
    "language": "de",
    "duration_ms": 3200
  }
  ```
- `GET /api/health` — Server-Status + GPU-Info
- `GET /` — liefert die Web-App (PWA) aus

**Transkription:** `faster-whisper` (CTranslate2-basiert, CUDA-beschleunigt)
- Modell: `large-v3` für beste Qualität, `medium` als schnellere Alternative
- Automatische Spracherkennung

**LLM-Verarbeitung:** `Ollama` mit kleinem Modell (z.B. `qwen2.5:3b` oder `gemma2:2b`)

Drei Modi, per Toggle im Frontend umschaltbar:

| Modus | Was passiert | Beispiel-Prompt |
|-------|-------------|-----------------|
| **Raw** | Keine LLM-Verarbeitung, reines Whisper-Ergebnis | — |
| **Cleanup** | Füllwörter, Versprecher, Wiederholungen entfernen | "Bereinige diesen Text. Entferne Füllwörter und Versprecher. Behalte Inhalt und Sprache bei." |
| **Reformulieren** | Inhalt beibehalten, aber klarer/schöner formulieren | "Formuliere diesen Text klar und professionell um. Behalte den Inhalt und die Sprache bei, aber verbessere Ausdruck und Struktur." |

- Der Client schickt den gewünschten Modus als Parameter: `mode: "raw" | "cleanup" | "rephrase"`
- API-Response enthält immer `raw_text` + `processed_text` (bei "raw" sind beide identisch)
- Für den Reformulier-Modus eignet sich ein etwas größeres Modell besser (z.B. `qwen2.5:7b`), da die Aufgabe anspruchsvoller ist

**Dateien:**
```
server/
├── main.py              # FastAPI App + Endpoints
├── transcriber.py       # faster-whisper Wrapper
├── cleanup.py           # Ollama LLM-Verarbeitung (Cleanup + Reformulierung)
├── personalization.py   # Korrekturen, Glossar, Sprachprofil
├── config.py            # Konfiguration (Modell, GPU etc.)
├── requirements.txt     # Python Dependencies
├── Dockerfile           # Container für einfaches Deployment
└── docker-compose.yml   # Server + Ollama zusammen starten
```

### 2. Web-App / PWA (Frontend)

**Warum PWA:** Ein einziges Frontend für alle Geräte. Installierbar auf Android Home-Screen, funktioniert im Browser auf Windows/Tablet. Kein App-Store nötig.

**UI:** Minimalistisch — ein großer Button.
- **Halten** = Aufnahme läuft (visuelles Feedback: Pulsierender Kreis, Audio-Waveform)
- **Loslassen** = Audio wird an Server geschickt
- **Ergebnis** = Text erscheint, automatisch in Zwischenablage kopiert
- **Modus-Auswahl** als 3-fach Toggle: Raw / Cleanup / Reformulieren
- **Historie** der letzten Transkriptionen (localStorage)

**Technologie:** Vanilla HTML/CSS/JS oder leichtgewichtiges Framework
- `MediaRecorder API` für Audio-Aufnahme
- `Clipboard API` für automatisches Kopieren
- `Service Worker` für PWA-Installation + Offline-Shell
- Responsive Design (funktioniert auf Handy und Desktop gleich gut)

**Dateien:**
```
web/
├── index.html           # Single Page App
├── app.js               # Aufnahme-Logik, API-Calls
├── style.css            # UI + Animationen
├── sw.js                # Service Worker (PWA)
└── manifest.json        # PWA Manifest (Icon, Name etc.)
```

### 3. Personalisierung & Lernfähigkeit

Das System wird mit der Zeit besser, indem es sich an den Nutzer anpasst. Drei Mechanismen:

**A) Korrektur-Feedback (Few-Shot Learning)**
- Im Frontend kann der Nutzer das Ergebnis **editieren**, bevor er es kopiert
- Jede Korrektur wird als Paar gespeichert: `{original, korrigiert}`
- Beim nächsten Mal werden die letzten relevanten Korrekturen als **Few-Shot-Beispiele** in den LLM-Prompt eingefügt:
  ```
  Hier sind Beispiele wie der Nutzer Texte formuliert haben möchte:
  Vorher: "Das Projekt ist halt echt mega wichtig"
  Nachher: "Das Projekt hat hohe Priorität"
  ---
  Jetzt formuliere diesen Text im selben Stil um: ...
  ```
- Dadurch lernt das LLM den persönlichen Stil, ohne Fine-Tuning

**B) Persönliches Wörterbuch / Glossar**
- Whisper unterstützt einen `initial_prompt` Parameter — dort können häufig genutzte Fachbegriffe, Namen, Firmennamen etc. hinterlegt werden
- Das verbessert die Transkriptions-Genauigkeit für domänenspezifische Wörter
- Im Frontend: einfache Liste zum Pflegen ("Antigravity", "Supabase", "Mirko" etc.)
- Wird automatisch aus häufig korrigierten Wörtern befüllt

**C) Spracherkennung & Profil**
- Das System merkt sich, welche Sprachen der Nutzer spricht und wie oft
- Whisper erkennt die Sprache automatisch, aber mit einem Sprachprofil kann die Erkennung priorisiert werden
- z.B. "Nutzer spricht 80% Deutsch, 20% Englisch" → bei Unsicherheit wird Deutsch bevorzugt

**Datenspeicherung auf dem Server:**
```
data/
├── corrections.jsonl     # Korrektur-Paare (original → korrigiert)
├── glossary.json         # Persönliches Wörterbuch
├── language_stats.json   # Sprachnutzungs-Statistik
└── preferences.json      # Nutzerpräferenzen (bevorzugter Modus etc.)
```

**Wichtig:** Kein echtes Fine-Tuning nötig — Few-Shot-Prompting mit gespeicherten Korrekturen ist deutlich einfacher, braucht keine GPU-intensive Trainingsläufe und funktioniert sofort.

### 4. Android-Tastatur-Integration

**Ziel:** Immer wenn die Android-Tastatur aufpoppt, soll ein Mikrofon-Button verfügbar sein, der die Sprache an den Heimserver schickt und den Text direkt ins Textfeld einfügt.

**Ansatz: Custom Input Method (IME)**

Android erlaubt eigene Tastaturen als "Input Method Editor". Wir bauen keine vollständige Tastatur, sondern eine **minimale IME**, die nur den Parley-Button bereitstellt und neben der normalen Tastatur nutzbar ist.

```
┌────────────────────────────────┐
│  Beliebige App (WhatsApp etc.) │
│  ┌──────────────────────────┐  │
│  │ Textfeld                 │  │
│  └──────────────────────────┘  │
│                                │
│  ┌──────────────────────────┐  │
│  │  Normale Tastatur        │  │
│  │  (GBoard etc.)           │  │
│  │                          │  │
│  │  ┌────────────────────┐  │  │
│  │  │ 🎤 Parley         │  │  │
│  │  │    (Hold to Talk)  │  │  │
│  │  └────────────────────┘  │  │
│  └──────────────────────────┘  │
└────────────────────────────────┘
```

**Zwei Optionen:**

| Option | Beschreibung | Vorteil | Nachteil |
|--------|-------------|---------|----------|
| **A) Vollständige IME** | Eigene Tastatur-App (Kotlin/Java), die beim Wechsel eine Aufnahme-UI zeigt | Text wird direkt ins Feld eingefügt (`commitText()`) | Aufwändiger zu bauen, Nutzer muss Tastatur wechseln |
| **B) Accessibility Overlay** | Floating-Button der über jeder App schwebt, bei Tap/Hold Aufnahme startet | Funktioniert mit jeder Tastatur parallel, kein Wechsel nötig | Braucht Accessibility-Permission, etwas hacky |

**Empfehlung: Option B (Accessibility Overlay)** — einfacher zu bauen, funktioniert überall, kein Tastaturwechsel nötig.

**Ablauf:**
1. App läuft als Accessibility-Service im Hintergrund
2. Kleiner schwebender Mikrofon-Button am Bildschirmrand (wie ein Chat-Head)
3. Button halten → Audio aufnehmen → an Server schicken
4. Ergebnis wird automatisch ins aktuell fokussierte Textfeld eingefügt
5. Modus (Raw/Cleanup/Reformulieren) über Long-Press-Menü am Button wählbar

**Technologie:**
- Kotlin (Android-native) oder React Native
- `AccessibilityService` für Overlay + Text-Einfügung
- `MediaRecorder` für Audio-Aufnahme
- HTTP-Client für Server-Kommunikation
- Minimale App — nur Service + Settings-Screen

**Dateien:**
```
android/
├── app/src/main/
│   ├── java/.../
│   │   ├── SpeechOverlayService.kt   # Accessibility Service + Floating Button
│   │   ├── AudioRecorder.kt          # Audio-Aufnahme
│   │   ├── ApiClient.kt              # Server-Kommunikation
│   │   └── SettingsActivity.kt       # Server-URL, Modus-Auswahl
│   ├── res/
│   │   ├── layout/overlay_button.xml  # Floating Button Layout
│   │   └── xml/accessibility_config.xml
│   └── AndroidManifest.xml
├── build.gradle.kts
└── README.md
```

### 5. Desktop-Client (Windows Hotkey)

Tray-App mit **globalem Hotkey** — funktioniert in jedem Programm, ohne Browser öffnen zu müssen.

**Ablauf:**
1. App sitzt im System-Tray (minimalistisch, kein Fenster)
2. Konfigurierbare Tastenkombination halten (z.B. `Ctrl+Shift+Space`)
3. Während gehalten: Audio wird aufgenommen, visuelles Feedback (Tray-Icon pulsiert / kleines Overlay)
4. Loslassen: Audio → Server → Text wird direkt ins aktive Fenster getippt (simuliert Tastatureingabe)
5. Alternativ: Text in Zwischenablage + automatisch `Ctrl+V`

**Technologie:** Python mit:
- `pynput` — globaler Hotkey-Listener (funktioniert auch wenn App nicht im Fokus)
- `sounddevice` oder `pyaudio` — Audio-Aufnahme
- `httpx` — async HTTP-Request an den Server
- `pystray` — System-Tray Icon
- Optional: als `.exe` gepackt mit `PyInstaller` (kein Python-Install nötig)

**Features:**
- Hotkey konfigurierbar über Tray-Rechtsklick-Menü
- Modus-Auswahl (Raw / Cleanup / Reformulieren) im Tray-Menü
- Server-URL konfigurierbar
- Autostart mit Windows (optional)
- Kleines visuelles Feedback während der Aufnahme (Overlay oder Tray-Animation)

**Dateien:**
```
desktop/
├── main.py              # Tray-App + Hotkey-Listener
├── recorder.py          # Audio-Aufnahme
├── api_client.py        # Server-Kommunikation
├── text_inserter.py     # Text ins aktive Fenster einfügen
├── config.py            # Einstellungen (Hotkey, Server-URL, Modus)
├── requirements.txt
└── build.spec           # PyInstaller Config für .exe
```

---

## Deployment (Docker)

Alles läuft containerisiert auf dem Heimserver. Drei Container:

```yaml
# docker-compose.yml
services:

  # 1. Ollama — LLM für Cleanup/Reformulierung
  ollama:
    image: ollama/ollama
    restart: unless-stopped
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
    volumes:
      - ollama_data:/root/.ollama
    # Kein Port-Expose nötig, nur intern erreichbar

  # 2. Parley API — FastAPI + faster-whisper
  parley:
    build: ./server
    restart: unless-stopped
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - OLLAMA_URL=http://ollama:11434
      - WHISPER_MODEL=large-v3
      - LOG_LEVEL=info
    depends_on:
      - ollama
    volumes:
      - whisper_models:/models          # Whisper-Modelle persistent
      - ./data:/app/data                # Personalisierungsdaten persistent

  # 3. Nginx Reverse Proxy — HTTPS + statische Dateien (PWA)
  nginx:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "7800:80"
      - "7443:443"                      # HTTPS (nötig für Mikrofon-Zugriff)
    volumes:
      - ./web:/usr/share/nginx/html     # PWA Frontend
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf
      - ./nginx/certs:/etc/nginx/certs  # Self-signed oder Let's Encrypt
    depends_on:
      - parley

volumes:
  ollama_data:
  whisper_models:
```

**Warum Nginx davor?**
- Browser erlauben Mikrofon-Zugriff (`getUserMedia`) nur über **HTTPS** oder `localhost`
- Nginx terminiert HTTPS mit Self-Signed-Zertifikat (im Heimnetz reicht das)
- Liefert die PWA als statische Dateien aus (schnell)
- Proxy zu `/api/*` an den FastAPI-Container

**Nginx-Config:**
```
server/
├── ...
nginx/
├── nginx.conf           # Reverse Proxy Config
└── certs/               # Self-signed Zertifikate (generiert beim Setup)
    ├── cert.pem
    └── key.pem
```

**Setup-Script** (`setup.sh`):
```bash
#!/bin/bash
# 1. Self-signed Zertifikat generieren
# 2. Ollama Modell pullen (qwen2.5:7b)
# 3. docker-compose up -d
# 4. Healthcheck
```

**Zugriff:**
- Heimnetz: `https://server-ip:7443`
- Von unterwegs: Tailscale/WireGuard (optional)
- Android: URL öffnen → "Zum Startbildschirm hinzufügen" für PWA
- Android Overlay-App: Server-URL in Settings eintragen

---

## Umsetzungs-Reihenfolge

### Phase 1: Server + Docker (MVP)
1. FastAPI-Server mit `/api/transcribe` Endpoint
2. faster-whisper Integration (CUDA)
3. Dockerfile für den Server
4. docker-compose.yml (Server + Ollama + Nginx)
5. Nginx Reverse Proxy mit HTTPS (Self-Signed Cert)
6. Setup-Script (`setup.sh`)

### Phase 2: Web-App (PWA)
7. Web-App mit Hold-to-Talk Button
8. Clipboard-Integration
9. PWA (Service Worker, Manifest, Icons)
10. Modus-Auswahl (Raw / Cleanup / Reformulieren)
11. Audio-Level-Visualisierung

### Phase 3: LLM-Verarbeitung
12. Ollama Integration
13. Prompt-Templates für Cleanup und Reformulierung
14. Drei Modi im Backend verdrahten

### Phase 4: Personalisierung
15. Korrektur-Feedback im Frontend (Text editierbar vor dem Kopieren)
16. Korrekturen als Few-Shot-Beispiele in LLM-Prompt einbauen
17. Persönliches Wörterbuch (Glossar) für Whisper `initial_prompt`
18. Sprachprofil (automatische Statistik welche Sprachen genutzt werden)
19. Transkriptions-Historie im Frontend

### Phase 5: Android Overlay-App
20. Accessibility-Service mit Floating-Button
21. Audio-Aufnahme + Server-Kommunikation
22. Text-Einfügung ins fokussierte Feld
23. Settings-Screen (Server-URL, Modus)

### Phase 6: Desktop-Client (Windows Hotkey)
24. Windows Tray-App mit System-Tray Icon
25. Globaler Hotkey-Listener (konfigurierbar)
26. Audio-Aufnahme + Server-Kommunikation
27. Text-Einfügung ins aktive Fenster (Tastatur-Simulation)
28. PyInstaller Build für standalone `.exe`

---

## Verifizierung

### Docker & Server
1. `./setup.sh` auf dem Heimserver ausführen
2. `docker-compose up -d` — alle 3 Container laufen
3. `curl https://server-ip:7443/api/health` — Server antwortet mit GPU-Info

### PWA (Browser)
4. `https://server-ip:7443` im Browser öffnen (HTTPS-Warnung akzeptieren)
5. Mikrofon-Berechtigung erteilen
6. Button halten, etwas sagen, loslassen → Text erscheint
7. Clipboard funktioniert (Ctrl+V in anderem Fenster)
8. Auf Android: "Zum Startbildschirm hinzufügen" → App-Icon erscheint

### LLM-Modi
9. Raw-Modus: Text mit Füllwörtern kommt durch
10. Cleanup-Modus: Füllwörter werden entfernt
11. Reformulier-Modus: gleicher Inhalt, bessere Formulierung

### Personalisierung
12. Text editieren vor dem Kopieren → Korrektur wird gespeichert
13. Nächste Transkription prüfen: Stil wird übernommen
14. Glossar-Wörter hinzufügen → Whisper erkennt sie besser

### Android Overlay
15. App installieren, Accessibility-Permission erteilen
16. Floating-Button erscheint über jeder App
17. Button halten in WhatsApp/Browser → Text wird ins Feld eingefügt

### Desktop Hotkey
18. Tray-App starten, Icon erscheint im System-Tray
19. Hotkey halten in beliebigem Programm (z.B. VS Code, Browser, Word)
20. Text wird direkt ins aktive Fenster eingefügt
21. Modus über Tray-Rechtsklick umschaltbar
