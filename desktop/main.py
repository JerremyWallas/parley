import ctypes
import json
import sys
import threading
import logging
import time
import tkinter as tk
import urllib3
import httpx
from pathlib import Path
from pynput import keyboard
import pystray

import config
import recorder
import api_client
import text_inserter
import settings_ui
from overlay import RecordingOverlay
from icon import create_tray_icon
from tray_window import TrayWindow

# Enable DPI awareness ONCE before any tkinter window is created
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Persistent hidden Tk root — created lazily, lives for the entire app lifetime.
# All windows use _get_tk_root() which returns Toplevel if root exists, or creates root.
_tk_root = None


def _get_tk_root():
    """Get or create the persistent Tk root. Returns a Toplevel window."""
    global _tk_root
    if _tk_root is None or not _tk_root.winfo_exists():
        _tk_root = tk.Tk()
        _tk_root.withdraw()
    return _tk_root

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
tray_window = TrayWindow()
last_result_text = ""
_active_mode = None  # None, "hold", or "toggle"
_streaming_session = None
_cached_presets = []  # list of preset dicts from server
_active_preset_id = "raw"  # currently active preset id
_server_connected = False
preset_hotkey_parts = {}  # {"<ctrl>+1": (["<ctrl>", "1"], "raw"), ...}
_pending_audio_dir = Path.home() / ".config" / "parley" / "pending_audio"
_pending_wav_bytes = None  # WAV bytes of last recording (kept for retry)
_pending_mode = None  # mode/preset used for last recording
_retry_in_progress = False


def _save_pending_audio(wav_bytes: bytes, mode: str):
    """Save audio to disk so it survives crashes and can be retried."""
    global _pending_wav_bytes, _pending_mode
    _pending_wav_bytes = wav_bytes
    _pending_mode = mode
    try:
        _pending_audio_dir.mkdir(parents=True, exist_ok=True)
        (_pending_audio_dir / "recording.wav").write_bytes(wav_bytes)
        (_pending_audio_dir / "recording.json").write_text(
            json.dumps({"mode": mode, "timestamp": time.time()}), encoding="utf-8"
        )
        logger.debug("Pending audio saved to disk")
    except Exception as e:
        logger.warning(f"Could not save pending audio: {e}")


def _cleanup_pending_audio():
    """Remove pending audio after successful transcription."""
    global _pending_wav_bytes, _pending_mode
    _pending_wav_bytes = None
    _pending_mode = None
    try:
        wav = _pending_audio_dir / "recording.wav"
        meta = _pending_audio_dir / "recording.json"
        if wav.exists():
            wav.unlink()
        if meta.exists():
            meta.unlink()
    except Exception as e:
        logger.debug(f"Pending audio cleanup: {e}")


def _cleanup_stale_pending_audio():
    """Remove pending audio files older than 1 hour (startup cleanup)."""
    try:
        meta = _pending_audio_dir / "recording.json"
        if meta.exists():
            data = json.loads(meta.read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) > 3600:
                _cleanup_pending_audio()
                logger.info("Cleaned up stale pending audio")
    except Exception:
        pass


def _check_server_connection():
    """Check if server is reachable and update tray icon."""
    global _server_connected
    try:
        url = f"{cfg['server_url'].rstrip('/')}/api/health"
        resp = httpx.get(url, verify=False, timeout=3.0)
        resp.raise_for_status()
        was_connected = _server_connected
        _server_connected = True
        if not was_connected:
            logger.info("Server connected")
            _refresh_tray_icon()
    except Exception:
        was_connected = _server_connected
        _server_connected = False
        if was_connected:
            logger.warning("Server disconnected")
            _refresh_tray_icon()


def _refresh_tray_icon():
    """Update tray icon to reflect current connection status."""
    if tray_icon:
        tray_icon.icon = create_tray_icon("#3b82f6", connected=_server_connected)


def _server_health_loop():
    """Periodically check server connection in background."""
    import time
    while True:
        _check_server_connection()
        time.sleep(30)


