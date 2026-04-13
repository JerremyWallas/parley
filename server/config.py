import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR.parent / "data"))
MODEL_DIR = Path(os.getenv("MODEL_DIR", "/models"))

# Whisper
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "auto")

# Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "7800"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

# Personalization
MAX_FEW_SHOT_EXAMPLES = int(os.getenv("MAX_FEW_SHOT_EXAMPLES", "5"))
