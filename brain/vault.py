"""Secrets vault: the Brain can use a password without ever seeing it.

You save a secret (or point to one in 1Password or Bitwarden) and list the sites it may be used on.
The Brain only knows the secret's NAME and writes a placeholder like {{secret:github}}. The real value is
filled in at the very last moment, inside the browser (or your Chrome extension), and only when the page
is on one of the allowed sites. It never goes into the model's prompt, the chat, memories or logs.
Every use is logged and sends you a notification.

Storage: values are encrypted (Fernet / AES) with a key kept outside your data folder.
1Password and Bitwarden: uses their official command-line tools (`op`, `bw`) on your own computer,
which must be signed in; nothing is copied into the Brain.
"""
import json
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse

from . import config, db

PLACEHOLDER = re.compile(r"\{\{secret:([a-z0-9_.-]{1,40})(?::(username|password|value))?\}\}")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")
_lock = threading.Lock()


class VaultError(RuntimeError):
    pass


def _fernet():
    from cryptography.fernet import Fernet
    kf = config.ROOT_DATA / ".vault.key"
    if not kf.exists():
        kf.parent.mkdir(parents=True, exist_ok=True)
        kf.write_bytes(Fernet.generate_key())
        try:
            kf.chmod(0o600)
        except OSError:
            pass
    return Fernet(kf.read_bytes().strip())


def _file():
    return config.DATA_DIR / "vault.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(f)


def _clean_sites(sites) -> list[str]:
    if isinstance(sites, str):
        sites = re.split(r"[\s,]+", sites)
    out = []
    for s in sites or []:
        s = s.strip().lower()
        s = urlparse(s).hostname or s if "://" in s else s.split("/")[0]
        s = s.lstrip("*.").strip()
        if s and re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", s):
            out.append(s)
    return list(dict.fromkeys(out))[:20]


def listing() -> list[dict]:
    """What the owner (and the Brain) may see: names, kinds and sites, never values."""
    return [{"name": n, "source": e["source"], "sites": e["sites"], "has_username": bool(e.get("username")),
             "note": e.get("note", ""), "uses": e.get("uses", 0), "last_used": e.get("last_used", "")}
            for n, e in sorted(_load().items())]


def save(name: str, sites, value: str = "", username: str = "", source: str = "local", ref: str = "", note: str = "") -> dict:
    name = (name or "").strip().lower()
    if not NAME_RE.match(name):
        raise VaultError("Names use small letters, numbers, - _ and . (e.g. github).")
    sites = _clean_sites(sites)
    if not sites:
        raise VaultError("Add at least one site where it may be used (e.g. github.com).")
    if source not in ("local", "1password", "bitwarden"):
        raise VaultError("Source is local, 1password or bitwarden.")
    if source != "local":
        if config.MULTIUSER:
            raise VaultError("1Password and Bitwarden work in the app on your own computer, not on a shared server.")
        if source == "1password" and not ref.startswith("op://"):
            raise VaultError("Use a 1Password secret reference like op://Private/GitHub/password.")
        if source == "bitwarden" and not ref.strip():
            raise VaultError("Give the Bitwarden item name or ID.")
    with _lock:
        data = _load()
        old = data.get(name, {})
        f = _fernet()
        entry = {"source": source, "sites": sites, "note": note[:200], "uses": old.get("uses", 0),
                 "last_used": old.get("last_used", ""), "ref": ref.strip()[:300]}
        if source == "local":
            if value:
                entry["value"] = f.encrypt(value.encode()).decode()
            elif old.get("value"):
                entry["value"] = old["value"]
            else:
                raise VaultError("Enter the secret.")
        if username:
            entry["username"] = f.encrypt(username.encode()).decode()
        elif old.get("username"):
            entry["username"] = old["username"]
        data[name] = entry
        _save(data)
    db.audit("owner", "vault.save", name=name, source=source, sites=sites)
    return next(x for x in listing() if x["name"] == name)


