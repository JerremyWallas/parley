# Whisper Model Download & Delete

**Date:** 2026-04-14
**Status:** Approved

---

## Summary

Add download and delete functionality for Whisper models in the Web-UI, matching the existing Ollama/LLM model management pattern.

---

## 1. Server Endpoints

### `POST /api/whisper-models/pull`

Downloads a Whisper model by triggering `faster-whisper`'s Hugging Face download.

**Request:**
```json
{"model": "medium"}
```

**Response:** SSE stream (`text/event-stream`)
```
data: {"status": "downloading", "message": "Downloading medium..."}
data: {"status": "success", "message": "Model medium ready"}
```

**Implementation:**
- Runs `WhisperModel(model_id, ...)` in a background thread to trigger the download
- Streams status updates via SSE (no percentage — Hugging Face doesn't expose progress)
- On success, model is cached in `MODEL_DIR` and ready for use
- On error, sends `{"status": "error", "message": "..."}`

### `POST /api/whisper-models/delete`

Deletes a cached Whisper model from disk.

**Request:**
```json
{"model": "medium"}
```

**Response:**
```json
{"status": "ok", "deleted": "medium"}
```

**Rules:**
- Cannot delete the currently active model (returns 400)
- Deletes the directory `MODEL_DIR/models--Systran--faster-whisper-{id}`
- If model is currently loaded in memory (`_model`), it is NOT affected (only the active model is loaded, and we block deleting that)

---

## 2. Web-UI Changes

### `loadWhisperModels()` in app.js

Adopt the same button logic as `loadModels()`:

| State | Button | Action |
|-------|--------|--------|
| Not installed, fits GPU | ⬇ Download | Calls `/api/whisper-models/pull`, shows pulsing progress bar |
| Installed, not active | 🗑 Delete | Calls `/api/whisper-models/delete` |
| Installed, active | (none) | Only VRAM badge shown |
| Too large for GPU | (none) | Card greyed out, "Passt nicht in GPU-Speicher" |

### Click-to-activate behavior
- Only works if model is installed
- If not installed: show hint "Modell muss erst heruntergeladen werden."

### Download animation
- No percentage progress (unlike Ollama which provides total/completed)
- Pulsing/indeterminate progress bar with text "Wird heruntergeladen..."
- On success: "Fertig!", then reload model list after 1s

---

## 3. Files Changed

- `server/main.py` — two new endpoints: `/api/whisper-models/pull`, `/api/whisper-models/delete`
- `server/transcriber.py` — new `download_model(model_id)` function that loads and immediately unloads
- `web/app.js` — updated `loadWhisperModels()` with download/delete buttons and `pullWhisperModel()` function
- `web/style.css` — pulsing progress bar animation (if not already covered by existing `.model-progress-fill` styles)
