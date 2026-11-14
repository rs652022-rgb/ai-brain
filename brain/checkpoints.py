"""Undo for the Brain's code edits (like Hermes' checkpoints).

Before the Brain writes or patches files in a code workspace, the files it is about to touch are copied
to data/checkpoints/<id>/. Restoring a checkpoint puts each file back exactly as it was (and removes
files that didn't exist yet), one step at a time, without touching your other changes.
Only the newest MAX_KEEP checkpoints are kept.
"""
import json
import re
import secrets
import shutil
from pathlib import Path

from . import config, db

MAX_KEEP = 50
MAX_FILE = 2_000_000          # bigger files aren't copied (and can't be undone this way)
ID_RE = re.compile(r"^[0-9]{14}-[0-9a-f]{6}$")


class CheckpointError(RuntimeError):
    pass


def root() -> Path:
    d = config.DATA_DIR / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def patch_paths(patch: str) -> list[str]:
    """Files a unified diff touches (old and new names)."""
    out = []
    for line in (patch or "").splitlines():
        if line.startswith(("+++ ", "--- ")):
            target = line[4:].strip().split("\t")[0]
            if target != "/dev/null":
                rel = re.sub(r"^[ab]/", "", target)
                if rel not in out:
                    out.append(rel)
    return out


def save(workspace: str, paths: list[str], reason: str = "") -> str | None:
    """Copy the files before they change. Returns the checkpoint id (None when there was nothing to copy)."""
    from . import workspaces as w
    ws = w._ws(workspace)
    paths = [p for p in dict.fromkeys(paths) if p][:50]
    if not paths:
        return None
    cid = db.now().replace("-", "").replace(":", "").replace("T", "")[:14].ljust(14, "0") + "-" + secrets.token_hex(3)
    folder = root() / cid
    (folder / "files").mkdir(parents=True)
    files = []
    for i, rel in enumerate(paths):
        p = w._path(ws, rel)
        entry = {"path": rel, "existed": p.is_file(), "copy": ""}
        if p.is_file():
            if p.stat().st_size > MAX_FILE:
                entry["skipped"] = "too big"
            else:
                entry["copy"] = f"{i}.bin"
                shutil.copy2(p, folder / "files" / entry["copy"])
        files.append(entry)
    (folder / "meta.json").write_text(json.dumps({"id": cid, "workspace": workspace, "reason": reason[:200],
                                                  "created_at": db.now(), "files": files}), encoding="utf-8")
    _prune()
    return cid


def _meta(cid: str) -> dict:
    if not ID_RE.match(cid or ""):
        raise CheckpointError("Unknown checkpoint.")
    f = root() / cid / "meta.json"
    if not f.is_file():
        raise CheckpointError("Unknown checkpoint.")
    return json.loads(f.read_text(encoding="utf-8"))


def list_all(workspace: str = "", limit: int = 30) -> list[dict]:
    out = []
    for d in sorted(root().iterdir(), reverse=True):
        if not (d.is_dir() and ID_RE.match(d.name)):
            continue
        try:
            m = _meta(d.name)
        except (CheckpointError, ValueError):
            continue
        if workspace and m["workspace"] != workspace:
            continue
        out.append({k: m[k] for k in ("id", "workspace", "reason", "created_at")} | {"files": [f["path"] for f in m["files"]]})
        if len(out) >= limit:
            break
    return out


def restore(cid: str, actor: str = "owner") -> list[str]:
    """Put every file in the checkpoint back. Returns the paths restored."""
    from . import workspaces as w
    m = _meta(cid)
    ws = w._ws(m["workspace"])
    done = []
    for f in m["files"]:
        if f.get("skipped"):
            continue
        p = w._path(ws, f["path"])
        if f["existed"]:
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root() / cid / "files" / f["copy"], p)
        elif p.is_file():
            p.unlink()
        done.append(f["path"])
    db.audit(actor, "checkpoint.restore", checkpoint=cid, files=len(done))
    return done


def _prune() -> None:
    dirs = sorted((d for d in root().iterdir() if d.is_dir() and ID_RE.match(d.name)), reverse=True)
    for d in dirs[MAX_KEEP:]:
        shutil.rmtree(d, ignore_errors=True)
