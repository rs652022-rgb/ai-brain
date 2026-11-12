"""Run Python for calculations and data work (like OpenClaw's code execution), kept in a box.

- With Docker available: a throwaway container with no network, 256 MB memory, 1 CPU, 30 seconds.
- Without Docker, only on your own computer and only if the owner switches on "sandbox_local": a separate
  Python process in an empty temporary folder with a time limit and memory limits (rlimits on Mac and Linux,
  a Job Object on Windows). It has no network isolation, so it is off by default and on shared servers.
"""
import os
import shutil
import subprocess
import sys
import tempfile

from . import config, settings

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
    if config.MULTIUSER or not settings.load().get("sandbox_local", False):
        return "off"
    return "local"


def _limits():                      # runs in the child before Python starts (Mac/Linux)
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT,) * 2)
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


MEMORY_LIMIT = 512 * 1024 * 1024


def _windows_job():
    """A Job Object that caps memory, allows one process and kills it when closed. None if unavailable."""
    import ctypes
    from ctypes import wintypes

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    # PROCESS_MEMORY | JOB_MEMORY | ACTIVE_PROCESS | KILL_ON_JOB_CLOSE
    info.Basic.LimitFlags = 0x100 | 0x200 | 0x8 | 0x2000
    info.Basic.ActiveProcessLimit = 1
    info.ProcessMemoryLimit = info.JobMemoryLimit = MEMORY_LIMIT
    if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(wintypes.HANDLE(job))
        return None
    return k32, job


def _run_local(code: str, tmp: str, env: dict) -> subprocess.CompletedProcess:
    args = [sys.executable, "-I", "-c", code]
    if os.name != "nt":
        return subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT, cwd=tmp, env=env,
                              preexec_fn=_limits)
    from ctypes import wintypes
    job = _windows_job()
    if job is None:
        raise SandboxError("Couldn't set memory limits for the code on Windows, so it wasn't run. Install Docker.")
    k32, handle = job
    try:
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, cwd=tmp, env=env, creationflags=0x08000000)
        if not k32.AssignProcessToJobObject(wintypes.HANDLE(handle), wintypes.HANDLE(int(p._handle))):
            p.kill()
            p.communicate()
            raise SandboxError("Couldn't put the code in a limited box on Windows, so it was stopped.")
        try:
            out, err = p.communicate(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            raise
        return subprocess.CompletedProcess(args, p.returncode, out, err)
    finally:
        k32.CloseHandle(wintypes.HANDLE(handle))


def run(code: str) -> str:
    code = (code or "").strip()
    if not code:
        raise SandboxError("No code.")
    if len(code) > 20000:
        raise SandboxError("That code is too long.")
    m = mode()
    if m == "off":
        raise SandboxError("Running code needs Docker (or, on your own computer, the sandbox_local setting).")
    try:
        if m == "docker":
            r = subprocess.run(["docker", "run", "--rm", "-i", "--network", "none", "--memory", "256m", "--cpus", "1",
                                "--pids-limit", "64", "--read-only", "--tmpfs", "/tmp", "python:3.12-slim", "python", "-"],
                               input=code, capture_output=True, text=True, timeout=TIMEOUT + 30)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
                r = _run_local(code, tmp, env)
    except subprocess.TimeoutExpired:
        return f"Stopped: the code ran longer than {TIMEOUT} seconds."
    out = (r.stdout or "")[-6000:]
    err = (r.stderr or "")[-2000:]
    return f"{out}{chr(10) + 'Errors:' + chr(10) + err if err else ''}".strip() or "(no output)"
