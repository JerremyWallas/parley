import json
import logging
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

import config
import transcriber
import cleanup
import personalization

logging.basicConfig(level=config.LOG_LEVEL.upper(), format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Migrate old custom_prompts to presets format
    personalization.migrate_custom_prompts()
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
    active_llm = cleanup._get_active_model()

    gpu_name = "unknown"
    gpu_memory_used = 0
    gpu_memory_total = 0
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            if len(parts) == 3:
                gpu_name = parts[0]
                gpu_memory_used = int(parts[1])
                gpu_memory_total = int(parts[2])
    except Exception:
        pass

    return {
        "status": "ok",
        "whisper_model": config.WHISPER_MODEL,
        "llm_model": active_llm,
        "gpu_name": gpu_name,
        "gpu_memory_used_mb": gpu_memory_used,
        "gpu_memory_total_mb": gpu_memory_total,
        "gpu_memory_percent": round(gpu_memory_used / gpu_memory_total * 100, 1) if gpu_memory_total > 0 else 0,
        "ollama": ollama_status,
        "language_stats": personalization.get_language_stats(),
    }


@app.post("/api/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    mode: str = Form("raw"),
):
    """Transcribe audio and optionally process with LLM.

    mode: preset ID or "raw" (no processing)
    """
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


# --- Presets ---

@app.get("/api/presets")
async def get_presets():
    """Get all presets and the active preset ID."""
    return {
        "presets": personalization.get_presets(),
        "active": personalization.get_active_preset_id(),
    }


@app.post("/api/presets")
async def create_preset(data: dict):
    """Create a new preset."""
    name = data.get("name", "").strip()
    prompt = data.get("prompt", "").strip()
    if not name or not prompt:
        raise HTTPException(400, "Fields 'name' and 'prompt' are required.")
    preset = personalization.add_preset(name, prompt)
    return preset


@app.put("/api/presets/active")
async def set_active_preset(data: dict):
    """Set the active preset by ID."""
    preset_id = data.get("id", "").strip()
    if not preset_id:
        raise HTTPException(400, "Field 'id' is required.")
    personalization.set_active_preset(preset_id)
    return {"status": "ok", "active": preset_id}


@app.post("/api/presets/{preset_id}/reset")
async def reset_preset(preset_id: str):
    """Reset a builtin preset's prompt back to the default."""
    if preset_id not in cleanup.DEFAULT_PROMPTS:
        raise HTTPException(400, f"No default prompt for '{preset_id}'.")
    default_prompt = cleanup.DEFAULT_PROMPTS[preset_id]
    personalization.update_preset(preset_id, prompt=default_prompt)
    return {"status": "ok", "prompt": default_prompt}


@app.put("/api/presets/{preset_id}")
async def update_preset(preset_id: str, data: dict):
    """Update an existing preset's name and/or prompt."""
    result = personalization.update_preset(
        preset_id,
        name=data.get("name"),
        prompt=data.get("prompt"),
    )
    if result is None:
        raise HTTPException(404, f"Preset '{preset_id}' not found.")
    return result


@app.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str):
    """Delete a non-builtin preset."""
    success = personalization.delete_preset(preset_id)
    if not success:
        raise HTTPException(400, f"Cannot delete preset '{preset_id}' (builtin or not found).")
    return {"status": "ok"}


# --- Preferences ---

@app.get("/api/preferences")
async def get_preferences():
    return personalization.get_preferences()


@app.put("/api/preferences")
async def update_preferences(data: dict):
    personalization.save_preferences(data)
    return data


# --- History (server-side, synced across devices) ---

@app.get("/api/history")
async def get_history():
    return {"entries": personalization.get_history()}


@app.post("/api/history")
async def add_history(data: dict):
    personalization.save_history_entry(
        raw_text=data.get("raw_text", ""),
        processed_text=data.get("processed_text", ""),
        mode=data.get("mode", "raw"),
        language=data.get("language", ""),
    )
    return {"status": "ok"}


@app.delete("/api/history")
async def clear_history():
    personalization.clear_history()
    return {"status": "ok"}


# --- Model selection ---

