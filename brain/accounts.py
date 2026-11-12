"""Accounts for running the Brain on a server (BRAIN_MULTIUSER=1): sign up, log in, admin.

Every user gets a private Brain in data/users/<id>/ with its own memories, settings, API keys,
identity and activity log. Nothing is shared between users.

Passwords are stored only as scrypt hashes. Sessions are random tokens; only their hashes are
stored. The first person to sign up becomes the admin, who can close sign-ups and manage users.
Password-reset emails work when SMTP is set (free: Gmail with an app password, or Brevo).
"""
import hashlib
import hmac
import os
import re
import secrets
import shutil
import smtplib
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from . import config

SESSION_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_lock = threading.Lock()
_ready: set[str] = set()
# Failed logins and sign-ups are counted in data/security.db (brain/ratelimit.py), so restarts don't reset them.


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db_path() -> Path:
    return config.ROOT_DATA / "accounts.db"


@contextmanager
def _conn():
    config.ROOT_DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name TEXT NOT NULL,
            pw_hash TEXT NOT NULL,
            pw_salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            disabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_login TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires REAL NOT NULL,
            created REAL NOT NULL,
            agent TEXT
        );
        CREATE TABLE IF NOT EXISTS resets (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS companions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created TEXT NOT NULL,
            last_seen TEXT
        );
        CREATE TABLE IF NOT EXISTS pairing (
            code TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,              -- 0 = the owner of a laptop Brain
            name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created TEXT NOT NULL,
            last_used TEXT
        );
        CREATE TABLE IF NOT EXISTS push_subs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            sub TEXT NOT NULL,
            created TEXT NOT NULL
        );
        """)


# ---------- Helpers ----------

def _hash_pw(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _public(row) -> dict:
    return {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"],
            "disabled": bool(row["disabled"]), "created_at": row["created_at"], "last_login": row["last_login"]}


def _check_password(pw: str) -> None:
    if len(pw or "") < 8:
        raise AuthError("Use a password with at least 8 characters.")
    if len(pw) > 200:
        raise AuthError("That password is too long.")


def _too_many(key: str, limit: int = 5, window: float = 300) -> bool:
    from . import ratelimit
    return ratelimit.count(f"login:{key}", window) >= limit


def _failed(*keys: str) -> None:
    from . import ratelimit
    ratelimit.hit(*(f"login:{k}" for k in keys))


def get_setting(key: str, default: str = "") -> str:
    with _conn() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _conn() as c:
        c.execute("INSERT INTO app_settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, value))


def signup_open() -> bool:
    if not user_count():
        return True                          # the first person always can (they become the admin)
    return get_setting("signup", os.environ.get("BRAIN_SIGNUP", "open")) == "open"


def user_count() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# ---------- Sign up, log in, log out ----------

def signup(email: str, password: str, name: str, ip: str = "") -> dict:
    email = (email or "").strip().lower()
    name = re.sub(r"\s+", " ", (name or "").strip())[:60]
    if not EMAIL_RE.match(email) or len(email) > 200:
        raise AuthError("Enter a valid email address.")
    if not name:
        raise AuthError("Enter your name.")
    _check_password(password)
    if not signup_open():
        raise AuthError("Sign-ups are closed. Ask the admin to invite you.", 403)
    from . import ratelimit
    if ip and not ratelimit.allow(f"signup:{ip}", 5, 3600):
        raise AuthError("Too many sign-ups from here. Try again later.", 429)
    salt = secrets.token_hex(16)
    with _lock, _conn() as c:
        first = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        try:
            cur = c.execute("INSERT INTO users(email, name, pw_hash, pw_salt, role, created_at) VALUES(?,?,?,?,?,?)",
                            (email, name, _hash_pw(password, salt), salt, "admin" if first else "user", _now()))
        except sqlite3.IntegrityError:
            raise AuthError("An account with this email already exists. Log in instead.", 409)
        row = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return _public(row)


def login(email: str, password: str, ip: str = "", agent: str = "") -> tuple[str, dict]:
    email = (email or "").strip().lower()
    if _too_many(f"email:{email}") or (ip and _too_many(f"ip:{ip}", limit=20)):
        raise AuthError("Too many wrong tries. Wait a few minutes and try again.", 429)
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    dummy = "00" * 16
    ok = bool(row) and hmac.compare_digest(_hash_pw(password or "", row["pw_salt"]), row["pw_hash"])
    if not row:
        _hash_pw(password or "", dummy)       # same time whether or not the email exists
    if not ok:
        _failed(f"email:{email}", f"ip:{ip}")
        raise AuthError("Wrong email or password.", 401)
    if row["disabled"]:
        raise AuthError("This account is switched off. Contact the admin.", 403)
    token = secrets.token_urlsafe(32)
    with _conn() as c:
        c.execute("INSERT INTO sessions(token_hash, user_id, expires, created, agent) VALUES(?,?,?,?,?)",
                  (_token_hash(token), row["id"], time.time() + SESSION_DAYS * 86400, time.time(), (agent or "")[:200]))
        c.execute("UPDATE users SET last_login=? WHERE id=?", (_now(), row["id"]))
        c.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
    return token, _public(row)


def user_for_token(token: str | None) -> dict | None:
    if not token:
        return None
    with _conn() as c:
        row = c.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                           WHERE s.token_hash=? AND s.expires > ? AND u.disabled = 0""",
                        (_token_hash(token), time.time())).fetchone()
    return _public(row) if row else None


