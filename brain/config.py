"""Settings, read from environment variables or a .env file in the project root."""
import contextvars
import os
import threading
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

# Where everything is stored. On a laptop (one owner) that is data/. On a server with accounts
# (BRAIN_MULTIUSER=1) every user has their own folder, data/users/<id>/, chosen per request.
ROOT_DATA = Path(os.environ.get("BRAIN_DATA_DIR", ROOT / "data"))
MULTIUSER = os.environ.get("BRAIN_MULTIUSER", "") == "1"
_user_dir: contextvars.ContextVar = contextvars.ContextVar("brain_user_dir", default=None)


def data_dir() -> Path:
    return _user_dir.get() or ROOT_DATA


_user_role: contextvars.ContextVar = contextvars.ContextVar("brain_user_role", default="admin")


def set_role(role: str):
    return _user_role.set(role)


def allow_private() -> bool:
    """May this user's Brain reach addresses on the server's own network (Ollama, local gateways)?
    Always on a laptop; on a shared server only for the admin."""
    return not MULTIUSER or _user_role.get() == "admin"


def user_key() -> str:
    """Identifies whose data this is (used to keep tasks, sessions and limits apart)."""
    return str(data_dir())


def use_dir(path: Path | None):
    """Switch the current request or thread to a user's folder. Returns a token for reset_dir()."""
    return _user_dir.set(Path(path) if path else None)


def reset_dir(token) -> None:
    _user_dir.reset(token)


def spawn(target, *args) -> threading.Thread:
    """Start a background thread that keeps working on the same user's data."""
    ctx = contextvars.copy_context()
    t = threading.Thread(target=ctx.run, args=(target, *args), daemon=True)
    t.start()
    return t


def __getattr__(name):           # config.DATA_DIR etc. always point at the current user's folder
    if name == "DATA_DIR":
        return data_dir()
    if name == "DB_PATH":
        return data_dir() / "brain.db"
    if name == "IDENTITY_PATH":
        return data_dir() / "identity.yaml"
    raise AttributeError(name)


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
