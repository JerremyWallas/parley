import sys
import threading
import logging
import urllib3
from PIL import Image, ImageDraw
from pynput import keyboard
import pystray

import config
import recorder
import api_client
import text_inserter

# Suppress SSL warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- State ---
cfg = config.load()
audio_rec = recorder.AudioRecorder()
hotkey_parts = []
pressed_keys = set()
tray_icon = None


def parse_hotkey(hotkey_str: str) -> list[str]:
    """Parse hotkey string like '<ctrl>+<shift>+space' into parts."""
    return [part.strip() for part in hotkey_str.split("+")]


def key_to_str(key) -> str:
    """Convert a pynput key to a comparable string."""
    if isinstance(key, keyboard.Key):
        return f"<{key.name}>"
    elif hasattr(key, "char") and key.char:
        return key.char.lower()
    return str(key)


def on_key_press(key):
    key_str = key_to_str(key)
    pressed_keys.add(key_str)

    # Check if all hotkey parts are pressed
    if all(part in pressed_keys for part in hotkey_parts):
        if not audio_rec.is_recording:
            logger.info("Hotkey pressed — starting recording")
            update_icon(recording=True)
            audio_rec.start()


def on_key_release(key):
    key_str = key_to_str(key)

    if audio_rec.is_recording and key_str in hotkey_parts:
        logger.info("Hotkey released — stopping recording")
        update_icon(recording=False)
        audio_bytes = audio_rec.stop()

        if audio_bytes:
            # Process in background thread
            threading.Thread(target=process_audio, args=(audio_bytes,), daemon=True).start()

    pressed_keys.discard(key_str)


def process_audio(audio_bytes: bytes):
    """Send audio to server and insert result."""
    update_icon(processing=True)
    try:
        result = api_client.transcribe(cfg["server_url"], audio_bytes, cfg["mode"])
        text = result.get("processed_text") or result.get("raw_text", "")

        if text:
            text_inserter.insert_text(text, auto_paste=cfg.get("auto_paste", True))
            logger.info(f"Inserted: {text[:80]}...")
        else:
            logger.warning("Empty transcription result")
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
    finally:
        update_icon()


# --- Tray icon ---

def create_icon_image(color: str = "#3b82f6") -> Image.Image:
    """Create a simple circular tray icon."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, size - 4, size - 4], fill=color)
    # Simple mic shape
    cx, cy = size // 2, size // 2
    draw.rounded_rectangle([cx - 6, cy - 14, cx + 6, cy + 4], radius=6, fill="white")
    draw.arc([cx - 10, cy - 6, cx + 10, cy + 10], start=0, end=180, fill="white", width=2)
    draw.line([cx, cy + 10, cx, cy + 16], fill="white", width=2)
    return img


def update_icon(recording: bool = False, processing: bool = False):
    global tray_icon
    if tray_icon is None:
        return
    if recording:
        tray_icon.icon = create_icon_image("#ef4444")
    elif processing:
        tray_icon.icon = create_icon_image("#f59e0b")
    else:
        tray_icon.icon = create_icon_image("#3b82f6")


def set_mode(mode: str):
    def _set(icon, item):
        cfg["mode"] = mode
        config.save(cfg)
        logger.info(f"Mode set to: {mode}")
    return _set


def get_mode_checked(mode: str):
    def _check(item):
        return cfg.get("mode") == mode
    return _check


def toggle_auto_paste(icon, item):
    cfg["auto_paste"] = not cfg.get("auto_paste", True)
    config.save(cfg)
    logger.info(f"Auto-paste: {cfg['auto_paste']}")


def quit_app(icon, item):
    icon.stop()


def build_menu():
    return pystray.Menu(
        pystray.MenuItem("Modus", pystray.Menu(
            pystray.MenuItem("Raw", set_mode("raw"), checked=get_mode_checked("raw")),
            pystray.MenuItem("Cleanup", set_mode("cleanup"), checked=get_mode_checked("cleanup")),
            pystray.MenuItem("Reformulieren", set_mode("rephrase"), checked=get_mode_checked("rephrase")),
        )),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Auto-Paste", toggle_auto_paste,
                         checked=lambda item: cfg.get("auto_paste", True)),
        pystray.MenuItem(f"Hotkey: {cfg['hotkey']}", lambda *a: None, enabled=False),
        pystray.MenuItem(f"Server: {cfg['server_url']}", lambda *a: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Beenden", quit_app),
    )


def main():
    global tray_icon, hotkey_parts

    logger.info(f"Parley Desktop Client")
    logger.info(f"Server: {cfg['server_url']}")
    logger.info(f"Hotkey: {cfg['hotkey']}")
    logger.info(f"Mode: {cfg['mode']}")

    hotkey_parts = parse_hotkey(cfg["hotkey"])
    logger.info(f"Listening for hotkey: {hotkey_parts}")

    # Start keyboard listener
    listener = keyboard.Listener(on_press=on_key_press, on_release=on_key_release)
    listener.start()

    # Create and run tray icon
    tray_icon = pystray.Icon(
        "parley",
        create_icon_image(),
        "Parley",
        menu=build_menu(),
    )

    logger.info("Tray icon ready. Hold the hotkey to record.")
    tray_icon.run()

    # Cleanup
    listener.stop()


if __name__ == "__main__":
    main()
