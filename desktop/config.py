import json
from pathlib import Path

CONFIG_FILE = Path.home() / ".config" / "parley" / "config.json"

DEFAULTS = {
    "server_url": "https://localhost:7443",
    "hotkey_hold": "<ctrl>+<shift>+space",
    "hotkey_toggle": "<ctrl>+<alt>+space",
    "stop_key": "<esc>",
    "mode": "raw",
    "auto_paste": True,
    "autostart": False,
    "send_mode": "off",  # "off", "auto", "voice"
    "send_listen_seconds": 10,
    "preset_hotkeys": {},  # e.g. {"<ctrl>+1": "raw", "<ctrl>+2": "cleanup"}
}


def load() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        return {**DEFAULTS, **saved}
    return DEFAULTS.copy()


def save(cfg: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
