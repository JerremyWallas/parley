import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import config
import transcriber
import cleanup
import personalization

logging.basicConfig(level=config.LOG_LEVEL.upper(), format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Preload whisper model on startup
    logger.info("Preloading Whisper model...")
    transcriber.get_model()
    logger.info("Server ready.")
    yield


app = FastAPI(title="Parley API", version="1.0.0", lifespan=lifespan)

# SECURITY: CORS ist offen konfiguriert — kein Auth-Mechanismus.
# Parley ist fuer den Einsatz im lokalen Heimnetz gedacht.
# Fuer den Einsatz ueber das Internet: Auth einbauen und Origins einschraenken.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    """Server health check with GPU and Ollama status."""
    ollama_status = await cleanup.check_ollama()

    gpu_info = "unknown"
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            gpu_info = result.stdout.strip()
    except Exception:
        pass

    return {
        "status": "ok",
        "whisper_model": config.WHISPER_MODEL,
        "gpu": gpu_info,
        "ollama": ollama_status,
        "language_stats": personalization.get_language_stats(),
    }


@app.post("/api/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    mode: str = Form("raw"),
):
    """Transcribe audio and optionally process with LLM.

    mode: "raw" | "cleanup" | "rephrase"
    """
    if mode not in ("raw", "cleanup", "rephrase"):
        raise HTTPException(400, f"Invalid mode: {mode}. Must be 'raw', 'cleanup', or 'rephrase'.")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "Empty audio file.")

    # Build initial prompt from glossary
    initial_prompt = personalization.build_initial_prompt()

    # Transcribe
    result = transcriber.transcribe(audio_bytes, initial_prompt=initial_prompt)
    raw_text = result["raw_text"]

    if not raw_text:
        return JSONResponse({
            "raw_text": "",
            "processed_text": "",
            "mode": mode,
            "language": result["language"],
            "duration_ms": result["duration_ms"],
        })

    # Update language stats
    personalization.update_language_stats(result["language"])

    # LLM processing
    few_shot = personalization.get_recent_corrections() if mode != "raw" else None
    processed_text = await cleanup.process_text(mode, raw_text, few_shot)

    return JSONResponse({
        "raw_text": raw_text,
        "processed_text": processed_text,
        "mode": mode,
        "language": result["language"],
        "language_probability": result["language_probability"],
        "duration_ms": result["duration_ms"],
    })


@app.post("/api/correction")
async def save_correction(data: dict):
    """Save a user correction for few-shot learning."""
    original = data.get("original", "").strip()
    corrected = data.get("corrected", "").strip()
    if not original or not corrected:
        raise HTTPException(400, "Both 'original' and 'corrected' fields are required.")
    personalization.save_correction(original, corrected)
    return {"status": "ok"}


# --- Glossary endpoints ---

@app.get("/api/glossary")
async def get_glossary():
    return {"words": personalization.get_glossary()}


@app.post("/api/glossary")
async def add_word(data: dict):
    word = data.get("word", "").strip()
    if not word:
        raise HTTPException(400, "Field 'word' is required.")
    personalization.add_glossary_word(word)
    return {"words": personalization.get_glossary()}


@app.delete("/api/glossary")
async def remove_word(data: dict):
    word = data.get("word", "").strip()
    if not word:
        raise HTTPException(400, "Field 'word' is required.")
    personalization.remove_glossary_word(word)
    return {"words": personalization.get_glossary()}


# --- Preferences ---

@app.get("/api/preferences")
async def get_preferences():
    return personalization.get_preferences()


@app.put("/api/preferences")
async def update_preferences(data: dict):
    personalization.save_preferences(data)
    return data


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, log_level=config.LOG_LEVEL)
