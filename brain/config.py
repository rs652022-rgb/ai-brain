"""Settings, read from environment variables or a .env file in the project root."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()

DATA_DIR = Path(os.environ.get("BRAIN_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "brain.db"
IDENTITY_PATH = DATA_DIR / "identity.yaml"
SAFETY_CORE_PATH = ROOT / "brain" / "safety_core.md"

OWNER_NAME = os.environ.get("BRAIN_OWNER_NAME", "Rahul Sharma")

# Providers, keys and models are set in the Settings tab (stored in data/settings.json).
# These are only defaults for the local Ollama model.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "llama3.2")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# How many memories to pull into each conversation
RECALL_LIMIT = int(os.environ.get("BRAIN_RECALL_LIMIT", "8"))

# Sensitivity labels, from least to most private.
# "sealed" memories are only used when the owner starts a conversation, never in actions.
SENSITIVITY_LEVELS = ["public", "personal", "private", "sealed"]
MEMORY_TYPES = ["episodic", "semantic", "procedural", "working"]
