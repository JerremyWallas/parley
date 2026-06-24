import io
import os
import re
import time
import logging
import threading
from typing import Generator
from faster_whisper import WhisperModel
import config
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, MODEL_DIR

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None
# Serializes cache-dir mutations so a delete can't race an in-progress download.
_cache_lock = threading.Lock()

# Available Whisper models sorted by VRAM requirement, then quality.
# "repo" is the HF id passed to faster-whisper (and used to derive the cache dir);
# it is decoupled from "id" so German CTranslate2 finetunes living under other
# orgs work with the same download/delete/list code as the Systran builtins.
AVAILABLE_WHISPER_MODELS = sorted([
    {"id": "tiny", "name": "Tiny", "desc": "Ultra-schnell, Basisqualitaet", "vram": "~1 GB", "vram_mb": 1024, "quality": 1, "repo": "Systran/faster-whisper-tiny"},
    {"id": "small", "name": "Small", "desc": "Schnell, gute Qualitaet", "vram": "~2 GB", "vram_mb": 2048, "quality": 2, "repo": "Systran/faster-whisper-small"},
    {"id": "medium", "name": "Medium", "desc": "Ausgewogen, sehr gute Qualitaet", "vram": "~5 GB", "vram_mb": 5120, "quality": 3, "repo": "Systran/faster-whisper-medium"},
    {"id": "large-v3", "name": "Large V3", "desc": "Beste Qualitaet, langsamer", "vram": "~6 GB", "vram_mb": 6144, "quality": 4, "repo": "Systran/faster-whisper-large-v3"},
    # German finetunes (primeline / Florian Zimmermeister), pre-converted to CTranslate2:
    # ponytail: turbo-german has a known bug - it writes a double-s where an eszett belongs
    # (e.g. "Massstab"). Author-confirmed. Add a post-process fix in transcribe() if it bothers you.
    {"id": "large-v3-turbo-german", "name": "Large V3 Turbo (Deutsch)", "desc": "Deutsch-optimiert + schnell (Turbo)", "vram": "~3 GB", "vram_mb": 3072, "quality": 5, "repo": "TheChola/whisper-large-v3-turbo-german-faster-whisper"},
    {"id": "large-v3-german", "name": "Large V3 (Deutsch)", "desc": "Deutsch-optimiert, beste dt. Qualitaet", "vram": "~6 GB", "vram_mb": 6144, "quality": 5, "repo": "Reality-Interface/whisper-large-v3-german-faster-whisper"},
], key=lambda m: (m["vram_mb"], m["quality"]))

_MODEL_BY_ID = {m["id"]: m for m in AVAILABLE_WHISPER_MODELS}


def _repo_for(model_id: str) -> str:
    """Map a model id to the HF repo / shortcut faster-whisper should load."""
    m = _MODEL_BY_ID.get(model_id)
    return m["repo"] if m else model_id


def _cache_dir(repo: str) -> str:
    """HF hub cache directory for a given repo id (e.g. org/name -> models--org--name)."""
    return os.path.join(str(MODEL_DIR), "models--" + repo.replace("/", "--"))


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
    """Download a Whisper model without using GPU VRAM."""
    with _cache_lock:
        logger.info(f"Downloading Whisper model '{model_name}'...")
        m = WhisperModel(
            _repo_for(model_name),
            device="cpu",
            compute_type="int8",
            download_root=str(MODEL_DIR),
        )
        del m
        logger.info(f"Whisper model '{model_name}' downloaded and cached.")


def delete_model(model_name: str) -> None:
    """Delete a cached Whisper model from disk."""
    import shutil
    model_dir = _cache_dir(_repo_for(model_name))
    with _cache_lock:
        if os.path.isdir(model_dir):
            shutil.rmtree(model_dir)
            logger.info(f"Deleted Whisper model '{model_name}' from {model_dir}")
        else:
            raise FileNotFoundError(f"Model directory not found: {model_dir}")


