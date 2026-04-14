import io
import os
import time
import logging
from typing import Generator
from faster_whisper import WhisperModel
import config
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, MODEL_DIR

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None

# Available Whisper models sorted by VRAM requirement, then quality
AVAILABLE_WHISPER_MODELS = sorted([
    {"id": "tiny", "name": "Tiny", "desc": "Ultra-schnell, Basisqualitaet", "vram": "~1 GB", "vram_mb": 1024, "quality": 1},
    {"id": "small", "name": "Small", "desc": "Schnell, gute Qualitaet", "vram": "~2 GB", "vram_mb": 2048, "quality": 2},
    {"id": "medium", "name": "Medium", "desc": "Ausgewogen, sehr gute Qualitaet", "vram": "~5 GB", "vram_mb": 5120, "quality": 3},
    {"id": "large-v3", "name": "Large V3", "desc": "Beste Qualitaet, langsamer", "vram": "~6 GB", "vram_mb": 6144, "quality": 4},
], key=lambda m: (m["vram_mb"], m["quality"]))


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


def set_model(model_name: str) -> None:
    """Unload current model and set the new model name for next load."""
    global _model
    config.WHISPER_MODEL = model_name
    _model = None
    logger.info(f"Whisper model switched to '{model_name}' (will load on next transcription)")


def download_model(model_name: str) -> None:
    """Download a Whisper model by loading it, then unload to free VRAM."""
    compute_type = _detect_compute_type()
    logger.info(f"Downloading Whisper model '{model_name}'...")
    m = WhisperModel(
        model_name,
        device=WHISPER_DEVICE,
        compute_type=compute_type,
        download_root=str(MODEL_DIR),
    )
    # Unload immediately — we just wanted the download
    del m
    logger.info(f"Whisper model '{model_name}' downloaded and cached.")


def delete_model(model_name: str) -> None:
    """Delete a cached Whisper model from disk."""
    import shutil
    model_dir = os.path.join(str(MODEL_DIR), f"models--Systran--faster-whisper-{model_name}")
    if os.path.isdir(model_dir):
        shutil.rmtree(model_dir)
        logger.info(f"Deleted Whisper model '{model_name}' from {model_dir}")
    else:
        raise FileNotFoundError(f"Model directory not found: {model_dir}")


def list_models(gpu_total_mb: int = 0) -> list[dict]:
    """Return available models with installed and fits_gpu flags."""
    result = []
    for m in AVAILABLE_WHISPER_MODELS:
        model_dir = os.path.join(str(MODEL_DIR), f"models--Systran--faster-whisper-{m['id']}")
        installed = os.path.isdir(model_dir)
        fits_gpu = gpu_total_mb >= m["vram_mb"] if gpu_total_mb > 0 else True
        result.append({**m, "installed": installed, "fits_gpu": fits_gpu})
    return result


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        import personalization
        prefs = personalization.get_preferences()
        model_name = prefs.get("whisper_model", WHISPER_MODEL)

        compute_type = _detect_compute_type()
        logger.info(f"Loading Whisper model '{model_name}' on {WHISPER_DEVICE} ({compute_type})...")
        _model = WhisperModel(
            model_name,
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