def logout(token: str | None) -> None:
    if token:
        with _conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def logout_everywhere(user_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def change_password(user_id: int, old: str, new: str, keep_token: str | None = None) -> None:
    _check_password(new)
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or not hmac.compare_digest(_hash_pw(old or "", row["pw_salt"]), row["pw_hash"]):
            raise AuthError("Your current password is wrong.", 401)
        salt = secrets.token_hex(16)
        c.execute("UPDATE users SET pw_hash=?, pw_salt=? WHERE id=?", (_hash_pw(new, salt), salt, user_id))
        keep = _token_hash(keep_token) if keep_token else ""
        c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash != ?", (user_id, keep))


def _set_password(user_id: int, new: str) -> None:
    salt = secrets.token_hex(16)
    with _conn() as c:
        c.execute("UPDATE users SET pw_hash=?, pw_salt=? WHERE id=?", (_hash_pw(new, salt), salt, user_id))
        c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


# ---------- Forgotten password ----------

def smtp_ready() -> bool:
    return bool(os.environ.get("BRAIN_SMTP_HOST") and os.environ.get("BRAIN_SMTP_FROM"))


def _send_mail(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["BRAIN_SMTP_FROM"], to, subject
    msg.set_content(body)
    port = int(os.environ.get("BRAIN_SMTP_PORT", "587"))
    with smtplib.SMTP(os.environ["BRAIN_SMTP_HOST"], port, timeout=20) as s:
        if port != 25:
            s.starttls()
        if os.environ.get("BRAIN_SMTP_USER"):
            s.login(os.environ["BRAIN_SMTP_USER"], os.environ.get("BRAIN_SMTP_PASSWORD", ""))
        s.send_message(msg)


def forgot(email: str, base_url: str) -> None:
    """Email a reset link if the account exists. Says nothing either way (no account probing)."""
    if not smtp_ready():
        raise AuthError("Password reset by email isn't set up on this server. Ask the admin to reset it for you.", 501)
    email = (email or "").strip().lower()
    if _too_many(f"reset:{email}", limit=3, window=3600):
        return
    _failed(f"reset:{email}")
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email=? AND disabled=0", (email,)).fetchone()
        if not row:
            return
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO resets(token_hash, user_id, expires) VALUES(?,?,?)",
                  (_token_hash(token), row["id"], time.time() + 3600))
    _send_mail(email, "Reset your Brain password",
               f"Hi {row['name']},\n\nOpen this link within an hour to choose a new password:\n"
               f"{base_url.rstrip('/')}/#reset={token}\n\nIf you didn't ask for this, ignore this email.")


def reset_password(token: str, new: str) -> None:
    _check_password(new)
    with _conn() as c:
        row = c.execute("SELECT * FROM resets WHERE token_hash=? AND expires > ?", (_token_hash(token or ""), time.time())).fetchone()
        if not row:
            raise AuthError("That reset link has expired. Ask for a new one.", 400)
        c.execute("DELETE FROM resets WHERE user_id=?", (row["user_id"],))
    _set_password(row["user_id"], new)


# ---------- Each user's own Brain ----------

def user_dir(user: dict) -> Path:
    return config.ROOT_DATA / "users" / str(int(user["id"]))


def prepare(user: dict) -> Path:
    """Create the user's private Brain the first time, and return its folder."""
    d = user_dir(user)
    key = str(d)
    if key in _ready:
        return d
    with _lock:
        if key in _ready:
            return d
        d.mkdir(parents=True, exist_ok=True)
        tok = config.use_dir(d)
        try:
            from . import db, identity, knowledge, master, settings
            new = not (d / "settings.json").exists()
            db.init()
            knowledge.init()
            identity.ensure()
            if new:
                data = settings.load()
                data["master"] = master.get() | {"owner_name": user["name"]}
                settings.save(data)
        finally:
            config.reset_dir(tok)
        _ready.add(key)
    return d


def delete_account(user_id: int) -> None:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise AuthError("No such user.", 404)
        if row["role"] == "admin" and c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 1 \
                and c.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 1:
            raise AuthError("You're the only admin. Make someone else admin first.", 400)
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
    d = config.ROOT_DATA / "users" / str(int(user_id))
    _ready.discard(str(d))
    for _ in range(10):                       # background work may still be closing files: try until it's gone
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            break
        time.sleep(0.2)


# ---------- Admin ----------

def list_users() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [_public(r) for r in rows]


def admin_update(user_id: int, changes: dict, by: dict) -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise AuthError("No such user.", 404)
        if "disabled" in changes:
            if user_id == by["id"]:
                raise AuthError("You can't switch off your own account.")
            c.execute("UPDATE users SET disabled=? WHERE id=?", (1 if changes["disabled"] else 0, user_id))
            if changes["disabled"]:
                c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        if changes.get("role") in ("admin", "user"):
            if user_id == by["id"] and changes["role"] == "user":
                raise AuthError("You can't remove your own admin rights.")
            c.execute("UPDATE users SET role=? WHERE id=?", (changes["role"], user_id))
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _public(row)


def admin_reset(user_id: int) -> str:
    """Set a temporary password the admin passes on. The user should change it after logging in."""
    temp = "-".join(secrets.token_hex(2) for _ in range(3))
    _set_password(user_id, temp)
    return temp


# ---------- Companion apps (device control from the cloud) ----------

PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def pairing_code(user_id: int) -> str:
    code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(8))
    with _conn() as c:
        c.execute("DELETE FROM pairing WHERE user_id=? OR expires < ?", (user_id, time.time()))
        c.execute("INSERT INTO pairing(code, user_id, expires) VALUES(?,?,?)", (code, user_id, time.time() + 600))
    return code


