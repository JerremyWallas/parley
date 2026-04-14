# Parley: Tabbed Settings, Whisper Model Selection, Prompt Presets

**Date:** 2026-04-14
**Status:** Approved

---

## Summary

Restructure the Parley settings UI into three tabs, add runtime Whisper model selection, and introduce a prompt preset system that works across Web and Desktop clients.

---

## 1. Web-UI: Tabbed Settings

Replace the current single-scroll settings modal with three tabs.

### Tab "Allgemein"
- Server connection status (green/red dot)
- GPU name, VRAM usage (used/total GB + percentage)
- Whisper model (read-only display, configured in Transkription tab)
- LLM model (read-only display, configured in Textverarbeitung tab)
- Sprachstatistik (language usage percentages)
- Server-URL setting with info tooltip

### Tab "Transkription"
- Whisper model selector — same card-based design as LLM models
- Available models: tiny (~1 GB), small (~2 GB), medium (~5 GB), large-v3 (~6 GB)
- Each model shows: name, VRAM requirement, quality description
- Download button for models not yet cached
- Models that exceed GPU VRAM are greyed out with "Passt nicht in GPU-Speicher"
- Switching triggers server-side model reload (10-30s, no container restart)

### Tab "Textverarbeitung"
- LLM model selector (existing, unchanged)
- Glossar management (existing, unchanged)
- **Prompt Presets section** (new — see section 3)

### Tab Navigation
- Horizontal tab bar at top of settings modal
- Active tab highlighted with accent color
- Content area scrollable per tab
- Tabs: "Allgemein", "Transkription", "Textverarbeitung"

---

## 2. Whisper Model Selection

### Server API

**New endpoint: `GET /api/whisper-models`**
```json
{
  "models": [
    {"id": "tiny", "name": "Tiny", "desc": "Ultra-schnell, Basisqualitaet", "vram": "~1 GB", "vram_mb": 1024, "quality": 1, "installed": true, "fits_gpu": true},
    {"id": "small", "name": "Small", "desc": "Schnell, gute Qualitaet", "vram": "~2 GB", "vram_mb": 2048, "quality": 2, "installed": false, "fits_gpu": true},
    {"id": "medium", "name": "Medium", "desc": "Ausgewogen, sehr gute Qualitaet", "vram": "~5 GB", "vram_mb": 5120, "quality": 3, "installed": false, "fits_gpu": true},
    {"id": "large-v3", "name": "Large V3", "desc": "Beste Qualitaet, langsamer", "vram": "~6 GB", "vram_mb": 6144, "quality": 4, "installed": true, "fits_gpu": true}
  ],
  "active": "large-v3",
  "gpu_total_mb": 6144
}
```

**New endpoint: `PUT /api/whisper-models`**
```json
{"model": "medium"}
```
- Saves selection to preferences
- Triggers model reload in transcriber.py (unloads current model, loads new one)
- Returns immediately, model loads in background

### Server Implementation (transcriber.py)
- New `set_model(model_name)` function
- Unloads current model from VRAM (`_model = None`)
- New model loads lazily on next transcription request (no background thread needed)
- First transcription after switch takes 10-30s longer (model loading)

### Whisper Model Download
- Whisper models are downloaded automatically by faster-whisper on first use
- No explicit pull needed (unlike Ollama)
- Progress not easily trackable — show "Modell wird beim naechsten Gebrauch heruntergeladen" hint

---

## 3. Prompt Presets

### Data Model (server-side, preferences.json)
```json
{
  "presets": [
    {"id": "cleanup", "name": "Cleanup", "prompt": "Bereinige den folgenden...", "builtin": true},
    {"id": "rephrase", "name": "Reformulieren", "prompt": "Du bist ein Schreibassistent...", "builtin": true},
    {"id": "whatsapp", "name": "WhatsApp", "prompt": "Schreibe als lockere WhatsApp-Nachricht..."},
    {"id": "email", "name": "E-Mail", "prompt": "Formuliere als professionelle E-Mail..."}
  ],
  "active_preset": "cleanup"
}
```

