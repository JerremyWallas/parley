import json
import logging
import os
from pathlib import Path
from config import DATA_DIR, MAX_FEW_SHOT_EXAMPLES

logger = logging.getLogger(__name__)

CORRECTIONS_FILE = DATA_DIR / "corrections.jsonl"
GLOSSARY_FILE = DATA_DIR / "glossary.json"
LANGUAGE_STATS_FILE = DATA_DIR / "language_stats.json"
PREFERENCES_FILE = DATA_DIR / "preferences.json"
HISTORY_FILE = DATA_DIR / "history.jsonl"
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "200"))


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# --- Corrections (Few-Shot Learning) ---

MAX_CORRECTIONS = int(os.getenv("MAX_CORRECTIONS", "100"))


def save_correction(original: str, corrected: str):
    """Save a correction pair for few-shot learning. Keeps only the last MAX_CORRECTIONS entries."""
    if original.strip() == corrected.strip():
        return
    _ensure_data_dir()
    with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"original": original, "corrected": corrected}, ensure_ascii=False) + "\n")
    logger.info(f"Saved correction: '{original[:50]}...' -> '{corrected[:50]}...'")

    # Truncate to last MAX_CORRECTIONS entries
    _truncate_corrections()


def _truncate_corrections():
    """Keep only the last MAX_CORRECTIONS entries in the corrections file."""
    if not CORRECTIONS_FILE.exists():
        return
    with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > MAX_CORRECTIONS:
        with open(CORRECTIONS_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines[-MAX_CORRECTIONS:])
        logger.info(f"Truncated corrections to last {MAX_CORRECTIONS} entries")


def get_recent_corrections(n: int = MAX_FEW_SHOT_EXAMPLES) -> list[dict]:
    """Get the most recent correction pairs for few-shot prompting."""
    if not CORRECTIONS_FILE.exists():
        return []
    corrections = []
    with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                corrections.append(json.loads(line))
    return corrections[-n:]


# --- Glossary ---

