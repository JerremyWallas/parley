# Tabbed Settings, Whisper Selection, Prompt Presets — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure settings into 3 tabs, add runtime Whisper model selection, and introduce a prompt preset system across Web and Desktop.

**Architecture:** Server-first approach — build APIs and data layer, then Web UI, then Desktop. Each task produces a working commit. Presets replace the current hardcoded mode system with a dynamic, user-configurable prompt library.

**Tech Stack:** Python/FastAPI (server), Vanilla JS (web), Python/tkinter (desktop)

**Spec:** `docs/superpowers/specs/2026-04-14-settings-tabs-presets-whisper-design.md`

---

## File Structure

### Server (modified)
- `server/personalization.py` — preset CRUD, migration from old custom_prompts
- `server/transcriber.py` — `set_model()`, `list_models()`, model info
- `server/cleanup.py` — load prompt from preset instead of hardcoded mode
- `server/main.py` — new API endpoints for whisper-models and presets

### Web (modified)
- `web/index.html` — tab navigation in settings, dynamic preset buttons
- `web/style.css` — tab bar, tab content, preset card styles
- `web/app.js` — tab logic, whisper model UI, preset CRUD UI, dynamic mode selector

### Desktop (modified)
- `desktop/main.py` — custom tray window, preset hotkeys, preset switching overlay
- `desktop/settings_ui.py` — (unchanged for now, presets managed via web)
- `desktop/config.py` — preset_hotkeys config

---

## Task 1: Server — Preset Data Layer

**Files:**
- Modify: `server/personalization.py`

- [ ] **Step 1: Add preset CRUD functions to personalization.py**

Add after the existing preferences section:

```python
# --- Presets ---

DEFAULT_PRESETS = [
    {
        "id": "cleanup",
        "name": "Cleanup",
        "prompt": DEFAULT_CLEANUP_PROMPT,
        "builtin": True,
    },
    {
        "id": "rephrase",
        "name": "Reformulieren",
        "prompt": DEFAULT_REPHRASE_PROMPT,
        "builtin": True,
    },
]


def get_presets() -> list[dict]:
    """Get all presets. Initializes defaults on first call."""
    prefs = get_preferences()
    if "presets" not in prefs:
        prefs["presets"] = [p.copy() for p in DEFAULT_PRESETS]
        save_preferences(prefs)
    return prefs["presets"]


def get_active_preset_id() -> str:
    prefs = get_preferences()
    return prefs.get("active_preset", "cleanup")


def set_active_preset(preset_id: str):
    prefs = get_preferences()
    prefs["active_preset"] = preset_id
    save_preferences(prefs)


def add_preset(name: str, prompt: str) -> dict:
    prefs = get_preferences()
    presets = prefs.get("presets", [])
    preset_id = name.lower().replace(" ", "-")
    # Ensure unique ID
    existing_ids = {p["id"] for p in presets}
    base_id = preset_id
    counter = 1
    while preset_id in existing_ids:
        preset_id = f"{base_id}-{counter}"
        counter += 1
    new_preset = {"id": preset_id, "name": name, "prompt": prompt}
    presets.append(new_preset)
    prefs["presets"] = presets
    save_preferences(prefs)
    return new_preset


def update_preset(preset_id: str, name: str = None, prompt: str = None):
    prefs = get_preferences()
    for p in prefs.get("presets", []):
        if p["id"] == preset_id:
            if name is not None:
                p["name"] = name
            if prompt is not None:
                p["prompt"] = prompt
            break
    save_preferences(prefs)


def delete_preset(preset_id: str) -> bool:
    prefs = get_preferences()
    presets = prefs.get("presets", [])
    for p in presets:
        if p["id"] == preset_id:
            if p.get("builtin"):
                return False
            presets.remove(p)
            # Reset active if deleted
            if prefs.get("active_preset") == preset_id:
                prefs["active_preset"] = "cleanup"
            save_preferences(prefs)
            return True
    return False


def migrate_custom_prompts():
    """Migrate old custom_prompts format to presets on first load."""
    prefs = get_preferences()
    if "presets" in prefs:
        return  # Already migrated
    custom = prefs.pop("custom_prompts", {})
    presets = [p.copy() for p in DEFAULT_PRESETS]
    # Override builtin prompts with custom ones
    for preset in presets:
        if preset["id"] in custom and custom[preset["id"]]:
            preset["prompt"] = custom[preset["id"]]
    prefs["presets"] = presets
    save_preferences(prefs)
```

