import json
import logging
from pathlib import Path
from config import DATA_DIR, MAX_FEW_SHOT_EXAMPLES

logger = logging.getLogger(__name__)

CORRECTIONS_FILE = DATA_DIR / "corrections.jsonl"
GLOSSARY_FILE = DATA_DIR / "glossary.json"
LANGUAGE_STATS_FILE = DATA_DIR / "language_stats.json"
PREFERENCES_FILE = DATA_DIR / "preferences.json"


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# --- Corrections (Few-Shot Learning) ---

def save_correction(original: str, corrected: str):
    """Save a correction pair for few-shot learning."""
    if original.strip() == corrected.strip():
        return
    _ensure_data_dir()
    with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"original": original, "corrected": corrected}, ensure_ascii=False) + "\n")
    logger.info(f"Saved correction: '{original[:50]}...' -> '{corrected[:50]}...'")


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
