import httpx
import logging

logger = logging.getLogger(__name__)


def transcribe(server_url: str, audio_bytes: bytes, mode: str = "raw") -> dict:
    """Send audio to the server and return transcription result."""
    url = f"{server_url.rstrip('/')}/api/transcribe"

    # SECURITY: SSL-Verifikation ist deaktiviert, da der Heimserver Self-Signed Certs nutzt.
    # Fuer den Einsatz mit richtigen Zertifikaten: verify=True setzen.
    with httpx.Client(timeout=120.0, verify=False) as client:
        response = client.post(
            url,
            files={"audio": ("recording.wav", audio_bytes, "audio/wav")},
            data={"mode": mode},
        )
        response.raise_for_status()
        return response.json()
