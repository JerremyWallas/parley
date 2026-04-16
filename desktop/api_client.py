"""API client for Parley server — WebSocket streaming + REST fallback."""
import io
import json
import wave
import ssl
import logging
import threading
import numpy as np
import websocket
import httpx

logger = logging.getLogger(__name__)

# SECURITY: SSL-Verifikation ist deaktiviert, da der Heimserver Self-Signed Certs nutzt.
# Fuer den Einsatz mit richtigen Zertifikaten: verify=True / ssl defaults setzen.
_ssl_context = ssl.create_default_context()
_ssl_context.check_hostname = False
_ssl_context.verify_mode = ssl.CERT_NONE


def _ws_url(server_url: str, path: str) -> str:
    return server_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + path


def _make_wav_chunk(frames: list[np.ndarray], sample_rate: int = 16000) -> bytes:
    """Convert audio frames to WAV bytes."""
    if not frames:
        return b""
    audio = np.concatenate(frames, axis=0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


class StreamingSession:
    """WebSocket session that streams audio during recording and receives results.

    Usage:
        session = StreamingSession(server_url, mode, on_result)
        session.start()
        # During recording, feed audio frames:
        session.send_audio(frames)
        # When done:
        session.finish()
        # Results arrive via on_result callback
    """

    def __init__(self, server_url: str, mode: str, on_segment=None, on_llm_token=None, on_done=None, on_error=None):
        self.server_url = server_url
        self.mode = mode
        self.on_segment = on_segment  # fn(text) — called for each Whisper segment
        self.on_llm_token = on_llm_token  # fn(token) — called for each LLM token
        self.on_done = on_done  # fn(raw_text, processed_text) — called when complete
        self.on_error = on_error  # fn(error_message)
        self._ws = None
        self._thread = None
        self._connected = threading.Event()
        self._raw_text = ""
        self._processed_parts = []

    def _on_open(self, ws):
        self._connected.set()
        logger.info("WebSocket connected")

    def start(self):
        """Open WebSocket connection in background thread."""
        url = _ws_url(self.server_url, "/ws/transcribe")
        logger.info(f"Opening WebSocket to {url}")

        self._connected.clear()
        self._ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_ws_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={"sslopt": {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}},
            daemon=True,
        )
        self._thread.start()
        # Wait for connection with proper timeout
        if not self._connected.wait(timeout=3.0):
            logger.warning("WebSocket connection timed out")

    def send_audio(self, wav_bytes: bytes):
        """Send audio chunk to server."""
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(wav_bytes, opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception as e:
                logger.error(f"Failed to send audio chunk: {e}")

    def finish(self):
        """Signal recording is done and wait for results."""
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(json.dumps({"type": "stop", "mode": self.mode}))
            except Exception as e:
                logger.error(f"Failed to send stop: {e}")

    def close(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")

        if msg_type == "segment":
            text = data.get("text", "")
            if text and self.on_segment:
                self.on_segment(text)

        elif msg_type == "transcription_done":
            self._raw_text = data.get("raw_text", "")
            logger.info(f"Transcription done: '{self._raw_text[:80]}...'")

        elif msg_type == "llm_token":
            token = data.get("token", "")
            self._processed_parts.append(token)
            if self.on_llm_token:
                self.on_llm_token(token)

        elif msg_type == "llm_done":
            processed = data.get("processed_text", self._raw_text)
            logger.info(f"LLM done: '{processed[:80]}...'")
            if self.on_done:
                self.on_done(self._raw_text, processed)
            self.close()

        elif msg_type == "error":
            error_msg = data.get("message", "Unknown error")
            logger.error(f"Server error: {error_msg}")
            if self.on_error:
                self.on_error(error_msg)
            self.close()

    def _on_ws_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")
        if self.on_error:
            self.on_error(str(error))

    def _on_close(self, ws, close_status_code, close_msg):
        logger.debug("WebSocket closed")


def transcribe(server_url: str, audio_bytes: bytes, mode: str = "raw") -> dict:
    """REST fallback — send complete audio and get result. Used by voice-send."""
    url = f"{server_url.rstrip('/')}/api/transcribe"

    with httpx.Client(timeout=120.0, verify=False) as client:
        response = client.post(
            url,
            files={"audio": ("recording.wav", audio_bytes, "audio/wav")},
            data={"mode": mode},
        )
        response.raise_for_status()
        return response.json()


def transcribe_with_retry(server_url: str, audio_bytes: bytes, mode: str = "raw",
                          max_retries: int = 3, on_retry=None) -> dict:
    """REST transcription with exponential backoff retry on network errors.

    Args:
        on_retry: Optional callback fn(attempt, max_retries) called before each retry.
    Returns:
        Server response dict on success.
    Raises:
        Last exception if all retries exhausted, or immediately on 4xx errors.
    """
    import time

    backoff = [2, 4, 8]
    last_error = None

    for attempt in range(1, max_retries + 2):  # 1 initial + max_retries
        try:
            return transcribe(server_url, audio_bytes, mode)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.WriteTimeout, httpx.PoolTimeout) as e:
            last_error = e
        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500:
                raise  # 4xx = client error, don't retry
            last_error = e
        except Exception as e:
            last_error = e

        if attempt > max_retries:
            break

        delay = backoff[attempt - 1] if attempt - 1 < len(backoff) else backoff[-1]
        logger.warning(f"Transcription failed (attempt {attempt}/{max_retries + 1}), "
                       f"retrying in {delay}s: {last_error}")
        if on_retry:
            on_retry(attempt, max_retries)
        time.sleep(delay)

    raise last_error
