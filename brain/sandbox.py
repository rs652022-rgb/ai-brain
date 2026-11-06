"""Run Python for calculations and data work (like OpenClaw's code execution), kept in a box.

- With Docker available: a throwaway container with no network, 256 MB memory, 1 CPU, 30 seconds.
- Without Docker, only on your own computer: a separate Python process in an empty temporary folder with
  a time limit (and memory/CPU limits on Mac and Linux). It is off on shared servers without Docker.
"""
import os
import shutil
import subprocess
import sys
import tempfile

from . import config

TIMEOUT = 30


class SandboxError(RuntimeError):
    pass


def docker() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=8).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_docker_cache: dict = {}


def mode() -> str:
    if "ok" not in _docker_cache:
        _docker_cache["ok"] = docker()
    if _docker_cache["ok"]:
        return "docker"
    return "local" if not config.MULTIUSER else "off"


def _limits():                      # runs in the child before Python starts (Mac/Linux)
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT,) * 2)
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


def run(code: str) -> str:
    code = (code or "").strip()
    if not code:
        raise SandboxError("No code.")
    if len(code) > 20000:
        raise SandboxError("That code is too long.")
    m = mode()
    if m == "off":
        raise SandboxError("Running code needs Docker on a shared server.")
    try:
        if m == "docker":
            r = subprocess.run(["docker", "run", "--rm", "-i", "--network", "none", "--memory", "256m", "--cpus", "1",
                                "--pids-limit", "64", "--read-only", "--tmpfs", "/tmp", "python:3.12-slim", "python", "-"],
                               input=code, capture_output=True, text=True, timeout=TIMEOUT + 30)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
                r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=TIMEOUT,
                                   cwd=tmp, env=env, preexec_fn=_limits if os.name != "nt" else None,
                                   creationflags=0x08000000 if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        return f"Stopped: the code ran longer than {TIMEOUT} seconds."
    out = (r.stdout or "")[-6000:]
    err = (r.stderr or "")[-2000:]
    return f"{out}{chr(10) + 'Errors:' + chr(10) + err if err else ''}".strip() or "(no output)"