Note: `DEFAULT_CLEANUP_PROMPT` and `DEFAULT_REPHRASE_PROMPT` will be imported from cleanup.py.

- [ ] **Step 2: Verify by running server**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build parley
```

Check logs for no import errors.

- [ ] **Step 3: Commit**

```bash
git add server/personalization.py
git commit -m "feat: add preset CRUD functions and migration to personalization.py"
```

---

## Task 2: Server — Preset API Endpoints

**Files:**
- Modify: `server/main.py`
- Modify: `server/cleanup.py`

- [ ] **Step 1: Add preset endpoints to main.py**

Replace the existing `/api/prompts` endpoints with:

```python
# --- Presets ---

@app.get("/api/presets")
async def list_presets():
    return {
        "presets": personalization.get_presets(),
        "active": personalization.get_active_preset_id(),
    }


@app.post("/api/presets")
async def create_preset(data: dict):
    name = data.get("name", "").strip()
    prompt = data.get("prompt", "").strip()
    if not name or not prompt:
        raise HTTPException(400, "Fields 'name' and 'prompt' are required.")
    preset = personalization.add_preset(name, prompt)
    return preset


@app.put("/api/presets/{preset_id}")
async def update_preset(preset_id: str, data: dict):
    personalization.update_preset(
        preset_id,
        name=data.get("name"),
        prompt=data.get("prompt"),
    )
    return {"status": "ok"}


@app.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str):
    if not personalization.delete_preset(preset_id):
        raise HTTPException(400, "Cannot delete builtin preset.")
    return {"status": "ok"}


@app.put("/api/presets/active")
async def set_active_preset(data: dict):
    preset_id = data.get("id", "").strip()
    if not preset_id:
        raise HTTPException(400, "Field 'id' is required.")
    personalization.set_active_preset(preset_id)
    return {"active": preset_id}
```

- [ ] **Step 2: Update cleanup.py to use presets**

Replace `_get_prompt(mode)` with:

```python
def _get_preset_prompt(preset_id: str) -> str:
    """Get prompt text for a preset. Falls back to defaults."""
    presets = personalization.get_presets()
    for p in presets:
        if p["id"] == preset_id:
            return p["prompt"]
    return DEFAULT_PROMPTS.get(preset_id, DEFAULT_PROMPTS["cleanup"])
```

Update `_build_prompt` to use `_get_preset_prompt(preset_id)` instead of `_get_prompt(mode)`.

- [ ] **Step 3: Update WebSocket handler to accept preset_id**

In the WebSocket stop handler in main.py, change:
```python
mode = data.get("mode", "raw")
```
to:
```python
mode = data.get("mode", "raw")
preset_id = data.get("preset", mode)  # backwards compat
```

Pass `preset_id` to cleanup functions instead of `mode`.

- [ ] **Step 4: Commit**

```bash
git add server/main.py server/cleanup.py
git commit -m "feat: preset API endpoints and preset-based prompt loading"
```

---

## Task 3: Server — Whisper Model API

**Files:**
- Modify: `server/transcriber.py`
- Modify: `server/main.py`

- [ ] **Step 1: Add model management to transcriber.py**

```python
AVAILABLE_WHISPER_MODELS = [
    {"id": "tiny", "name": "Tiny", "desc": "Ultra-schnell, Basisqualitaet", "vram": "~1 GB", "vram_mb": 1024, "quality": 1},
    {"id": "small", "name": "Small", "desc": "Schnell, gute Qualitaet", "vram": "~2 GB", "vram_mb": 2048, "quality": 2},
    {"id": "medium", "name": "Medium", "desc": "Ausgewogen, sehr gute Qualitaet", "vram": "~5 GB", "vram_mb": 5120, "quality": 3},
    {"id": "large-v3", "name": "Large V3", "desc": "Beste Qualitaet, langsamer", "vram": "~6 GB", "vram_mb": 6144, "quality": 4},
]