def get_glossary() -> list[str]:
    """Get the personal glossary/dictionary for Whisper initial_prompt."""
    if not GLOSSARY_FILE.exists():
        return []
    with open(GLOSSARY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("words", [])


def save_glossary(words: list[str]):
    """Save the glossary."""
    _ensure_data_dir()
    with open(GLOSSARY_FILE, "w", encoding="utf-8") as f:
        json.dump({"words": words}, f, ensure_ascii=False, indent=2)


def add_glossary_word(word: str):
    """Add a word to the glossary."""
    words = get_glossary()
    if word not in words:
        words.append(word)
        save_glossary(words)


def remove_glossary_word(word: str):
    """Remove a word from the glossary."""
    words = get_glossary()
    if word in words:
        words.remove(word)
        save_glossary(words)


def build_initial_prompt() -> str | None:
    """Build whisper initial_prompt from glossary words."""
    words = get_glossary()
    if not words:
        return None
    return ", ".join(words)


# --- Language Stats ---

def update_language_stats(language: str):
    """Track language usage statistics."""
    _ensure_data_dir()
    stats = {}
    if LANGUAGE_STATS_FILE.exists():
        with open(LANGUAGE_STATS_FILE, "r", encoding="utf-8") as f:
            stats = json.load(f)

    counts = stats.get("counts", {})
    counts[language] = counts.get(language, 0) + 1
    total = sum(counts.values())
    percentages = {lang: round(c / total * 100, 1) for lang, c in counts.items()}

    stats = {"counts": counts, "percentages": percentages, "total": total}
    with open(LANGUAGE_STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def get_language_stats() -> dict:
    """Get language usage statistics."""
    if not LANGUAGE_STATS_FILE.exists():
        return {"counts": {}, "percentages": {}, "total": 0}
    with open(LANGUAGE_STATS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# --- Preferences ---

def get_preferences() -> dict:
    """Get user preferences."""
    if not PREFERENCES_FILE.exists():
        return {"default_mode": "raw"}
    with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_preferences(prefs: dict):
    """Save user preferences."""
    _ensure_data_dir()
    with open(PREFERENCES_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)


# --- History (server-side, synced across devices) ---

def save_history_entry(raw_text: str, processed_text: str, mode: str, language: str):
    """Append a transcription to the history. Truncates to MAX_HISTORY entries."""
    _ensure_data_dir()
    from datetime import datetime, timezone
    entry = {
        "raw_text": raw_text,
        "processed_text": processed_text,
        "mode": mode,
        "language": language,
        "time": datetime.now(timezone.utc).isoformat(),
    }
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _truncate_history()
    logger.info(f"Saved history entry: '{processed_text[:50]}...'")


def get_history(n: int = 50) -> list[dict]:
    """Get the most recent history entries."""
    if not HISTORY_FILE.exists():
        return []
    entries = []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return list(reversed(entries[-n:]))


def clear_history():
    """Delete all history entries."""
    if HISTORY_FILE.exists():
        HISTORY_FILE.unlink()
    logger.info("History cleared")


def _truncate_history():
    """Keep only the last MAX_HISTORY entries."""
    if not HISTORY_FILE.exists():
        return
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > MAX_HISTORY:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines[-MAX_HISTORY:])
        logger.info(f"Truncated history to last {MAX_HISTORY} entries")


# --- Presets ---

def _get_default_prompts():
    """Import DEFAULT_PROMPTS from cleanup (deferred to avoid circular imports)."""
    from cleanup import DEFAULT_PROMPTS
    return DEFAULT_PROMPTS


def _default_presets() -> list[dict]:
    """Return the two builtin presets using default prompt texts."""
    prompts = _get_default_prompts()
    return [
        {"id": "cleanup", "name": "Cleanup", "prompt": prompts["cleanup"], "builtin": True},
        {"id": "rephrase", "name": "Rephrase", "prompt": prompts["rephrase"], "builtin": True},
    ]


def get_presets() -> list[dict]:
    """Return the list of presets from preferences, initializing with defaults if missing."""
    prefs = get_preferences()
    if "presets" not in prefs:
        prefs["presets"] = _default_presets()
        save_preferences(prefs)
    return prefs["presets"]


def get_active_preset_id() -> str:
    """Return the active preset ID from preferences (default: 'cleanup')."""
    prefs = get_preferences()
    return prefs.get("active_preset", "cleanup")


def set_active_preset(preset_id: str):
    """Save the active preset ID to preferences."""
    prefs = get_preferences()
    prefs["active_preset"] = preset_id
    save_preferences(prefs)


def _generate_preset_id(name: str, existing_ids: set[str]) -> str:
    """Generate a unique ID from a name: lowercase, spaces to hyphens, deduplicate."""
    base = name.lower().replace(" ", "-")
    candidate = base
    counter = 2
    while candidate in existing_ids:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def add_preset(name: str, prompt: str) -> dict:
    """Create a new preset with an auto-generated unique ID. Returns the new preset."""
    prefs = get_preferences()
    presets = prefs.get("presets", _default_presets())
    existing_ids = {p["id"] for p in presets}
    preset_id = _generate_preset_id(name, existing_ids)
    preset = {"id": preset_id, "name": name, "prompt": prompt, "builtin": False}
    presets.append(preset)
    prefs["presets"] = presets
    save_preferences(prefs)
    logger.info(f"Added preset '{name}' with id '{preset_id}'")
    return preset


def update_preset(preset_id: str, name: str | None = None, prompt: str | None = None) -> dict | None:
    """Update name and/or prompt of an existing preset. Returns updated preset or None if not found."""
    prefs = get_preferences()
    presets = prefs.get("presets", _default_presets())
    for preset in presets:
        if preset["id"] == preset_id:
            if name is not None:
                preset["name"] = name
            if prompt is not None:
                preset["prompt"] = prompt
            prefs["presets"] = presets
            save_preferences(prefs)
            logger.info(f"Updated preset '{preset_id}'")
            return preset
    return None


def delete_preset(preset_id: str) -> bool:
    """Delete a preset. Refuses if builtin. Resets active preset if the deleted one was active. Returns success."""
    prefs = get_preferences()
    presets = prefs.get("presets", _default_presets())
    for preset in presets:
        if preset["id"] == preset_id:
            if preset.get("builtin"):
                logger.warning(f"Cannot delete builtin preset '{preset_id}'")
                return False
            presets.remove(preset)
            prefs["presets"] = presets
            if prefs.get("active_preset") == preset_id:
                prefs["active_preset"] = "cleanup"
                logger.info(f"Reset active preset to 'cleanup' after deleting '{preset_id}'")
            save_preferences(prefs)
            logger.info(f"Deleted preset '{preset_id}'")
            return True
    return False


def migrate_custom_prompts():
    """One-time migration: convert old custom_prompts dict to new presets format.

    Safe to call multiple times — no-op if presets already exist or no custom_prompts found.
    """
    prefs = get_preferences()

    # Already migrated or fresh install
    if "presets" in prefs:
        return

    custom_prompts = prefs.get("custom_prompts", {})
    presets = _default_presets()

    # Override builtin prompts with any custom ones
    for preset in presets:
        if preset["id"] in custom_prompts:
            preset["prompt"] = custom_prompts[preset["id"]]

    prefs["presets"] = presets

    # Clean up old key
    if "custom_prompts" in prefs:
        del prefs["custom_prompts"]

    save_preferences(prefs)
    logger.info("Migrated custom_prompts to presets format")
