import io
import time
import logging
from typing import Generator
from faster_whisper import WhisperModel
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, MODEL_DIR

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None


def _detect_compute_type() -> str:
    """Detect the best compute type for the current GPU.

    float16 requires Compute Capability >= 7.0 (Volta/Turing/Ampere/Ada).
    Older GPUs (Pascal: GTX 1060/1070/1080, CC 6.1) crash or run very
    slowly with float16. For those we fall back to int8_float32.
    """
    if WHISPER_COMPUTE_TYPE != "auto":
        return WHISPER_COMPUTE_TYPE

    if WHISPER_DEVICE != "cuda":
        return "int8"

    try:
        import ctypes
        libcudart = ctypes.CDLL("libcudart.so")
        device = ctypes.c_int(0)
        major = ctypes.c_int(0)
        minor = ctypes.c_int(0)
        # cudaDeviceGetAttribute: 75 = major, 76 = minor compute capability
        libcudart.cudaDeviceGetAttribute(ctypes.byref(major), 75, device)
        libcudart.cudaDeviceGetAttribute(ctypes.byref(minor), 76, device)
        cc = major.value + minor.value / 10
        logger.info(f"GPU Compute Capability: {major.value}.{minor.value}")

        if cc >= 7.0:
            logger.info("GPU supports float16 — using float16")
            return "float16"
        else:
            logger.info("GPU too old for float16 (needs CC >= 7.0) — using int8_float32")
            return "int8_float32"
    except Exception as e:
        logger.warning(f"Could not detect GPU capability ({e}) — falling back to int8_float32")
        return "int8_float32"


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        compute_type = _detect_compute_type()
        logger.info(f"Loading Whisper model '{WHISPER_MODEL}' on {WHISPER_DEVICE} ({compute_type})...")
        _model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=compute_type,
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


def transcribe_streaming(audio_bytes: bytes, initial_prompt: str | None = None) -> Generator[dict, None, None]:
    """Transcribe audio and yield each segment as it's ready."""
    model = get_model()

    start = time.time()
    segments, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=5,
        initial_prompt=initial_prompt,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    for segment in segments:
        yield {
            "type": "segment",
            "text": segment.text.strip(),
            "start": round(segment.start, 2),
            "end": round(segment.end, 2),
            "language": info.language,
        }

    duration_ms = int((time.time() - start) * 1000)
    yield {
        "type": "transcription_done",
        "language": info.language,
        "language_probability": round(info.language_probability, 2),
        "duration_ms": duration_ms,
    }