# Sorted by vram_mb ascending, then quality ascending
AVAILABLE_MODELS = sorted([
    {"id": "gemma2:2b", "name": "Gemma 2 2B", "desc": "Sehr schnell, Basisqualitaet", "vram": "~2 GB", "vram_mb": 2048, "quality": 1},
    {"id": "qwen2.5:3b", "name": "Qwen 2.5 3B", "desc": "Schnell, einfache Aufgaben", "vram": "~2 GB", "vram_mb": 2048, "quality": 2},
    {"id": "mistral:7b", "name": "Mistral 7B", "desc": "Schnell, gute europaeische Sprachen", "vram": "~5 GB", "vram_mb": 5120, "quality": 3},
    {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "desc": "Solide Allround-Qualitaet", "vram": "~5 GB", "vram_mb": 5120, "quality": 4},
    {"id": "qwen2.5:7b", "name": "Qwen 2.5 7B", "desc": "Gute Balance aus Qualitaet und Geschwindigkeit", "vram": "~5 GB", "vram_mb": 5120, "quality": 5},
    {"id": "gemma2:9b", "name": "Gemma 2 9B", "desc": "Gute Qualitaet, kompakt", "vram": "~6 GB", "vram_mb": 6144, "quality": 6},
    {"id": "qwen2.5:14b", "name": "Qwen 2.5 14B", "desc": "Besseres Textverstaendnis und Reformulierung", "vram": "~10 GB", "vram_mb": 10240, "quality": 7},
    {"id": "qwen2.5:32b", "name": "Qwen 2.5 32B", "desc": "Beste Qualitaet, braucht viel Speicher", "vram": "~20 GB", "vram_mb": 20480, "quality": 8},
], key=lambda m: (m["vram_mb"], m["quality"]))


@app.get("/api/models")
async def get_models():
    """List available models with the currently active one and GPU VRAM info."""
    prefs = personalization.get_preferences()
    active = prefs.get("ollama_model", config.OLLAMA_MODEL)

    # Check which models are actually pulled in Ollama
    installed = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{config.OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            tags = resp.json()
            installed = [m["name"] for m in tags.get("models", [])]
    except Exception:
        pass

    # Get GPU total VRAM
    gpu_total_mb = 0
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            gpu_total_mb = int(result.stdout.strip())
    except Exception:
        pass

    models = []
    for m in AVAILABLE_MODELS:
        fits_gpu = gpu_total_mb >= m["vram_mb"] if gpu_total_mb > 0 else True
        models.append({**m, "installed": any(m["id"] in name for name in installed), "fits_gpu": fits_gpu})

    return {"models": models, "active": active, "gpu_total_mb": gpu_total_mb}


@app.put("/api/models")
async def set_model(data: dict):
    """Set the active LLM model."""
    model_id = data.get("model", "").strip()
    if not model_id:
        raise HTTPException(400, "Field 'model' is required.")

    prefs = personalization.get_preferences()
    prefs["ollama_model"] = model_id
    personalization.save_preferences(prefs)

    return {"active": model_id}


@app.post("/api/models/pull")
async def pull_model(data: dict):
    """Pull an Ollama model with streaming progress via SSE."""
    model_id = data.get("model", "").strip()
    if not model_id:
        raise HTTPException(400, "Field 'model' is required.")

    async def stream_progress():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{config.OLLAMA_URL}/api/pull",
                    json={"model": model_id, "stream": True},
                ) as response:
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        progress = json.loads(line)
                        status = progress.get("status", "")
                        total = progress.get("total", 0)
                        completed = progress.get("completed", 0)
                        pct = round(completed / total * 100, 1) if total > 0 else 0

                        event = json.dumps({
                            "status": status,
                            "percent": pct,
                            "completed": completed,
                            "total": total,
                        })
                        yield f"data: {event}\n\n"

                        if status == "success":
                            break
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream_progress(), media_type="text/event-stream")


@app.post("/api/models/delete")
async def delete_model(data: dict):
    """Delete an Ollama model to free up disk space."""
    model_id = data.get("model", "").strip()
    if not model_id:
        raise HTTPException(400, "Field 'model' is required.")

    # Don't allow deleting the active model
    prefs = personalization.get_preferences()
    active = prefs.get("ollama_model", config.OLLAMA_MODEL)
    if model_id == active:
        raise HTTPException(400, "Cannot delete the active model. Switch to another model first.")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(
                f"{config.OLLAMA_URL}/api/delete",
                json={"model": model_id},
            )
            resp.raise_for_status()
    except Exception as e:
        raise HTTPException(500, f"Failed to delete model: {e}")

    return {"status": "ok", "deleted": model_id}


# --- Whisper model selection ---

@app.get("/api/whisper-models")
async def get_whisper_models():
    """List available Whisper models with the currently active one and GPU VRAM info."""
    prefs = personalization.get_preferences()
    active = prefs.get("whisper_model", config.WHISPER_MODEL)

    # Get GPU total VRAM
    gpu_total_mb = 0
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            gpu_total_mb = int(result.stdout.strip())
    except Exception:
        pass

    return {
        "models": transcriber.list_models(gpu_total_mb),
        "active": active,
        "gpu_total_mb": gpu_total_mb,
    }


@app.put("/api/whisper-models")
async def set_whisper_model(data: dict):
    """Set the active Whisper model (unloads current model)."""
    model_id = data.get("model", "").strip()
    if not model_id:
        raise HTTPException(400, "Field 'model' is required.")

    prefs = personalization.get_preferences()
    prefs["whisper_model"] = model_id
    personalization.save_preferences(prefs)

    transcriber.set_model(model_id)

    return {"active": model_id}