def _fetch_presets() -> list[dict]:
    """Fetch presets from server and cache them. Returns list of preset dicts."""
    global _cached_presets, _active_preset_id
    try:
        url = f"{cfg['server_url'].rstrip('/')}/api/presets"
        resp = httpx.get(url, verify=False, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        _cached_presets = data.get("presets", [])
        _active_preset_id = data.get("active", "raw")
        # Sync mode in cfg
        cfg["mode"] = _active_preset_id
        logger.info(f"Fetched {len(_cached_presets)} presets, active: {_active_preset_id}")
        return _cached_presets
    except Exception as e:
        logger.warning(f"Could not fetch presets: {e}")
        return []


def _set_active_preset(preset_id: str):
    """Set the active preset on the server and update local state."""
    global _active_preset_id
    try:
        url = f"{cfg['server_url'].rstrip('/')}/api/presets/active"
        resp = httpx.put(url, json={"id": preset_id}, verify=False, timeout=5.0)
        resp.raise_for_status()
        _active_preset_id = preset_id
        cfg["mode"] = preset_id
        logger.info(f"Active preset set to: {preset_id}")
    except Exception as e:
        logger.warning(f"Could not set active preset: {e}")
        # Still update locally even if server call fails
        _active_preset_id = preset_id
        cfg["mode"] = preset_id


def _get_active_preset_name() -> str:
    """Get the display name of the currently active preset."""
    if _active_preset_id == "raw":
        return "Raw"
    for p in _cached_presets:
        if p.get("id") == _active_preset_id:
            return p.get("name", _active_preset_id)
    return _active_preset_id


def _get_preset_name(preset_id: str) -> str:
    """Get the display name for a preset by id."""
    if preset_id == "raw":
        return "Raw"
    for p in _cached_presets:
        if p.get("id") == preset_id:
            return p.get("name", preset_id)
    return preset_id


def _switch_preset_with_notification(preset_id: str):
    """Switch to a preset and show a brief overlay notification."""
    _set_active_preset(preset_id)
    name = _get_preset_name(preset_id)
    logger.info(f"Preset switched to: {name}")
    # Show brief overlay notification
    overlay.show_notification(f"Preset: {name}")
    # Rebuild tray menu to reflect the change
    if tray_icon:
        tray_icon.menu = build_menu()


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
        mode=_active_preset_id,
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
        # Save audio BEFORE sending so it survives network failures
        _save_pending_audio(wav_bytes, _active_preset_id)
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

    # If currently recording in toggle mode: any of these stops it
    if audio_rec.is_recording and _active_mode == "toggle":
        # Stop key
        if key_str in stop_parts:
            _stop_recording()
            return
        # Any toggle hotkey part pressed again (user doesn't need to press all at once)
        if key_str in toggle_parts:
            _stop_recording()
            return
        # Don't process any other hotkeys while in toggle recording
        return

    # If currently recording in hold mode: ignore new hotkeys
    if audio_rec.is_recording and _active_mode == "hold":
        return

    # Not recording — check preset hotkeys
    for hotkey_str, (parts, preset_id) in preset_hotkey_parts.items():
        if all(part in pressed_keys for part in parts):
            _switch_preset_with_notification(preset_id)
            return

    # Toggle hotkey: all parts pressed → start toggle recording
    if toggle_parts and all(part in pressed_keys for part in toggle_parts):
        _active_mode = "toggle"
        _start_recording()
        logger.info("Toggle mode: recording started (press any toggle key or stop key to finish)")
        return

    # Hold hotkey: all parts pressed → start hold recording
    if hold_parts and all(part in pressed_keys for part in hold_parts):
        _active_mode = "hold"
        _start_recording()
        return


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
        _cleanup_pending_audio()
        text_inserter.insert_text(text, auto_paste=cfg.get("auto_paste", True))
        logger.info(f"Inserted: {text[:80]}...")
        update_icon()
        # Update tray menu to show last result preview
        if tray_icon:
            tray_icon.menu = build_menu()
        # Save to server history
        _save_to_server_history(raw_text, text)
        # Run send mode in separate thread (don't block WebSocket callback thread)
        threading.Thread(target=handle_send_mode, daemon=True).start()
    else:
        logger.warning("Empty transcription result")
        _cleanup_pending_audio()
        update_icon()


def _save_to_server_history(raw_text: str, processed_text: str):
    """Save transcription to server history so it shows in the web app."""
    try:
        url = f"{cfg['server_url'].rstrip('/')}/api/history"
        httpx.post(url, json={
            "raw_text": raw_text,
            "processed_text": processed_text,
            "mode": _active_preset_id,
            "language": "",
        }, verify=False, timeout=5.0)
        logger.debug("Saved to server history")
    except Exception as e:
        logger.debug(f"Could not save to server history: {e}")


def _on_error(message: str):
    """Called on WebSocket or server error — attempt auto-retry via REST."""
    global _streaming_session
    _streaming_session = None

    logger.error(f"Server error: {message}")

    if _pending_wav_bytes and _pending_mode:
        # Auto-retry in background thread using REST fallback
        wav = _pending_wav_bytes
        mode = _pending_mode
        threading.Thread(target=_auto_retry, args=(wav, mode), daemon=True).start()
    else:
        update_icon()
        _show_error_popup(message)


def _auto_retry(wav_bytes: bytes, mode: str):
    """Attempt transcription via REST with exponential backoff."""
    global _retry_in_progress
    _retry_in_progress = True
    update_icon(retrying=True)
    overlay.show("retrying")

    def _on_retry(attempt, max_retries):
        logger.info(f"Retry attempt {attempt + 1}/{max_retries + 1}")
        overlay.show_notification(f"Erneuter Versuch ({attempt + 1}/{max_retries + 1})...")

    try:
        result = api_client.transcribe_with_retry(
            cfg["server_url"], wav_bytes, mode,
            max_retries=3, on_retry=_on_retry,
        )
        _retry_in_progress = False
        raw = result.get("raw_text", "")
        processed = result.get("processed_text", raw)
        logger.info("Auto-retry succeeded")
        _on_done(raw, processed)
    except Exception as e:
        _retry_in_progress = False
        update_icon()
        logger.error(f"Auto-retry exhausted: {e}")
        _show_error_popup(str(e))
        # Update tray menu to show retry option
        if tray_icon:
            tray_icon.menu = build_menu()


def _show_error_popup(message: str):
    """Show a Windows notification popup for errors."""
    try:
        import threading

        def _show():
            from tkinter import messagebox
            if not tk._default_root:
                _hidden = tk.Tk()
                _hidden.withdraw()
            root = tk.Toplevel()
            root.withdraw()
            root.attributes("-topmost", True)
            if "connect" in message.lower() or "refused" in message.lower() or "unreachable" in message.lower() or "timed out" in message.lower():
                messagebox.showerror(
                    "Parley — Server nicht erreichbar",
                    f"Der Server konnte nicht erreicht werden.\n\n"
                    f"Pruefe:\n"
                    f"  - Laeuft Tailscale / VPN?\n"
                    f"  - Ist der Server eingeschaltet?\n"
                    f"  - Stimmt die Server-URL?\n\n"
                    f"URL: {cfg.get('server_url', '?')}\n"
                    f"Fehler: {message}",
                )
            else:
                messagebox.showerror(
                    "Parley — Fehler",
                    f"Bei der Verarbeitung ist ein Fehler aufgetreten.\n\n"
                    f"Fehler: {message}",
                )
            root.destroy()

        threading.Thread(target=_show, daemon=True).start()
    except Exception:
        pass


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

    # Use same device settings as main recorder for compatibility
    from recorder import _wasapi_input_device, _wasapi_settings
    stream_kwargs = {
        "samplerate": sample_rate,
        "channels": 1,
        "dtype": "int16",
        "blocksize": block_size,
        "callback": audio_callback,
    }
    if _wasapi_input_device is not None:
        stream_kwargs["device"] = _wasapi_input_device
    if _wasapi_settings is not None:
        stream_kwargs["extra_settings"] = _wasapi_settings

    try:
        stream = sd.InputStream(**stream_kwargs)
    except sd.PortAudioError:
        # Fallback to default device
        stream = sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="int16",
            blocksize=block_size, callback=audio_callback,
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


def update_icon(recording: bool = False, processing: bool = False,
                listening: bool = False, retrying: bool = False):
    global tray_icon
    if tray_icon is None:
        return
    if recording:
        tray_icon.icon = create_tray_icon("#ef4444", connected=_server_connected)
        overlay.show("recording")
    elif processing:
        tray_icon.icon = create_tray_icon("#f59e0b", connected=_server_connected)
        overlay.update_state("processing")
    elif retrying:
        tray_icon.icon = create_tray_icon("#f97316", connected=_server_connected)
        overlay.show("retrying")
    elif listening:
        tray_icon.icon = create_tray_icon("#22c55e", connected=_server_connected)
        overlay.update_state("listening")
    else:
        tray_icon.icon = create_tray_icon("#3b82f6", connected=_server_connected)
        overlay.hide()


def set_preset(preset_id: str):
    def _set(icon, item):
        _set_active_preset(preset_id)
        logger.info(f"Preset set to: {preset_id}")
    return _set


def get_preset_checked(preset_id: str):
    def _check(item):
        return _active_preset_id == preset_id
    return _check


def toggle_auto_paste(icon, item):
    cfg["auto_paste"] = not cfg.get("auto_paste", True)
    config.save(cfg)
    logger.info(f"Auto-paste: {cfg['auto_paste']}")


def set_send_mode(mode: str):
    def _set(icon, item):
        cfg["send_mode"] = mode
        config.save(cfg)
        logger.info(f"Send mode: {mode}")
    return _set


def get_send_mode_checked(mode: str):
    def _check(item):
        return cfg.get("send_mode", "off") == mode
    return _check


def open_settings(icon, item):
    """Open settings window and apply changes."""
    logger.info("Opening settings window...")

    def on_save(new_cfg):
        global cfg, hold_parts, toggle_parts, stop_parts
        cfg = new_cfg
        hold_parts = parse_hotkey(cfg["hotkey_hold"])
        toggle_parts = parse_hotkey(cfg["hotkey_toggle"])
        stop_parts = parse_hotkey(cfg["stop_key"])
        _parse_preset_hotkeys()
        _fetch_presets()
        logger.info(f"Settings updated — Hold: {cfg['hotkey_hold']}, Toggle: {cfg['hotkey_toggle']}, Server: {cfg['server_url']}")
        if tray_icon:
            tray_icon.menu = build_menu()

    def _open():
        try:
            settings_ui.open_settings(cfg, on_save)
        except Exception as e:
            logger.error(f"Settings window error: {e}")

    threading.Thread(target=_open, daemon=True).start()


def retry_last_recording(icon, item):
    """Manual retry: re-send pending audio via REST with retries."""
    wav = _pending_wav_bytes
    mode = _pending_mode
    if not wav:
        # Try loading from disk
        try:
            wav_path = _pending_audio_dir / "recording.wav"
            meta_path = _pending_audio_dir / "recording.json"
            if wav_path.exists() and meta_path.exists():
                wav = wav_path.read_bytes()
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                mode = meta.get("mode", "raw")
        except Exception:
            pass
    if wav:
        threading.Thread(target=_auto_retry, args=(wav, mode or "raw"), daemon=True).start()
    else:
        overlay.show_notification("Kein Audio zum Wiederholen")


def copy_last_result(icon, item):
    """Copy the last transcription result to clipboard and show notification."""
    if last_result_text:
        import pyperclip
        pyperclip.copy(last_result_text)
        logger.info(f"Copied last result to clipboard: {last_result_text[:50]}...")
        overlay.show_notification("In Zwischenablage kopiert")
    else:
        logger.info("No previous result to copy")


def show_tray_window(icon=None, item=None):
    """Open the custom tray window with current state."""
    state = {
        "connected": _server_connected,
        "active_preset": _active_preset_id,
        "presets": _cached_presets,
        "last_result": last_result_text,
        "auto_paste": cfg.get("auto_paste", True),
        "send_mode": cfg.get("send_mode", "off"),
        "hotkey_hold": cfg.get("hotkey_hold", ""),
        "hotkey_toggle": cfg.get("hotkey_toggle", ""),
    }
    tray_window.show(state=state)


def _setup_tray_window_callbacks():
    """Wire up the TrayWindow actions to app functions."""
    tray_window.on("set_preset", lambda pid: _switch_preset_with_notification(pid))
    tray_window.on("copy_last", lambda: (
        copy_last_result(None, None),
    ))
    tray_window.on("toggle_auto_paste", lambda: toggle_auto_paste(None, None))
    tray_window.on("set_send_mode", lambda mode: (
        set_send_mode(mode)(None, None),
    ))
    tray_window.on("open_settings", lambda: open_settings(None, None))
    tray_window.on("quit", lambda: tray_icon.stop() if tray_icon else None)


def quit_app(icon, item):
    icon.stop()


def _build_preset_menu_items():
    """Build dynamic preset menu items from cached presets."""
    items = [pystray.MenuItem("Raw", set_preset("raw"), checked=get_preset_checked("raw"))]
    for preset in _cached_presets:
        pid = preset.get("id", "")
        name = preset.get("name", pid)
        if pid == "raw":
            continue  # Already added as first item
        items.append(pystray.MenuItem(name, set_preset(pid), checked=get_preset_checked(pid)))
    return items


def build_menu():
    last_preview = (last_result_text[:30] + "...") if len(last_result_text) > 30 else last_result_text
    has_pending = _pending_wav_bytes is not None or (_pending_audio_dir / "recording.wav").exists()
    return pystray.Menu(
        pystray.MenuItem("Preset", pystray.Menu(*_build_preset_menu_items())),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            f"Letzte: {last_preview}" if last_result_text else "Kein letztes Ergebnis",
            copy_last_result,
            enabled=bool(last_result_text),
        ),
        pystray.MenuItem(
            "Erneut versuchen",
            retry_last_recording,
            enabled=has_pending,
            visible=has_pending,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Auto-Paste", toggle_auto_paste,
                         checked=lambda item: cfg.get("auto_paste", True)),
        pystray.MenuItem("Senden", pystray.Menu(
            pystray.MenuItem("Aus", set_send_mode("off"), checked=get_send_mode_checked("off")),
            pystray.MenuItem("Auto (Enter)", set_send_mode("auto"), checked=get_send_mode_checked("auto")),
            pystray.MenuItem("Sprachbefehl", set_send_mode("voice"), checked=get_send_mode_checked("voice")),
        )),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"Halten: {cfg.get('hotkey_hold', '')}", lambda *a: None, enabled=False),
        pystray.MenuItem(f"Freihand: {cfg.get('hotkey_toggle', '')}", lambda *a: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Einstellungen...", open_settings),
        pystray.MenuItem("Beenden", quit_app),
    )


