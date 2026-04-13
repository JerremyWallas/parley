import logging
from typing import AsyncGenerator
import httpx
from config import OLLAMA_URL, OLLAMA_MODEL
import personalization

logger = logging.getLogger(__name__)


def _get_active_model() -> str:
    """Get the active model from preferences, falling back to config default."""
    prefs = personalization.get_preferences()
    return prefs.get("ollama_model", OLLAMA_MODEL)

DEFAULT_PROMPTS = {
    "cleanup": (
        "Du bist ein Textbereinigungsassistent. "
        "Bereinige den folgenden transkribierten Text: entferne Füllwörter (ähm, also, halt, sozusagen, quasi), "
        "Versprecher und Wiederholungen. Behalte den Inhalt und die Sprache exakt bei. "
        "Gib NUR den bereinigten Text zurück, ohne Erklärungen.\n\n"
    ),
    "rephrase": (
        "Du bist ein Schreibassistent. Du bekommst gesprochenen Text — dieser enthält oft "
        "Satzfragmente, Gedankensprünge, Abbrüche und unvollständige Formulierungen. "
        "Das ist normal bei gesprochener Sprache.\n\n"
        "Deine Aufgabe:\n"
        "1. Verstehe zuerst die KERNAUSSAGE — was will die Person eigentlich sagen?\n"
        "2. Formuliere dann einen klaren, gut lesbaren Text der genau diese Aussage transportiert.\n\n"
        "Regeln:\n"
        "- Behalte die Sprache bei (Deutsch bleibt Deutsch, Englisch bleibt Englisch)\n"
        "- Erfinde keine neuen Inhalte, aber vervollständige offensichtlich abgebrochene Gedanken\n"
        "- Passe den Ton an den Inhalt an (locker wenn es locker klingt, sachlich wenn es sachlich ist)\n"
        "- Gib NUR den fertigen Text zurück, ohne Erklärungen\n\n"
    ),
}


def _get_prompt(mode: str) -> str:
    """Get the prompt for a mode, using custom prompt from preferences if set."""
    prefs = personalization.get_preferences()
    custom_prompts = prefs.get("custom_prompts", {})
    return custom_prompts.get(mode) or DEFAULT_PROMPTS[mode]


def _build_prompt(mode: str, raw_text: str, few_shot_examples: list[dict] | None = None) -> str:
    base = _get_prompt(mode)

    if few_shot_examples:
        base += "Hier sind Beispiele, wie der Nutzer Texte formuliert haben möchte:\n"
        for ex in few_shot_examples:
            base += f'Vorher: "{ex["original"]}"\n'
            base += f'Nachher: "{ex["corrected"]}"\n---\n'
        base += "\nJetzt verarbeite diesen Text im selben Stil:\n"

    base += f'"{raw_text}"'
    return base


async def process_text(
    mode: str,
    raw_text: str,
    few_shot_examples: list[dict] | None = None,
) -> str:
    """Send text to Ollama for cleanup/rephrasing. Returns processed text."""
    if mode == "raw" or not raw_text.strip():
        return raw_text

    prompt = _build_prompt(mode, raw_text, few_shot_examples)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            active_model = _get_active_model()
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": active_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "top_p": 0.9,
                    },
                },
            )
            response.raise_for_status()
            result = response.json()
            processed = result.get("response", raw_text).strip()
            # Remove surrounding quotes if the model wrapped the response
            if len(processed) >= 2 and processed[0] == '"' and processed[-1] == '"':
                processed = processed[1:-1]
            return processed
    except Exception as e:
        logger.error(f"Ollama processing failed: {e}")
        return raw_text


async def process_text_streaming(
    mode: str,
    raw_text: str,
    few_shot_examples: list[dict] | None = None,
):
    """Stream LLM response token by token. Yields text chunks."""
    if mode == "raw" or not raw_text.strip():
        yield raw_text
        return

    prompt = _build_prompt(mode, raw_text, few_shot_examples)

    try:
        active_model = _get_active_model()
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": active_model,
                    "prompt": prompt,
                    "stream": True,
                    "options": {
                        "temperature": 0.3,
                        "top_p": 0.9,
                    },
                },
            ) as response:
                response.raise_for_status()
                import json
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    token = data.get("response", "")
                    if token:
                        yield token
                    if data.get("done", False):
                        break
    except Exception as e:
        logger.error(f"Ollama streaming failed: {e}")
        yield raw_text


async def check_ollama() -> dict:
    """Check if Ollama is reachable and model is available."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            tags = resp.json()
            models = [m["name"] for m in tags.get("models", [])]
            return {"status": "ok", "models": models, "configured_model": OLLAMA_MODEL}
    except Exception as e:
        return {"status": "error", "error": str(e)}
