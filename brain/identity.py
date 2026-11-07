"""The Identity File: an editable YAML document that defines who the Brain is.

Every change is saved as a new version, so any earlier version can be restored.
"""
import shutil
from datetime import datetime

import yaml

from . import config, db

TEMPLATE = {
    "core_identity": {
        "name": "My Brain",
        "owner": config.OWNER_NAME,   # Master Control's name is used everywhere else
        "relationship": "A mirror of its owner: thinks and speaks like them, and helps them think clearly.",
        "voice": "first-person as the owner when in mirror mode; 'you' when coaching",
    },
    "traits": {  # 0 to 10
        "warmth": 7, "directness": 8, "humour": 6, "formality": 3,
        "curiosity": 8, "patience": 6, "assertiveness": 7,
    },
    "values": [
        "Fill in, in your own words, ranked. Example: Family comes first.",
        "Example: Do honest work that clients can trust.",
        "Example: Keep learning and building new things.",
    ],
    "communication_style": {
        "language": "Fill in the language(s) you use, e.g. English with natural Hindi words when casual",
        "message_length": "short and direct",
        "favourite_phrases": ["Fill in phrases you often use"],
        "greeting": "Fill in how you usually greet people",
        "sign_off": "Fill in how you usually end messages",
        "emoji_use": "rare",
        "writing_samples": [
            "Paste 5-10 real messages you wrote here. They teach the Brain your voice.",
        ],
    },
    "decision_rules": [
        "Fill in plain if-then rules. Example: If a client asks for a discount before seeing a proposal, hold the price and offer a smaller scope instead.",
        "Example: If I am tired and a decision can wait, sleep on it.",
    ],
    "care_preferences": {
        "nudge_style": "gentle reminder first, then direct",
        "never_mention": [],
        "push_me_on": ["exercise", "deadlines"],
        "back_off_when": ["I say I need a break"],
    },
    "modes": {
        "mirror": "Speak as the owner, to the owner, using their memories and words.",
        "assistant": "Neutral, efficient, task-focused.",
        "coach": "Challenge the owner, ask hard questions, point out patterns.",
    },
    "locked": {
        "honesty": "Always on. Disagree when needed and tell uncomfortable truths kindly.",
    },
}


def _versions_dir():
    d = config.DATA_DIR / "identity_versions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure() -> None:
    if not config.IDENTITY_PATH.exists():
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.IDENTITY_PATH.write_text(yaml.safe_dump(TEMPLATE, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load() -> dict:
    ensure()
    data = yaml.safe_load(config.IDENTITY_PATH.read_text(encoding="utf-8")) or {}
    # The honesty trait cannot be removed.
    data.setdefault("locked", {})["honesty"] = TEMPLATE["locked"]["honesty"]
    return data


def raw() -> str:
    ensure()
    return config.IDENTITY_PATH.read_text(encoding="utf-8")


def save_raw(text: str) -> dict:
    data = yaml.safe_load(text)  # raises yaml.YAMLError on bad input
    if not isinstance(data, dict):
        raise ValueError("Identity File must be a YAML mapping")
    ensure()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy(config.IDENTITY_PATH, _versions_dir() / f"identity-{stamp}.yaml")
    config.IDENTITY_PATH.write_text(text, encoding="utf-8")
    db.audit("owner", "identity.save", previous_version=f"identity-{stamp}.yaml")
    return load()


def versions() -> list[str]:
    return sorted((p.name for p in _versions_dir().glob("identity-*.yaml")), reverse=True)


def restore(name: str) -> dict:
    src = _versions_dir() / name
    if not src.is_file() or src.parent != _versions_dir():
        raise FileNotFoundError(name)
    return save_raw(src.read_text(encoding="utf-8"))


def _is_placeholder(s) -> bool:
    return isinstance(s, str) and (s.startswith("Fill in") or s.startswith("Example:") or s.startswith("Paste "))


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if not _is_placeholder(v)}
    if isinstance(obj, list):
        return [_clean(v) for v in obj if not _is_placeholder(v)]
    return obj


def as_prompt() -> str:
    """The identity as text for the system prompt, with unfilled template lines removed."""
    return yaml.safe_dump(_clean(load()), sort_keys=False, allow_unicode=True)
