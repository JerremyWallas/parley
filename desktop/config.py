import json
from pathlib import Path

CONFIG_FILE = Path.home() / ".config" / "parley" / "config.json"

DEFAULTS = {
    "server_url": "https://localhost:7443",
    "hotkey": "<ctrl>+<shift>+space",
    "mode": "raw",
    "auto_paste": True,
    "autostart": False,
    "send_mode": "off",  # "off", "auto", "voice"
    "send_listen_seconds": 10,
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