def _parse_preset_hotkeys():
    """Parse preset hotkey config into the global preset_hotkey_parts dict."""
    global preset_hotkey_parts
    preset_hotkey_parts = {}
    raw_map = cfg.get("preset_hotkeys", {})
    for hotkey_str, preset_id in raw_map.items():
        parts = parse_hotkey(hotkey_str)
        preset_hotkey_parts[hotkey_str] = (parts, preset_id)
        logger.info(f"Preset hotkey: {hotkey_str} -> {preset_id}")


def main():
    global tray_icon, hold_parts, toggle_parts, stop_parts

    logger.info("Parley Desktop Client")
    logger.info(f"Server: {cfg['server_url']}")

    # Clean up stale pending audio from previous sessions
    _cleanup_stale_pending_audio()

    # Check server and fetch presets
    _check_server_connection()
    _fetch_presets()
    logger.info(f"Active preset: {_active_preset_id}")
    logger.info(f"Server connected: {_server_connected}")

    # Start background health check
    threading.Thread(target=_server_health_loop, daemon=True).start()

    hold_parts = parse_hotkey(cfg["hotkey_hold"])
    toggle_parts = parse_hotkey(cfg["hotkey_toggle"])
    stop_parts = parse_hotkey(cfg["stop_key"])
    _parse_preset_hotkeys()
    logger.info(f"Hold hotkey: {hold_parts}")
    logger.info(f"Toggle hotkey: {toggle_parts}")
    logger.info(f"Stop key: {stop_parts}")

    # Wire up custom tray window callbacks
    _setup_tray_window_callbacks()

    # Start keyboard listener
    listener = keyboard.Listener(on_press=on_key_press, on_release=on_key_release)
    listener.start()

    # Create and run tray icon (left-click opens custom window, right-click shows minimal menu)
    tray_icon = pystray.Icon(
        "parley",
        create_tray_icon(connected=_server_connected),
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