### Server API

**`GET /api/presets`** — list all presets with active marker

**`POST /api/presets`** — create new preset
```json
{"name": "WhatsApp", "prompt": "Schreibe als lockere Nachricht..."}
```

**`PUT /api/presets/{id}`** — update preset name/prompt

**`DELETE /api/presets/{id}`** — delete preset (builtin presets cannot be deleted)

**`PUT /api/presets/active`** — set active preset
```json
{"id": "whatsapp"}
```

### Mode Selector (Main Page)
- Current: `Raw | Cleanup | Reformulieren` (hardcoded buttons)
- New: `Raw` + dynamically generated buttons from presets
- Active preset highlighted
- If more presets than fit in one row: horizontal scroll or overflow menu

### Textverarbeitung Tab (Settings)
- List of all presets as cards
- Each card shows: name, prompt preview (first 80 chars), edit/delete buttons
- Builtin presets (Cleanup, Reformulieren) show edit but no delete
- "Neues Preset" button at bottom opens inline form: name + prompt textarea + save
- Active preset marked with accent border

### cleanup.py Integration
- `_get_prompt(mode)` replaced by `_get_preset_prompt(preset_id)`
- Reads from presets list in preferences
- Falls back to DEFAULT_PROMPTS if preset not found
- API transcribe endpoint accepts `preset_id` instead of `mode` (with backwards compat for "raw", "cleanup", "rephrase")

---

## 4. Desktop App

### Tray: Custom Window Instead of System Context Menu
- Click on tray icon opens a custom tkinter window (not OS context menu)
- Dark theme matching settings UI
- Auto-sizes to content (no scrolling needed)
- Always-on-top, closes on click outside or Escape

### Tray Window Layout
```
┌──────────────────────────────────┐
│  Parley                    [⚙] [✕]│
├──────────────────────────────────┤
│                                    │
│  Presets                           │
│  [Raw] [Cleanup] [WhatsApp] [E-Mail]│
│                                    │
│  Letzte Transkription              │
│  "Das Projekt hat hohe Prio..."    │
│  [Kopieren] [Nochmal einfuegen]    │
│                                    │
│  Halten: Ctrl+Shift                │
│  Freihand: Ctrl+Alt+Space          │
│                                    │
│              [Beenden]             │
└──────────────────────────────────┘
```

### Preset Hotkeys
- Configurable in settings: assign keyboard shortcuts to presets
- E.g. Ctrl+1 = Raw, Ctrl+2 = Cleanup, Ctrl+3 = WhatsApp
- Switching shows brief overlay notification: "WhatsApp" (fades after 1s)
- Optional: stored in config.json as `"preset_hotkeys": {"ctrl+1": "raw", "ctrl+2": "cleanup"}`

---

## 5. Error Handling

- Server not reachable: popup with VPN/Tailscale hint (already implemented)
- Whisper model reload failure: show error in Transkription tab
- Preset save failure: show error inline
- WebSocket disconnect during recording: show overlay notification + try REST fallback

---

## 6. Files Changed

### Server
- `main.py` — new endpoints: `/api/whisper-models`, `/api/presets/*`
- `transcriber.py` — `reload_model()` function, model listing
- `cleanup.py` — preset-based prompt loading instead of mode-based
- `personalization.py` — preset CRUD operations

### Web
- `index.html` — tab navigation in settings modal, preset buttons on main page
- `style.css` — tab styles, preset card styles
- `app.js` — tab switching logic, whisper model selector, preset management, dynamic mode buttons

### Desktop
- `main.py` — custom tray window, preset hotkeys, preset switching
- `settings_ui.py` — preset configuration section
- `config.py` — preset hotkey defaults

---

## 7. Migration

- Existing `custom_prompts` in preferences migrated to presets format on first load
- Existing `mode` setting mapped to nearest preset
- No breaking changes for users who don't use presets (Raw/Cleanup/Reformulieren still work)
