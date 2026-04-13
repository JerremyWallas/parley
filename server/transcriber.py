import io
import time
import logging
from faster_whisper import WhisperModel
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, MODEL_DIR

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        logger.info(f"Loading Whisper model '{WHISPER_MODEL}' on {WHISPER_DEVICE} ({WHISPER_COMPUTE_TYPE})...")
        _model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
            download_root=str(MODEL_DIR),
        )
        logger.info("Whisper model loaded.")
    return _model


def transcribe(audio_bytes: bytes, initial_prompt: str | None = None) -> dict:
    """Transcribe audio bytes and return raw text, language, and duration."""
    model = get_model()

    start = time.time()
    segments, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=5,
        initial_prompt=initial_prompt,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    text_parts = []
    for segment in segments:
        text_parts.append(segment.text)

    raw_text = " ".join(text_parts).strip()
    duration_ms = int((time.time() - start) * 1000)

    return {
        "raw_text": raw_text,
        "language": info.language,
        "language_probability": round(info.language_probability, 2),
        "duration_ms": duration_ms,
    }
