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
import settings_ui
from overlay import RecordingOverlay

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
overlay = RecordingOverlay()
_streaming_session = None


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
    global _streaming_session
    key_str = key_to_str(key)
    pressed_keys.add(key_str)

    # Check if all hotkey parts are pressed
    if all(part in pressed_keys for part in hotkey_parts):
        if not audio_rec.is_recording:
            logger.info("Hotkey pressed — starting streaming recording")
            update_icon(recording=True)

            # Open WebSocket session and start streaming audio
            _streaming_session = api_client.StreamingSession(
                server_url=cfg["server_url"],
                mode=cfg["mode"],
                on_segment=_on_segment,
                on_llm_token=_on_llm_token,
                on_done=_on_done,
                on_error=_on_error,
            )
            _streaming_session.start()

            # Start recording with chunk callback that streams to server
            audio_rec.start(
                on_chunk=lambda wav_bytes: _streaming_session.send_audio(wav_bytes),
                chunk_interval_ms=500,
            )


def on_key_release(key):
    global _streaming_session
    key_str = key_to_str(key)

    if audio_rec.is_recording and key_str in hotkey_parts:
        logger.info("Hotkey released — stopping recording, waiting for results")
        audio_rec.stop()
        update_icon(processing=True)

        # Tell server we're done recording
        if _streaming_session:
            _streaming_session.finish()

    pressed_keys.discard(key_str)


def _on_segment(text: str):
    """Called when a Whisper segment arrives during transcription."""
    logger.info(f"Segment: {text}")


def _on_llm_token(token: str):
    """Called for each LLM token during streaming."""
    pass  # Desktop doesn't show streaming text, just waits for final


def _on_done(raw_text: str, processed_text: str):
    """Called when transcription + LLM processing is complete."""
    global _streaming_session
    text = processed_text or raw_text

    if text:
        text_inserter.insert_text(text, auto_paste=cfg.get("auto_paste", True))
        logger.info(f"Inserted: {text[:80]}...")
        update_icon()
        handle_send_mode()
    else:
        logger.warning("Empty transcription result")
        update_icon()

    _streaming_session = None


def _on_error(message: str):
    """Called on WebSocket or server error — fall back to REST."""
    global _streaming_session
    logger.warning(f"Streaming failed ({message}), trying REST fallback...")
    _streaming_session = None

    # Fallback: use the complete recorded audio via REST
    update_icon(processing=True)
    try:
        # Re-record is not possible, but audio_rec.stop() already returned the full audio
        # The frames are gone, so just report the error
        logger.error("REST fallback not possible — audio already consumed by stream")
    finally:
        update_icon()


def handle_send_mode():
    """Handle auto-send or voice-command send after text insertion."""
    send_mode = cfg.get("send_mode", "off")

    if send_mode == "auto":
        logger.info("Auto-send: pressing Enter")
        text_inserter.press_enter()

    elif send_mode == "voice":
        max_seconds = cfg.get("send_listen_seconds", 10)
        logger.info(f"Voice-send: listening for up to {max_seconds}s...")
        update_icon(listening=True)

        send_triggers = ["senden", "sende", "send", "abschicken", "absenden", "enter"]
        detected = _voice_send_listen(max_seconds, send_triggers)

        if detected:
            logger.info("Send command detected — pressing Enter")
            text_inserter.press_enter()
        else:
            logger.info("Voice-send: no send command detected")

        update_icon()