def set_model(model_name: str):
    """Unload current model and set new model name. Loads lazily on next request."""
    global _model
    import config
    config.WHISPER_MODEL = model_name
    if _model is not None:
        logger.info(f"Unloading Whisper model, switching to '{model_name}'")
        _model = None


def list_models(gpu_total_mb: int = 0) -> list[dict]:
    """List available Whisper models with install and GPU fit status."""
    import os
    models = []
    for m in AVAILABLE_WHISPER_MODELS:
        # Check if model is cached by looking for it in the model directory
        model_dir = str(config.MODEL_DIR)
        installed = os.path.exists(os.path.join(model_dir, f"models--Systran--faster-whisper-{m['id']}"))
        fits_gpu = gpu_total_mb >= m["vram_mb"] if gpu_total_mb > 0 else True
        models.append({**m, "installed": installed, "fits_gpu": fits_gpu})
    return models
```

- [ ] **Step 2: Add whisper-models endpoints to main.py**

```python
@app.get("/api/whisper-models")
async def get_whisper_models():
    prefs = personalization.get_preferences()
    active = prefs.get("whisper_model", config.WHISPER_MODEL)
    gpu_total_mb = 0
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            gpu_total_mb = int(result.stdout.strip())
    except Exception:
        pass
    return {
        "models": transcriber.list_models(gpu_total_mb),
        "active": active,
        "gpu_total_mb": gpu_total_mb,
    }


@app.put("/api/whisper-models")
async def set_whisper_model(data: dict):
    model_id = data.get("model", "").strip()
    if not model_id:
        raise HTTPException(400, "Field 'model' is required.")
    prefs = personalization.get_preferences()
    prefs["whisper_model"] = model_id
    personalization.save_preferences(prefs)
    transcriber.set_model(model_id)
    return {"active": model_id}
