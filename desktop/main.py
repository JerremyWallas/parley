import sys
import threading
import logging
import urllib3
from pynput import keyboard
import pystray

import config
import recorder
import api_client
import text_inserter
import settings_ui
from overlay import RecordingOverlay
from icon import create_tray_icon

# Suppress SSL warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- State ---
cfg = config.load()
audio_rec = recorder.AudioRecorder()
hold_parts = []
toggle_parts = []
stop_parts = []
pressed_keys = set()
tray_icon = None
overlay = RecordingOverlay()
last_result_text = ""
_active_mode = None  # None, "hold", or "toggle"
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


def _start_recording():
    """Start a new streaming recording session."""
    global _streaming_session
    logger.info("Starting streaming recording")
    update_icon(recording=True)

    _streaming_session = api_client.StreamingSession(
        server_url=cfg["server_url"],
        mode=cfg["mode"],
        on_segment=_on_segment,
        on_llm_token=_on_llm_token,
        on_done=_on_done,
        on_error=_on_error,
    )
    _streaming_session.start()
    audio_rec.start()


def _stop_recording():
    """Stop recording and send audio for processing."""
    global _streaming_session, _active_mode
    _active_mode = None
    logger.info("Stopping recording — sending audio via WebSocket")
    wav_bytes = audio_rec.stop()
    update_icon(processing=True)

    if _streaming_session and wav_bytes:
        _streaming_session.send_audio(wav_bytes)
        _streaming_session.finish()
    elif _streaming_session:
        _streaming_session.close()
        _streaming_session = None
        update_icon()


def on_key_press(key):
    global _active_mode
    key_str = key_to_str(key)
    pressed_keys.add(key_str)

    # Hold hotkey pressed — start hold recording
    if not audio_rec.is_recording and all(part in pressed_keys for part in hold_parts):
        _active_mode = "hold"
        _start_recording()
        return

    # Toggle hotkey pressed
    if all(part in pressed_keys for part in toggle_parts):
        if not audio_rec.is_recording:
            _active_mode = "toggle"
            _start_recording()
        elif _active_mode == "toggle":
            _stop_recording()
        return

    # Stop key pressed while in toggle mode
    if audio_rec.is_recording and _active_mode == "toggle":
        if key_str in stop_parts:
            _stop_recording()


def on_key_release(key):
    global _active_mode
    key_str = key_to_str(key)

    # Hold mode: stop when any hold hotkey part is released
    if audio_rec.is_recording and _active_mode == "hold" and key_str in hold_parts:
        _stop_recording()

    pressed_keys.discard(key_str)


def _on_segment(text: str):
    """Called when a Whisper segment arrives during transcription."""
    logger.info(f"Segment: {text}")


def _on_llm_token(token: str):
    """Called for each LLM token during streaming."""
    pass  # Desktop doesn't show streaming text, just waits for final


def _on_done(raw_text: str, processed_text: str):
    """Called when transcription + LLM processing is complete."""
    global _streaming_session, last_result_text
    _streaming_session = None
    text = processed_text or raw_text

    if text:
        last_result_text = text
        text_inserter.insert_text(text, auto_paste=cfg.get("auto_paste", True))
        logger.info(f"Inserted: {text[:80]}...")
        update_icon()
        # Update tray menu to show last result preview
        if tray_icon:
            tray_icon.menu = build_menu()
        # Save to server history
        _save_to_server_history(raw_text, text)
        handle_send_mode()
    else:
        logger.warning("Empty transcription result")
        update_icon()


def _save_to_server_history(raw_text: str, processed_text: str):
    """Save transcription to server history so it shows in the web app."""
    try:
        import httpx
        url = f"{cfg['server_url'].rstrip('/')}/api/history"
        httpx.post(url, json={
            "raw_text": raw_text,
            "processed_text": processed_text,
            "mode": cfg.get("mode", "raw"),
            "language": "",
        }, verify=False, timeout=5.0)
        logger.debug("Saved to server history")
    except Exception as e:
        logger.debug(f"Could not save to server history: {e}")


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


