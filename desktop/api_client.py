"""API client for Parley server — WebSocket streaming + REST fallback."""
import json
import ssl
import logging
import threading
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
        self._completed = False  # set once a terminal callback (done/error) has fired

    def _on_open(self, ws):
        self._connected.set()
        logger.info("WebSocket connected")

    def start(self):
        """Open WebSocket connection in background thread."""
        url = _ws_url(self.server_url, "/ws/transcribe")
        logger.info(f"Opening WebSocket to {url}")

        self._connected.clear()
        self._completed = False
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

    def send_audio(self, audio_bytes: bytes):
        """Send audio chunk to server. Format depends on what the recorder produces
        (Ogg/Opus by default, WAV in fallback mode); the server detects via magic bytes."""
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(audio_bytes, opcode=websocket.ABNF.OPCODE_BINARY)
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
            self._completed = True
            if self.on_done:
                self.on_done(self._raw_text, processed)
            self.close()

        elif msg_type == "error":
            error_msg = data.get("message", "Unknown error")
            logger.error(f"Server error: {error_msg}")
            self._completed = True
            if self.on_error:
                self.on_error(error_msg)
            self.close()

    def _on_ws_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")
        self._completed = True
        if self.on_error:
            self.on_error(str(error))

    def _on_close(self, ws, close_status_code, close_msg):
        logger.debug("WebSocket closed")
        # If the socket closed cleanly before any terminal result (e.g. the server
        # dropped the connection between transcription_done and llm_done), route it
        # to on_error so the caller recovers instead of hanging on "processing".
        if not self._completed:
            self._completed = True
            if self.on_error:
                self.on_error("connection closed before result")


def transcribe(server_url: str, audio_bytes: bytes, mode: str = "raw",
               content_type: str = "audio/ogg", filename: str = "recording.ogg") -> dict:
    """REST transcription — send complete audio, get result.

    Default content_type is audio/ogg because the main client path now records
    Ogg/Opus (~10× smaller than WAV). Voice-send and other internal callers
    that still produce WAV inline can pass content_type="audio/wav" explicitly.

    Timeouts are split per phase to be mobile-friendly:
      connect=30s — TLS handshake under flaky cell signal
      write=240s  — Edge-class uploads even for WAV-fallback payloads
      read=180s   — server-side LLM step can take a while on big models
    """
    url = f"{server_url.rstrip('/')}/api/transcribe"

    timeout = httpx.Timeout(connect=30.0, read=180.0, write=240.0, pool=10.0)
    with httpx.Client(timeout=timeout, verify=False) as client:
        response = client.post(
            url,
            files={"audio": (filename, audio_bytes, content_type)},
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

    # Defensive: with max_retries=0 and no exception assigning last_error,
    # we'd otherwise `raise None`. Practically unreachable but free.
    raise last_error if last_error else RuntimeError("Transcription failed with no recorded error")
