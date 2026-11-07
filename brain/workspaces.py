"""Coding workspaces (like OpenClaw's apply_patch, code mode and managed git worktrees).

A workspace is a git repository the Brain may work in: data/workspaces/<name>. The Brain can read and
search files, apply patches (unified diffs), write files, and make a separate branch (git worktree) for each
task. You review every change in the diff viewer and decide to keep (commit) or throw it away.
The Brain never pushes; you do that yourself.
"""
import re
import shutil
import subprocess
from pathlib import Path

from . import config, db, settings

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


class WorkspaceError(RuntimeError):
    pass


def root() -> Path:
    d = config.DATA_DIR / "workspaces"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _git(cwd: Path, *args, timeout=120, input_text=None) -> str:
    if not shutil.which("git"):
        raise WorkspaceError("Git isn't installed on this computer (git-scm.com).")
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, input=input_text,
                       env={"GIT_TERMINAL_PROMPT": "0", "PATH": __import__("os").environ.get("PATH", ""),
                            "HOME": str(config.ROOT_DATA), "GIT_AUTHOR_NAME": "Brain", "GIT_AUTHOR_EMAIL": "brain@localhost",
                            "GIT_COMMITTER_NAME": "Brain", "GIT_COMMITTER_EMAIL": "brain@localhost",
                            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
                       creationflags=0x08000000 if __import__("os").name == "nt" else 0)
    if r.returncode:
        raise WorkspaceError((r.stderr or r.stdout).strip()[-500:] or f"git {args[0]} failed")
    return r.stdout


def _ws(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise WorkspaceError("Workspace names use small letters, numbers, - and _.")
    d = root() / name
    if not (d / ".git").exists():
        raise WorkspaceError(f"No workspace called {name}.")
    return d


def _path(ws: Path, rel: str) -> Path:
    p = (ws / (rel or "").lstrip("/\\")).resolve()
    if not p.is_relative_to(ws.resolve()) or ".git" in p.relative_to(ws.resolve()).parts:
        raise WorkspaceError("That path is outside the workspace.")
    return p


def list_all() -> list[dict]:
    out = []
    for d in sorted(root().iterdir()):
        if (d / ".git").exists():
            try:
                branch = _git(d, "branch", "--show-current").strip()
                changed = len([l for l in _git(d, "status", "--porcelain").splitlines() if l.strip()])
            except WorkspaceError:
                branch, changed = "?", 0
            out.append({"name": d.name, "branch": branch, "changed": changed})
    return out


def create(name: str, url: str = "") -> dict:
    if not NAME_RE.match(name or ""):
        raise WorkspaceError("Workspace names use small letters, numbers, - and _.")
    d = root() / name
    if d.exists():
        raise WorkspaceError("That workspace already exists.")
    if url:
        if not re.match(r"^https://", url) or not settings.url_allowed(url):
            raise WorkspaceError("Clone from a public https:// address (for private repos include a token in the URL).")
        from . import web
        web.check_url(url)
        _git(root(), "clone", "--depth", "50", url, name, timeout=600)
    else:
        d.mkdir()
        _git(d, "init", "-q", "-b", "main")
        (d / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "Start")
    db.audit("owner", "workspace.create", name=name, cloned=bool(url))
    return next(w for w in list_all() if w["name"] == name)


def delete(name: str) -> None:
    d = _ws(name)
    shutil.rmtree(d, ignore_errors=True)
    db.audit("owner", "workspace.delete", name=name)


def tree(name: str, limit: int = 300) -> str:
    return "\n".join(_git(_ws(name), "ls-files").splitlines()[:limit]) or "(empty)"


def read(name: str, path: str) -> str:
    p = _path(_ws(name), path)
    if not p.is_file():
        raise WorkspaceError(f"{path} isn't a file.")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(f"{i:>5}  {l}" for i, l in enumerate(lines[:1500], 1)) + ("\n…" if len(lines) > 1500 else "")


def search(name: str, pattern: str) -> str:
    try:
        return _git(_ws(name), "grep", "-n", "-I", "-e", pattern, "--", ".")[:6000] or "No matches."
    except WorkspaceError as exc:
        return "No matches." if "exit" in str(exc) or not str(exc) else str(exc)


def write(name: str, path: str, content: str) -> str:
    p = _path(_ws(name), path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    db.audit("brain", "workspace.write", name=name, path=path)
    return f"Wrote {path} ({len(content)} characters)."


def apply_patch(name: str, patch: str) -> str:
    """Apply a unified diff (checked first; nothing changes if any part doesn't fit)."""
    ws = _ws(name)
    if not patch.strip():
        raise WorkspaceError("Empty patch.")
    for line in patch.splitlines():
        if line.startswith(("+++ ", "--- ")):
            target = line[4:].strip().split("\t")[0]
            if target != "/dev/null":
                _path(ws, re.sub(r"^[ab]/", "", target))
    patch = patch if patch.endswith("\n") else patch + "\n"
    _git(ws, "apply", "--check", "--recount", "--whitespace=nowarn", "-", input_text=patch)
    _git(ws, "apply", "--recount", "--whitespace=nowarn", "-", input_text=patch)
    db.audit("brain", "workspace.patch", name=name, lines=patch.count("\n"))
    return "Patch applied. The owner can review it in the diff viewer."


def branch(name: str, branch_name: str) -> str:
    """A separate branch in its own folder (git worktree), so tasks don't step on each other."""
    ws = _ws(name)
    b = re.sub(r"[^a-z0-9/_-]", "-", branch_name.lower())[:40] or "task"
    dest = root() / f"{name}--{b.replace('/', '-')}"
    if dest.exists():
        raise WorkspaceError("That branch workspace already exists.")
    _git(ws, "worktree", "add", "-b", b, str(dest))
    db.audit("brain", "workspace.branch", name=name, branch=b)
    return f"Made branch {b} as workspace {dest.name}."


def diff(name: str) -> str:
    ws = _ws(name)
    _git(ws, "add", "-A", "--intent-to-add")
    return _git(ws, "diff", "--no-color", "--stat", "--patch")[:200000]


def commit(name: str, message: str) -> str:
    ws = _ws(name)
    _git(ws, "add", "-A")
    if not _git(ws, "status", "--porcelain").strip():
        return "Nothing to keep."
    _git(ws, "commit", "-q", "-m", (message or "Changes from Brain")[:200])
    db.audit("owner", "workspace.commit", name=name)
    return "Kept (committed)."


def discard(name: str) -> str:
    ws = _ws(name)
    _git(ws, "reset", "-q", "--hard")
    _git(ws, "clean", "-q", "-fd")
    db.audit("owner", "workspace.discard", name=name)
    return "Changes thrown away."
