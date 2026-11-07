"""Back up and restore your whole Brain in one encrypted file.

The backup holds everything in your Brain's folder: memories, chats, identity, journal, skills, settings,
channels, jobs and workspaces. It is encrypted with a passphrase you choose (scrypt + AES), so the file
is safe to keep on Google Drive or a USB stick. Without the passphrase nobody can open it, including us.

Vault passwords and Gmail access are re-encrypted for the computer you restore on, so a backup moves
cleanly between your laptop, Oracle, Google Cloud or the desktop app.
"""
import base64
import io
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from . import config, db, settings

MAGIC = b"AIBRAIN-BACKUP-1\n"
SKIP_DIRS = {"webview", "__pycache__", "lancedb", "downloads"}    # caches that rebuild themselves
SKIP_FILES = {".vault.key", "vault.json.tmp"}
MAX_RESTORE = 2 * 1024 ** 3


class BackupError(RuntimeError):
    pass


def _key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    if len(passphrase or "") < 8:
        raise BackupError("Use a passphrase of at least 8 characters (and keep it somewhere safe).")
    return base64.urlsafe_b64encode(Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode()))


def _secrets_out() -> dict:
    """Vault and Gmail secrets, readable only inside the encrypted backup."""
    from . import vault
    out = {"vault": {}, "gmail": {}}
    f = vault._fernet()
    for name, e in vault._load().items():
        item = {}
        for k in ("value", "username"):
            if e.get(k):
                item[k] = f.decrypt(e[k].encode()).decode()
        out["vault"][name] = item
    g = settings.load().get("gmail") or {}
    for k in ("client_secret", "refresh"):
        if g.get(k):
            out["gmail"][k] = f.decrypt(g[k].encode()).decode()
    return out


def _secrets_in(data: dict) -> None:
    from . import vault
    f = vault._fernet()
    v = vault._load()
    for name, item in (data.get("vault") or {}).items():
        if name in v:
            for k, val in item.items():
                v[name][k] = f.encrypt(val.encode()).decode()
    vault._save(v)
    s = settings.load()
    g = s.get("gmail") or {}
    for k, val in (data.get("gmail") or {}).items():
        g[k] = f.encrypt(val.encode()).decode()
    if g:
        s["gmail"] = g
        settings.save(s)


def make(passphrase: str) -> bytes:
    salt = os.urandom(16)
    key = _key(passphrase, salt)
    root = config.DATA_DIR
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if path.is_dir() or rel.parts[0] in SKIP_DIRS or path.name in SKIP_FILES or path.suffix in (".tmp", ".lock"):
                continue
            if path.suffix == ".db":                 # a consistent copy even while the Brain is running
                import sqlite3
                tmp = Path(tempfile.mkstemp(suffix=".db")[1])
                try:
                    src = sqlite3.connect(path)
                    dst = sqlite3.connect(tmp)
                    src.backup(dst)
                    src.close()
                    dst.close()
                    z.write(tmp, str(rel).replace("\\", "/"))
                finally:
                    tmp.unlink(missing_ok=True)
            else:
                z.write(path, str(rel).replace("\\", "/"))
        z.writestr("_secrets.json", json.dumps(_secrets_out()))
        z.writestr("_backup.json", json.dumps({"made": time.strftime("%Y-%m-%d %H:%M"), "version": 1}))
    from cryptography.fernet import Fernet
    token = Fernet(key).encrypt(buf.getvalue())
    db.audit("owner", "backup.make", bytes=len(token))
    return MAGIC + base64.b64encode(salt) + b"\n" + token


def restore(blob: bytes, passphrase: str) -> dict:
    from cryptography.fernet import Fernet, InvalidToken
    if not blob.startswith(MAGIC):
        raise BackupError("That isn't an AI Brain backup file.")
    if len(blob) > MAX_RESTORE:
        raise BackupError("That backup is too big.")
    salt_line, token = blob[len(MAGIC):].split(b"\n", 1)
    try:
        raw = Fernet(_key(passphrase, base64.b64decode(salt_line))).decrypt(token)
    except InvalidToken:
        raise BackupError("Wrong passphrase, or the file is damaged.")
    root = config.DATA_DIR.resolve()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        if "_backup.json" not in names:
            raise BackupError("The backup is incomplete.")
        stage = Path(tempfile.mkdtemp(prefix="brain-restore-"))
        try:
            for n in names:
                if n.startswith("_"):
                    continue
                dest = (stage / n).resolve()
                if not dest.is_relative_to(stage.resolve()) or Path(n).name in SKIP_FILES:
                    raise BackupError("The backup contains an unsafe path.")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(n))
            secrets = json.loads(z.read("_secrets.json")) if "_secrets.json" in names else {}
            safety = root.parent / f"{root.name}-before-restore-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copytree(root, safety, ignore=shutil.ignore_patterns(*SKIP_DIRS))   # undo point
            for item in stage.iterdir():
                target = root / item.name
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                elif target.exists():
                    target.unlink()
                shutil.move(str(item), str(target))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    _secrets_in(secrets)
    db.init()
    db.audit("owner", "backup.restore", files=len(names))
    return {"files": len(names) - 2, "previous_copy": safety.name}