def list_models(gpu_total_mb: int = 0) -> list[dict]:
    """Return available models with installed and fits_gpu flags."""
    result = []
    for m in AVAILABLE_WHISPER_MODELS:
        model_dir = _cache_dir(m["repo"])
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
            _repo_for(model_name),
            device=WHISPER_DEVICE,
            compute_type=compute_type,
            download_root=str(MODEL_DIR),
        )
        logger.info("Whisper model loaded.")
    return _model


def _get_language() -> str | None:
    """Get the configured whisper language from preferences (None = auto-detect)."""
    import personalization
    prefs = personalization.get_preferences()
    return prefs.get("whisper_language", None)


# ponytail: the large-v3-turbo-german finetune has an author-confirmed bug where it
# writes a double-s instead of an eszett (e.g. "Massstab"). We can't blindly replace
# "ss" (dass/muss/Wasser are correct), so we whitelist common words whose double-s
# spelling is itself not valid German. Ceiling: misses rare words, and may "correct" a
# surname (Weiss/Gross); upgrade path = a German hunspell lookup. Only runs for that model.
_ESZETT_FIXES = {
    "strasse": "straße", "strassen": "straßen",
    "gross": "groß", "grosse": "große", "grossen": "großen",
    "grosser": "großer", "grosses": "großes", "grösse": "größe", "grössere": "größere",
    "fuss": "fuß", "füsse": "füße",
    "weiss": "weiß", "weisse": "weiße", "weissen": "weißen",
    "heiss": "heiß", "heisse": "heiße", "heissen": "heißen", "heisst": "heißt",
    "spass": "spaß",
    "gruss": "gruß", "grüsse": "grüße",
    "schliessen": "schließen", "schliesst": "schließt", "schliesse": "schließe",
    "geniessen": "genießen", "geniesst": "genießt",
    "draussen": "draußen",
    "massnahme": "maßnahme", "massnahmen": "maßnahmen",
    "massstab": "maßstab", "massstäbe": "maßstäbe",
    "fliessen": "fließen", "fliesst": "fließt",
    "stossen": "stoßen", "stösst": "stößt",
    "schiessen": "schießen",
    "giessen": "gießen",
    "süss": "süß", "süsse": "süße", "süssen": "süßen",
}

_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")

_ESZETT_MODEL_IDS = {"large-v3-turbo-german"}


def _fix_eszett(text: str) -> str:
    """Repair double-s -> eszett for known words. Leaves legitimate 'ss' untouched."""
    def repl(m: re.Match) -> str:
        w = m.group(0)
        if w.isupper():
            return w  # all-caps German uses SS, not ß
        fix = _ESZETT_FIXES.get(w.lower())
        if not fix:
            return w
        return fix[0].upper() + fix[1:] if w[0].isupper() else fix
    return _WORD_RE.sub(repl, text)


def _eszett_active() -> bool:
    import personalization
    model_id = personalization.get_preferences().get("whisper_model", WHISPER_MODEL)
    return model_id in _ESZETT_MODEL_IDS


def transcribe(audio_bytes: bytes, initial_prompt: str | None = None) -> dict:
    """Transcribe audio bytes and return raw text, language, and duration."""
    model = get_model()
    language = _get_language()

    start = time.time()
    segments, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=5,
        initial_prompt=initial_prompt,
        language=language,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    text_parts = []
    for segment in segments:
        text_parts.append(segment.text)

    raw_text = " ".join(text_parts).strip()
    if _eszett_active():
        raw_text = _fix_eszett(raw_text)
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
    language = _get_language()

    start = time.time()
    segments, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=5,
        initial_prompt=initial_prompt,
        language=language,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    fix_eszett = _eszett_active()
    for segment in segments:
        text = segment.text.strip()
        if fix_eszett:
            text = _fix_eszett(text)
        yield {
            "type": "segment",
            "text": text,
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
