import logging
import httpx
from config import OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

PROMPTS = {
    "cleanup": (
        "Du bist ein Textbereinigungsassistent. "
        "Bereinige den folgenden transkribierten Text: entferne Füllwörter (ähm, also, halt, sozusagen, quasi), "
        "Versprecher und Wiederholungen. Behalte den Inhalt und die Sprache exakt bei. "
        "Gib NUR den bereinigten Text zurück, ohne Erklärungen.\n\n"
    ),
    "rephrase": (
        "Du bist ein Schreibassistent. "
        "Formuliere den folgenden transkribierten Text klar und professionell um. "
        "Behalte den Inhalt und die Sprache bei, aber verbessere Ausdruck, Struktur und Lesbarkeit. "
        "Gib NUR den umformulierten Text zurück, ohne Erklärungen.\n\n"
    ),
}


def _build_prompt(mode: str, raw_text: str, few_shot_examples: list[dict] | None = None) -> str:
    base = PROMPTS[mode]

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
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
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
            processed = result.get("response", raw_text).strip().strip('"')
            return processed
    except Exception as e:
        logger.error(f"Ollama processing failed: {e}")
        return raw_text


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