def _voice_send_listen(max_seconds: float, triggers: list[str]) -> bool:
    """Listen for a voice command using a continuous audio stream.

    Uses a non-stop InputStream so no audio is ever missed. A callback
    writes every frame into a ring buffer. The main loop checks the
    buffer for speech (RMS above threshold). When speech ends, the
    segment is sent to the server for transcription.
    """
    import numpy as np
    import sounddevice as sd
    import io
    import wave
    import time
    import threading

    sample_rate = 16000
    block_size = 1600  # 100ms blocks (16000 * 0.1)
    silence_threshold = 500
    # How many silent blocks (100ms each) after speech before we transcribe
    silence_blocks_needed = 12  # 1.2 seconds of silence after speech
    min_speech_blocks = 5  # At least 0.5s of speech

    all_blocks = []
    speech_blocks = []
    silent_count = 0
    is_speaking = False
    found = False
    lock = threading.Lock()

    def audio_callback(indata, frames, time_info, status):
        nonlocal is_speaking, silent_count, found
        block = indata.copy()

        with lock:
            all_blocks.append(block)
            rms = np.sqrt(np.mean(block.astype(np.float32) ** 2))

            if rms > silence_threshold:
                speech_blocks.append(block)
                silent_count = 0
                is_speaking = True
            elif is_speaking:
                speech_blocks.append(block)
                silent_count += 1

    stream = sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=block_size,
        callback=audio_callback,
    )
    stream.start()

    start_time = time.time()
    try:
        while (time.time() - start_time) < max_seconds:
            time.sleep(0.05)  # Check every 50ms

            with lock:
                if is_speaking and silent_count >= silence_blocks_needed:
                    # Speech segment ended — transcribe it
                    if len(speech_blocks) >= min_speech_blocks:
                        audio_to_send = list(speech_blocks)
                        speech_blocks.clear()
                        silent_count = 0
                        is_speaking = False

                        heard = _transcribe_blocks(audio_to_send, sample_rate)
                        logger.info(f"Voice-send heard: '{heard}'")
                        if any(t in heard for t in triggers):
                            found = True
                            break
                    else:
                        # Too short, discard
                        speech_blocks.clear()
                        silent_count = 0
                        is_speaking = False

        # Timeout — check remaining speech
        if not found:
            with lock:
                if len(speech_blocks) >= min_speech_blocks:
                    heard = _transcribe_blocks(list(speech_blocks), sample_rate)
                    logger.info(f"Voice-send heard (timeout): '{heard}'")
                    if any(t in heard for t in triggers):
                        found = True
    finally:
        stream.stop()
        stream.close()

    return found


def _transcribe_blocks(blocks, sample_rate: int) -> str:
    """Convert audio blocks to WAV and transcribe via server."""
    import numpy as np
    import io
    import wave

    audio = np.concatenate(blocks, axis=0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())

    try:
        result = api_client.transcribe(cfg["server_url"], buf.getvalue(), "raw")
        return (result.get("raw_text") or "").lower().strip()
    except Exception as e:
        logger.error(f"Voice-send transcription failed: {e}")
        return ""


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


def update_icon(recording: bool = False, processing: bool = False, listening: bool = False):
    global tray_icon
    if tray_icon is None:
        return
    if recording:
        tray_icon.icon = create_icon_image("#ef4444")  # red
        overlay.show("recording")
    elif processing:
        tray_icon.icon = create_icon_image("#f59e0b")  # orange
        overlay.update_state("processing")
    elif listening:
        tray_icon.icon = create_icon_image("#22c55e")  # green — waiting for "senden"
        overlay.update_state("listening")
    else:
        tray_icon.icon = create_icon_image("#3b82f6")  # blue
        overlay.hide()


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


def open_settings(icon, item):
    """Open settings window and apply changes."""
    def on_save(new_cfg):
        global cfg, hotkey_parts
        cfg = new_cfg
        hotkey_parts = parse_hotkey(cfg["hotkey"])
        logger.info(f"Settings updated — Hotkey: {cfg['hotkey']}, Server: {cfg['server_url']}, Mode: {cfg['mode']}")
        # Rebuild tray menu to reflect new settings
        if tray_icon:
            tray_icon.menu = build_menu()

    threading.Thread(target=settings_ui.open_settings, args=(cfg, on_save), daemon=True).start()


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
        pystray.MenuItem("Einstellungen...", open_settings),
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
