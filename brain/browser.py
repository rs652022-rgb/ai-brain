"""A web browser the Brain can drive (like OpenClaw's browser tool): open a page, click, type, press keys,
then read what the page shows. Runs a headless Chromium on the machine the Brain runs on.

Safety: it can't open your own computer or private network addresses (every request is checked),
it never types passwords or card numbers, and page text is information, not instructions.
Needs Playwright: Settings → Install the browser tool (or `pip install playwright && playwright install chromium`).
"""
import base64
import importlib.util
import re
import subprocess
import sys
from urllib.parse import urlparse

from . import config, devices, web


class BrowserError(RuntimeError):
    pass


def installed() -> bool:
    return importlib.util.find_spec("playwright") is not None


def install() -> dict:
    if config.MULTIUSER:
        raise BrowserError("The admin installs the browser tool on the server (see DEPLOY.md).")
    for cmd in ([sys.executable, "-m", "pip", "install", "-q", "playwright"], [sys.executable, "-m", "playwright", "install", "chromium"]):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode:
            raise BrowserError(f"Install failed: {(r.stderr or r.stdout)[-300:]}")
    return {"ok": True}


_public_cache: dict[str, bool] = {}


def _host_ok(url: str) -> bool:
    p = urlparse(url)
    if p.scheme in ("data", "blob", "about"):
        return True
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    if config.allow_private() and not config.MULTIUSER and p.hostname in ("localhost", "127.0.0.1"):
        return False                      # never the Brain's own pages or your local services
    if p.hostname not in _public_cache:
        _public_cache[p.hostname] = web._host_is_public(p.hostname)
    return _public_cache[p.hostname]


def _elements(page) -> str:
    js = """() => [...document.querySelectorAll('a,button,input,textarea,select,[role=button]')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).slice(0, 60).map(e => {
        const t = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || e.name || '').trim().slice(0, 60);
        return `${e.tagName.toLowerCase()}${e.type ? '[' + e.type + ']' : ''}: ${t}`; })"""
    try:
        return "\n".join(page.evaluate(js))
    except Exception:
        return ""


def browse(url: str, actions: list | None = None, screenshot: bool = False) -> dict:
    """Open url, run the actions in order, and return what the page shows."""
    if not installed():
        raise BrowserError("The browser tool isn't installed. Settings → Install the browser tool.")
    web.check_url(url)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            ctx = b.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=False,
                                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                           "Chrome/124.0 Safari/537.36 AI-Brain")
            ctx.route("**/*", lambda route: route.continue_() if _host_ok(route.request.url) else route.abort())
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            done = []
            for act in (actions or [])[:12]:
                if not isinstance(act, dict):
                    continue
                try:
                    if "click" in act:
                        target = str(act["click"])
                        loc = page.locator(target) if re.match(r"^[#.\[]|^\w+\[", target) else page.get_by_text(target, exact=False)
                        loc.first.click(timeout=8000)
                        done.append(f"clicked {target}")
                    elif "fill" in act:
                        sel, value = act["fill"][0], str(act["fill"][1])
                        devices.check_text(value)
                        if re.search(r"pass|card|cvv|otp|pin", sel, re.I):
                            raise PermissionError("I don't type into password, card, OTP or PIN fields.")
                        loc = page.locator(sel) if re.match(r"^[#.\[]|^\w+\[", sel) else page.get_by_placeholder(sel)
                        loc.first.fill(value, timeout=8000)
                        done.append(f"typed into {sel}")
                    elif "press" in act:
                        page.keyboard.press(str(act["press"]))
                        done.append(f"pressed {act['press']}")
                    elif "wait" in act:
                        page.wait_for_timeout(min(8000, int(float(act["wait"]) * 1000)))
                    elif "scroll" in act:
                        page.mouse.wheel(0, 900 if str(act["scroll"]) != "up" else -900)
                    elif "goto" in act:
                        web.check_url(str(act["goto"]))
                        page.goto(str(act["goto"]), wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except PermissionError:
                    raise
                except Exception as exc:
                    done.append(f"couldn't do {act}: {str(exc)[:120]}")
            page.wait_for_timeout(600)
            text, _ = web.html_to_text(page.content(), page.url)
            out = {"url": page.url, "title": page.title(), "text": text[:7000], "elements": _elements(page), "done": done}
            if screenshot:
                out["shot"] = devices._jpeg(page.screenshot(type="png"), max_w=1100)
            return out
        finally:
            b.close()


def _b64(png: bytes) -> str:
    return base64.b64encode(png).decode()