```

- [ ] **Step 3: Commit**

```bash
git add server/transcriber.py server/main.py
git commit -m "feat: Whisper model selection API with runtime switching"
```

---

## Task 4: Web — Tab Navigation in Settings

**Files:**
- Modify: `web/index.html`
- Modify: `web/style.css`
- Modify: `web/app.js`

- [ ] **Step 1: Restructure settings modal HTML with tabs**

Replace the settings modal content with a tab bar and three tab panels. Each existing settings section moves into its respective tab.

- [ ] **Step 2: Add tab CSS**

Tab bar with horizontal buttons, active tab accent-colored, tab panels with `display:none` / `display:block` switching.

- [ ] **Step 3: Add tab switching JS**

Click handler on tab buttons toggles active class and shows/hides tab panels. `loadSettings()` loads data for the active tab.

- [ ] **Step 4: Verify in browser**

Open `https://server-ip:7443`, click settings gear, verify three tabs work and content is correctly distributed.

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/style.css web/app.js
git commit -m "feat: tabbed settings modal (Allgemein, Transkription, Textverarbeitung)"
```

---

## Task 5: Web — Whisper Model Selector in Transkription Tab

**Files:**
- Modify: `web/app.js`
- Modify: `web/index.html`

- [ ] **Step 1: Add Whisper model list to Transkription tab**

Reuse the existing `loadModels()` pattern — same card design, VRAM badge, download hint, greyed out for oversized models. New function `loadWhisperModels()` calling `GET /api/whisper-models`.

- [ ] **Step 2: Add switch handler**

Click on a Whisper model card calls `PUT /api/whisper-models` and shows "Modell wird beim naechsten Gebrauch geladen" hint.

- [ ] **Step 3: Verify**

Switch Whisper model in UI, then do a transcription — first one should take longer (model loading), subsequent ones should be fast.

- [ ] **Step 4: Commit**

```bash
git add web/app.js web/index.html
git commit -m "feat: Whisper model selector in Transkription settings tab"
```

---

## Task 6: Web — Preset Management in Textverarbeitung Tab

**Files:**
- Modify: `web/app.js`
- Modify: `web/index.html`
- Modify: `web/style.css`

- [ ] **Step 1: Add preset list UI in Textverarbeitung tab**

Cards showing each preset: name, prompt preview (80 chars), edit button, delete button (hidden for builtins). Active preset has accent border.

- [ ] **Step 2: Add create preset form**

"Neues Preset" button expands an inline form: name input + prompt textarea + save button.

- [ ] **Step 3: Add edit/delete handlers**

Edit opens the preset's prompt in an inline textarea. Delete calls `DELETE /api/presets/{id}` with confirmation.

- [ ] **Step 4: Commit**

```bash
git add web/app.js web/index.html web/style.css
git commit -m "feat: preset CRUD UI in Textverarbeitung settings tab"
```

---

## Task 7: Web — Dynamic Mode Selector from Presets

**Files:**
- Modify: `web/app.js`
- Modify: `web/index.html`

- [ ] **Step 1: Replace hardcoded mode buttons with dynamic preset buttons**

On page load, fetch `GET /api/presets` and generate `Raw` button + one button per preset. Active preset highlighted.

- [ ] **Step 2: Update recording to send preset_id**

When sending the stop signal via WebSocket, include `preset` field instead of `mode`. Map "raw" to no processing.

- [ ] **Step 3: Remove old prompt editor**

The prompt editor below the mode selector is no longer needed — prompts are now managed in the Textverarbeitung tab.

- [ ] **Step 4: Verify full flow**

Create a custom preset "WhatsApp", select it, record audio, verify the prompt is used.

- [ ] **Step 5: Commit**

```bash
git add web/app.js web/index.html
git commit -m "feat: dynamic mode selector from presets, remove old prompt editor"
```

---

## Task 8: Desktop — Preset Switching via Tray and Hotkeys

**Files:**
- Modify: `desktop/main.py`
- Modify: `desktop/config.py`

- [ ] **Step 1: Fetch presets from server on startup**

Add `_load_presets()` that calls `GET /api/presets` via httpx and caches locally.

- [ ] **Step 2: Replace mode switching with preset switching in tray menu**

Instead of hardcoded Raw/Cleanup/Reformulieren, dynamically generate menu items from fetched presets.

- [ ] **Step 3: Add preset hotkeys**

Add `preset_hotkeys` to config defaults. In the keyboard listener, detect preset hotkey presses and switch active preset + show overlay notification.

- [ ] **Step 4: Send preset_id in WebSocket stop signal**

Update `_streaming_session.finish()` to send `preset` instead of `mode`.

- [ ] **Step 5: Build .exe and verify**

```bash
cd desktop && python -m PyInstaller build.spec --noconfirm
```

Test: switch preset via tray, record, verify correct prompt is used.

- [ ] **Step 6: Commit**

```bash
git add desktop/main.py desktop/config.py
git commit -m "feat: desktop preset switching via tray menu and hotkeys"
```

---

## Task 9: Desktop — Custom Tray Window

**Files:**
- Modify: `desktop/main.py`

- [ ] **Step 1: Replace pystray context menu with custom tkinter window**

Create a `TrayWindow` class: dark themed, auto-sizing, always-on-top, closes on Escape or click outside. Shows: preset buttons, last transcription with copy/paste, hotkey info, settings + quit buttons.

- [ ] **Step 2: Wire tray icon click to open/close TrayWindow**

Left-click toggles the window. Right-click still works as fallback.

- [ ] **Step 3: Build .exe and verify**

Test: click tray icon, verify window appears with correct content, switch presets, close window.

- [ ] **Step 4: Commit**

```bash
git add desktop/main.py
git commit -m "feat: modern custom tray window replacing OS context menu"
```

---

## Task 10: Migration and Final Integration

**Files:**
- Modify: `server/personalization.py`
- Modify: `server/main.py`

- [ ] **Step 1: Add migration call on server startup**

In the FastAPI lifespan, call `personalization.migrate_custom_prompts()` to convert old format.

- [ ] **Step 2: Remove old /api/prompts endpoints**

Delete the old GET/PUT `/api/prompts` endpoints (replaced by `/api/presets`).

- [ ] **Step 3: End-to-end test**

1. Web: open settings → 3 tabs visible, switch between them
2. Transkription tab: see Whisper models, switch model
3. Textverarbeitung tab: see presets, create new preset, edit, delete
4. Main page: preset buttons appear dynamically
5. Desktop: tray shows presets, hotkey switching works
6. Record audio with custom preset — verify correct prompt used

- [ ] **Step 4: Commit and push**

```bash
git add -A
git commit -m "feat: complete tabbed settings, Whisper selection, and preset system"
git push origin master
```