def pair(code: str, name: str, ip: str = "") -> tuple[str, dict]:
    code = re.sub(r"[\s-]", "", (code or "").upper())
    if _too_many(f"pair:{ip}", limit=10, window=600):
        raise AuthError("Too many tries. Wait a few minutes.", 429)
    with _conn() as c:
        row = c.execute("SELECT * FROM pairing WHERE code=? AND expires > ?", (code, time.time())).fetchone()
        if not row:
            _failed(f"pair:{ip}")
            raise AuthError("That code is wrong or has expired. Make a new one in Control.", 401)
        c.execute("DELETE FROM pairing WHERE code=?", (code,))
        token = secrets.token_urlsafe(40)
        c.execute("INSERT INTO companions(user_id, name, token_hash, created) VALUES(?,?,?,?)",
                  (row["user_id"], re.sub(r"\s+", " ", name or "My computer").strip()[:60], _token_hash(token), _now()))
        user = c.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
    return token, _public(user)


def companion_user(token: str) -> tuple[dict, dict] | None:
    with _conn() as c:
        row = c.execute("""SELECT c.id AS cid, c.name AS cname, u.* FROM companions c JOIN users u ON u.id=c.user_id
                           WHERE c.token_hash=? AND u.disabled=0""", (_token_hash(token or ""),)).fetchone()
        if not row:
            return None
        c.execute("UPDATE companions SET last_seen=? WHERE id=?", (_now(), row["cid"]))
    return _public(row), {"id": row["cid"], "name": row["cname"]}


def companions(user_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT id, name, created, last_seen FROM companions WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def remove_companion(user_id: int, cid: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM companions WHERE id=? AND user_id=?", (cid, user_id))


# ---------- Personal API tokens (CLI, OpenAI-compatible API, other apps) ----------

def make_token(user_id: int, name: str) -> tuple[str, dict]:
    token = "brain_" + secrets.token_urlsafe(32)
    with _conn() as c:
        cur = c.execute("INSERT INTO api_tokens(user_id, name, token_hash, created) VALUES(?,?,?,?)",
                        (user_id, (name or "API token")[:60], _token_hash(token), _now()))
        row = c.execute("SELECT id, name, created, last_used FROM api_tokens WHERE id=?", (cur.lastrowid,)).fetchone()
    return token, dict(row)


def tokens(user_id: int) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT id, name, created, last_used FROM api_tokens WHERE user_id=? ORDER BY id",
                                           (user_id,)).fetchall()]


def drop_token(user_id: int, tid: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM api_tokens WHERE id=? AND user_id=?", (tid, user_id))


def token_user(token: str) -> tuple[dict | None, int] | None:
    """(user or None for the laptop owner, token id) for a valid API token."""
    if not (token or "").startswith("brain_"):
        return None
    with _conn() as c:
        row = c.execute("SELECT * FROM api_tokens WHERE token_hash=?", (_token_hash(token),)).fetchone()
        if not row:
            return None
        c.execute("UPDATE api_tokens SET last_used=? WHERE id=?", (_now(), row["id"]))
        if row["user_id"] == 0:
            return None, row["id"]
        u = c.execute("SELECT * FROM users WHERE id=? AND disabled=0", (row["user_id"],)).fetchone()
    return (_public(u), row["id"]) if u else None