def update_icon(recording: bool = False, processing: bool = False, listening: bool = False):
    global tray_icon
    if tray_icon is None:
        return
    if recording:
        tray_icon.icon = create_tray_icon("#ef4444")  # red
        overlay.show("recording")
    elif processing:
        tray_icon.icon = create_tray_icon("#f59e0b")  # orange
        overlay.update_state("processing")
    elif listening:
        tray_icon.icon = create_tray_icon("#22c55e")  # green — waiting for "senden"
        overlay.update_state("listening")
    else:
        tray_icon.icon = create_tray_icon("#3b82f6")  # blue
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
        global cfg, hold_parts, toggle_parts, stop_parts
        cfg = new_cfg
        hold_parts = parse_hotkey(cfg["hotkey_hold"])
        toggle_parts = parse_hotkey(cfg["hotkey_toggle"])
        stop_parts = parse_hotkey(cfg["stop_key"])
        logger.info(f"Settings updated — Hold: {cfg['hotkey_hold']}, Toggle: {cfg['hotkey_toggle']}, Server: {cfg['server_url']}")
        # Rebuild tray menu to reflect new settings
        if tray_icon:
            tray_icon.menu = build_menu()

    threading.Thread(target=settings_ui.open_settings, args=(cfg, on_save), daemon=True).start()


def copy_last_result(icon, item):
    """Copy the last transcription result to clipboard."""
    if last_result_text:
        import pyperclip
        pyperclip.copy(last_result_text)
        logger.info(f"Copied last result to clipboard: {last_result_text[:50]}...")
    else:
        logger.info("No previous result to copy")


def paste_last_result(icon, item):
    """Paste the last transcription result into the active window."""
    if last_result_text:
        text_inserter.insert_text(last_result_text, auto_paste=cfg.get("auto_paste", True))
        logger.info(f"Re-pasted last result: {last_result_text[:50]}...")
    else:
        logger.info("No previous result to paste")


def quit_app(icon, item):
    icon.stop()


def build_menu():
    last_preview = (last_result_text[:30] + "...") if len(last_result_text) > 30 else last_result_text
    return pystray.Menu(
        pystray.MenuItem("Modus", pystray.Menu(
            pystray.MenuItem("Raw", set_mode("raw"), checked=get_mode_checked("raw")),
            pystray.MenuItem("Cleanup", set_mode("cleanup"), checked=get_mode_checked("cleanup")),
            pystray.MenuItem("Reformulieren", set_mode("rephrase"), checked=get_mode_checked("rephrase")),
        )),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            f"Letzte: {last_preview}" if last_result_text else "Kein letztes Ergebnis",
            pystray.Menu(
                pystray.MenuItem("In Zwischenablage kopieren", copy_last_result),
                pystray.MenuItem("Nochmal einfuegen", paste_last_result),
            ),
            enabled=bool(last_result_text),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Auto-Paste", toggle_auto_paste,
                         checked=lambda item: cfg.get("auto_paste", True)),
        pystray.MenuItem(f"Halten: {cfg.get('hotkey_hold', '')}", lambda *a: None, enabled=False),
        pystray.MenuItem(f"Freihand: {cfg.get('hotkey_toggle', '')}", lambda *a: None, enabled=False),
        pystray.MenuItem(f"Server: {cfg['server_url']}", lambda *a: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Einstellungen...", open_settings),
        pystray.MenuItem("Beenden", quit_app),
    )


def main():
    global tray_icon, hold_parts, toggle_parts, stop_parts

    logger.info("Parley Desktop Client")
    logger.info(f"Server: {cfg['server_url']}")
    logger.info(f"Mode: {cfg['mode']}")

    hold_parts = parse_hotkey(cfg["hotkey_hold"])
    toggle_parts = parse_hotkey(cfg["hotkey_toggle"])
    stop_parts = parse_hotkey(cfg["stop_key"])
    logger.info(f"Hold hotkey: {hold_parts}")
    logger.info(f"Toggle hotkey: {toggle_parts}")
    logger.info(f"Stop key: {stop_parts}")

    # Start keyboard listener
    listener = keyboard.Listener(on_press=on_key_press, on_release=on_key_release)
    listener.start()

    # Create and run tray icon
    tray_icon = pystray.Icon(
        "parley",
        create_tray_icon(),
        "Parley",
        menu=build_menu(),
    )

    logger.info("Tray icon ready. Hold the hotkey to record.")
    tray_icon.run()

    # Cleanup
    listener.stop()


def ensure_single_instance():
    """Ensure only one instance of Parley is running using a lock file."""
    import msvcrt
    lock_path = config.CONFIG_FILE.parent / "parley.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Open lock file and try exclusive lock
        lock_file = open(lock_path, "w")
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        return lock_file  # Keep reference alive so lock persists
    except (OSError, IOError):
        logger.error("Parley is already running. Exiting.")
        sys.exit(0)


if __name__ == "__main__":
    import os
    _lock = ensure_single_instance()
    main()