@app.post("/api/whisper-models/pull")
async def pull_whisper_model(data: dict):
    """Download a Whisper model with SSE status updates."""
    import asyncio
    model_id = data.get("model", "").strip()
    if not model_id:
        raise HTTPException(400, "Field 'model' is required.")

    async def stream_progress():
        yield f"data: {json.dumps({'status': 'downloading', 'message': f'Downloading {model_id}...'})}\n\n"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, transcriber.download_model, model_id)
            yield f"data: {json.dumps({'status': 'success', 'message': f'Model {model_id} ready'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream_progress(), media_type="text/event-stream")


@app.post("/api/whisper-models/delete")
async def delete_whisper_model(data: dict):
    """Delete a cached Whisper model from disk."""
    model_id = data.get("model", "").strip()
    if not model_id:
        raise HTTPException(400, "Field 'model' is required.")

    prefs = personalization.get_preferences()
    active = prefs.get("whisper_model", config.WHISPER_MODEL)
    if model_id == active:
        raise HTTPException(400, "Cannot delete the active model. Switch to another model first.")

    try:
        transcriber.delete_model(model_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to delete model: {e}")

    return {"status": "ok", "deleted": model_id}


# --- WebSocket streaming endpoint ---

@app.websocket("/ws/transcribe")
async def ws_transcribe(ws: WebSocket):
    """WebSocket endpoint for streaming audio upload and result streaming.

    Protocol:
    Client sends:
      - binary frames: audio chunks (Opus/WebM) during recording
      - text frame: '{"type":"stop","mode":"raw|cleanup|rephrase"}' when done

    Server sends:
      - '{"type":"segment","text":"..."}' for each Whisper segment
      - '{"type":"transcription_done","raw_text":"...","language":"...","duration_ms":N}'
      - '{"type":"llm_token","token":"..."}' for each LLM token
      - '{"type":"llm_done","processed_text":"..."}' when LLM is finished
      - '{"type":"error","message":"..."}' on errors
    """
    await ws.accept()
    audio_buffer = bytearray()

    try:
        while True:
            message = await ws.receive()

            if message.get("type") == "websocket.disconnect":
                break

            # Binary frame = audio chunk
            if "bytes" in message and message["bytes"]:
                audio_buffer.extend(message["bytes"])
                continue

            # Text frame = control message
            if "text" in message:
                data = json.loads(message["text"])
                if data.get("type") != "stop":
                    continue

                mode = data.get("mode", "raw")
                preset_id = data.get("preset", mode)  # New clients send "preset", old clients send "mode"

                if not audio_buffer:
                    await ws.send_json({"type": "error", "message": "No audio received"})
                    continue

                raw_audio = bytes(audio_buffer)
                audio_buffer.clear()

                # If the audio doesn't start with a WAV/RIFF header, it's raw PCM
                # from the desktop client — wrap it in a WAV container
                if raw_audio[:4] != b"RIFF":
                    import io as _io
                    import wave as _wave
                    sample_rate = data.get("sample_rate", 16000)
                    buf = _io.BytesIO()
                    with _wave.open(buf, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(sample_rate)
                        wf.writeframes(raw_audio)
                    audio_bytes = buf.getvalue()
                else:
                    audio_bytes = raw_audio

                # --- Phase 1: Stream Whisper segments ---
                initial_prompt = personalization.build_initial_prompt()
                full_text_parts = []
                language = ""
                duration_ms = 0

                for result in transcriber.transcribe_streaming(audio_bytes, initial_prompt=initial_prompt):
                    if result["type"] == "segment":
                        full_text_parts.append(result["text"])
                        language = result["language"]
                        await ws.send_json({
                            "type": "segment",
                            "text": result["text"],
                        })
                    elif result["type"] == "transcription_done":
                        language = result["language"]
                        duration_ms = result["duration_ms"]

                raw_text = " ".join(full_text_parts).strip()

                await ws.send_json({
                    "type": "transcription_done",
                    "raw_text": raw_text,
                    "language": language,
                    "duration_ms": duration_ms,
                })

                if not raw_text:
                    await ws.send_json({"type": "llm_done", "processed_text": ""})
                    continue

                # Update language stats
                personalization.update_language_stats(language)

                # --- Phase 2: Stream LLM tokens ---
                if preset_id == "raw":
                    await ws.send_json({"type": "llm_done", "processed_text": raw_text})
                else:
                    few_shot = personalization.get_recent_corrections()
                    full_response = []
                    async for token in cleanup.process_text_streaming(preset_id, raw_text, few_shot):
                        full_response.append(token)
                        await ws.send_json({"type": "llm_token", "token": token})

                    processed_text = "".join(full_response).strip()
                    # Remove surrounding quotes if the model wrapped the response
                    if len(processed_text) >= 2 and processed_text[0] == '"' and processed_text[-1] == '"':
                        processed_text = processed_text[1:-1]

                    await ws.send_json({"type": "llm_done", "processed_text": processed_text})

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, log_level=config.LOG_LEVEL)
