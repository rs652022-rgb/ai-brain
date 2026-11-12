"""Plugins (like OpenClaw's plugin SDK) and skill packs (like ClawHub).

Plugin: a folder in data/plugins/<name>/ with plugin.py:

    def register(brain):
        @brain.tool("weather", "Current weather for a city", '{"city": "Jaipur"}')
        def weather(args, ctx):
            return "31°C and sunny"

Plugins are Python code, so they only run on a computer you control: your laptop, or a server where the
admin put them in the plugins folder. They can't be uploaded through the web version.

Skill packs: share skills as a .zip of SKILL.md files. Safe to import anywhere (they're just text).
"""
import importlib.util
import io
import json
import logging
import re
import zipfile

from . import config, db, skills, tools

log = logging.getLogger("brain.plugins")
_loaded: dict[str, dict] = {}


def folder():
    d = config.ROOT_DATA / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _enabled() -> set[str]:
    try:
        return set(json.loads((folder() / "enabled.json").read_text()))
    except (OSError, ValueError):
        return set()


def set_enabled(name: str, on: bool) -> None:
    names = _enabled()
    (names.add if on else names.discard)(name)
    (folder() / "enabled.json").write_text(json.dumps(sorted(names)))
    load_all()


class _API:
    def __init__(self, plugin: str):
        self.plugin, self.tools = plugin, []

    def tool(self, name: str, description: str, args_hint: str = "{}"):
        def deco(fn):
            full = f"p_{re.sub(r'[^a-z0-9_]', '_', name.lower())}"
            tools.TOOLS[full] = (args_hint, f"[plugin {self.plugin}] {description}", lambda a, c: str(fn(a, c)),
                                 lambda c: True)
            self.tools.append(full)
            return fn
        return deco


def load_all() -> list[dict]:
    for info in _loaded.values():
        for t in info.get("tools", []):
            tools.TOOLS.pop(t, None)
    _loaded.clear()
    on = _enabled()
    for d in sorted(p for p in folder().iterdir() if p.is_dir()):
        info = {"name": d.name, "enabled": d.name in on, "tools": [], "error": ""}
        if info["enabled"] and (d / "plugin.py").is_file():
            try:
                spec = importlib.util.spec_from_file_location(f"brain_plugin_{d.name}", d / "plugin.py")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                api = _API(d.name)
                mod.register(api)
                info["tools"] = api.tools
                info["description"] = (mod.__doc__ or "").strip().split("\n")[0][:200]
            except Exception as exc:
                info["error"] = f"{exc.__class__.__name__}: {exc}"[:300]
                log.warning("Plugin %s failed: %s", d.name, exc)
        _loaded[d.name] = info
    return list(_loaded.values())


def listing() -> list[dict]:
    return list(_loaded.values()) or load_all()


def install_zip(data: bytes) -> str:
    """Install a plugin from a .zip (laptop only)."""
    if config.MULTIUSER:
        raise PermissionError("On a shared server, only the admin can add plugins (in the server's plugins folder).")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        plug = next((n for n in names if n.endswith("plugin.py")), None)
        if not plug:
            raise ValueError("The zip has no plugin.py.")
        root = plug.rsplit("/", 1)[0] if "/" in plug else ""
        name = re.sub(r"[^a-z0-9_-]", "", (root.split("/")[-1] if root else "plugin").lower())[:30] or "plugin"
        dest = folder() / name
        dest.mkdir(exist_ok=True)
        for n in names:
            if n.endswith("/") or (root and not n.startswith(root + "/")):
                continue
            rel = n[len(root) + 1:] if root else n
            parts = rel.replace("\\", "/").split("/")
            if rel.startswith(("/", "\\")) or ".." in parts or ":" in rel:
                raise ValueError("Unsafe file in the zip.")
            out = dest / rel
            if not out.resolve().is_relative_to(dest.resolve()):
                raise ValueError("Unsafe file in the zip.")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(n))
    db.audit("owner", "plugin.install", plugin=name)
    load_all()
    return name


# ---------- Skill packs ----------

def export_skills() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for s in skills.list_all():
            if s["status"] != "off":
                z.write(skills.root() / s["name"] / "SKILL.md", f"{s['name']}/SKILL.md")
    return buf.getvalue()


def import_skills(name: str, data: bytes) -> list[str]:
    added = []
    if name.lower().endswith(".md"):
        meta, body = skills._parse(data.decode("utf-8", "replace"))
        s = skills.save(meta.get("name") or name.rsplit(".", 1)[0], str(meta.get("description", "")), body, "suggested", "import")
        return [s["name"]]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist()[:50]:
            if n.endswith("SKILL.md"):
                meta, body = skills._parse(z.read(n).decode("utf-8", "replace")[:20000])
                folder_name = n.rsplit("/", 2)[-2] if "/" in n else "skill"
                try:
                    s = skills.save(meta.get("name") or folder_name, str(meta.get("description", "")), body,
                                    "suggested", "import")
                    added.append(s["name"])
                except ValueError:
                    pass
    db.audit("owner", "skills.import", count=len(added))
    return added