def delete(name: str) -> None:
    with _lock:
        data = _load()
        data.pop(name, None)
        _save(data)
    db.audit("owner", "vault.delete", name=name)


def site_ok(entry_sites: list[str], url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host or urlparse(url).scheme != "https":
        return False                                    # secrets only go to https pages
    return any(host == s or host.endswith("." + s) for s in entry_sites)


def _external(entry: dict, part: str) -> str:
    if entry["source"] == "1password":
        if not shutil.which("op"):
            raise VaultError("The 1Password CLI (op) isn't installed or signed in on this computer.")
        ref = entry["ref"]
        if part == "username":
            ref = ref.rsplit("/", 1)[0] + "/username"
        r = subprocess.run(["op", "read", "--no-newline", ref], capture_output=True, text=True, timeout=30)
    else:
        if not shutil.which("bw"):
            raise VaultError("The Bitwarden CLI (bw) isn't installed or unlocked on this computer.")
        r = subprocess.run(["bw", "get", "username" if part == "username" else "password", entry["ref"]],
                           capture_output=True, text=True, timeout=30)
    if r.returncode or not r.stdout:
        raise VaultError(f"{entry['source']} didn't give the secret (is it signed in / unlocked?).")
    return r.stdout.rstrip("\n")


def _value(name: str, entry: dict, part: str) -> str:
    if part == "username":
        if entry.get("username"):
            return _fernet().decrypt(entry["username"].encode()).decode()
        if entry["source"] == "local":
            raise VaultError(f"{name} has no username saved.")
    if entry["source"] == "local":
        return _fernet().decrypt(entry["value"].encode()).decode()
    return _external(entry, part)


def has_placeholder(text: str) -> bool:
    return bool(PLACEHOLDER.search(text or ""))


def resolve(text: str, url: str, where: str = "browser") -> tuple[str, list[str]]:
    """Fill placeholders for a page at url. Returns (filled text, the secret values used, for scrubbing)."""
    data = _load()
    used: list[str] = []

    def rep(m):
        name, part = m.group(1), m.group(2) or "password"
        entry = data.get(name)
        if not entry:
            raise VaultError(f"There's no secret called {name} in the vault.")
        if not site_ok(entry["sites"], url):
            db.audit("brain", "vault.blocked", name=name, host=urlparse(url).hostname or "?", where=where)
            raise VaultError(f"The secret {name} may only be used on {', '.join(entry['sites'])} over https, "
                             f"not on {urlparse(url).hostname}.")
        val = _value(name, entry, part)
        used.append(val)
        _mark_used(name, url, where, part)
        return val

    return PLACEHOLDER.sub(rep, text), used


def _mark_used(name: str, url: str, where: str, part: str) -> None:
    host = urlparse(url).hostname or "?"
    with _lock:
        data = _load()
        if name in data:
            data[name]["uses"] = data[name].get("uses", 0) + 1
            data[name]["last_used"] = time.strftime("%Y-%m-%d %H:%M")
            _save(data)
    db.audit("brain", "vault.use", name=name, part=part, host=host, where=where)
    try:
        from . import push
        push.notify_async("🔑 Secret used", f"The Brain filled in {name} ({part}) on {host}.", "/#settings")
    except Exception:
        pass


def scrub(text: str, values: list[str]) -> str:
    for v in sorted(set(values), key=len, reverse=True):
        if v and len(v) >= 3:
            text = text.replace(v, "[secret]")
    return text


def prompt_block() -> str:
    items = listing()
    if not items:
        return ""
    return ("\nSecrets vault: to type a password or username, write the placeholder, never ask for the value. "
            "It is filled in only on its allowed sites:\n"
            + "\n".join(f"- {{{{secret:{x['name']}}}}} (username: {{{{secret:{x['name']}:username}}}}) on {', '.join(x['sites'])}"
                        for x in items[:30]))
