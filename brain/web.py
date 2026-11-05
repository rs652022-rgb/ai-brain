"""Web access: search, safe page reading, and reading documents.

Everything read here is DATA. It is stored and shown to the model inside <web_content> tags
with an explicit note that it is information, never instructions.
"""
import io
import ipaddress
import logging
import re
import socket
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from . import settings

log = logging.getLogger("brain.web")

UA = "Mozilla/5.0 (compatible; AIBrain/0.2; personal research assistant)"
MAX_PAGE_BYTES = 3_000_000
MAX_TEXT_CHARS = 60_000

SEARCH_PRESETS = {
    "duckduckgo": {"label": "DuckDuckGo", "needs_key": False, "free": True, "key_url": "",
                   "note": "No key needed. Can be rate-limited if used heavily."},
    "brave": {"label": "Brave Search", "needs_key": True, "free": True, "key_url": "https://api-dashboard.search.brave.com",
              "note": "Free plan with a monthly quota. Reliable."},
    "tavily": {"label": "Tavily", "needs_key": True, "free": True, "key_url": "https://app.tavily.com",
               "note": "Free monthly credits. Built for AI research."},
    "searxng": {"label": "SearXNG (self-hosted)", "needs_key": False, "free": True, "key_url": "",
                "note": "Your own search server. Paste its URL, e.g. http://localhost:8080"},
}


class WebError(RuntimeError):
    pass


# ---------- Safety: never fetch your own laptop or private network ----------

def _host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def check_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebError("Only http and https web addresses can be read.")
    if not _host_is_public(parts.hostname):
        raise WebError("For safety the Brain does not read addresses on your own computer or private network.")
    return url


# ---------- Reading pages ----------

def fetch(url: str, max_redirects: int = 5) -> tuple[str, str, str]:
    """Return (final_url, content_type, body_bytes_as_text_or_marker). Follows redirects safely."""
    url = check_url(url)
    for _ in range(max_redirects + 1):
        try:
            with httpx.stream("GET", url, headers={"User-Agent": UA, "Accept": "text/html,application/pdf,*/*"},
                              timeout=httpx.Timeout(25, connect=10), follow_redirects=False) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = check_url(urljoin(url, r.headers["location"]))
                    continue
                if r.status_code >= 400:
                    raise WebError(f"The page returned HTTP {r.status_code}.")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                data = b""
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > MAX_PAGE_BYTES:
                        break
                return url, ctype, data
        except httpx.HTTPError as exc:
            raise WebError(f"Could not reach the page ({exc.__class__.__name__}).") from exc
    raise WebError("Too many redirects.")


def html_to_text(html: str | bytes, base_url: str = "") -> tuple[str, str]:
    """Readable text and title from HTML: drops scripts, menus, footers and hidden elements."""
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:200]
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header", "aside", "template"]):
        tag.decompose()
    for tag in soup.select('[hidden], [aria-hidden="true"], [style*="display:none"], [style*="display: none"]'):
        tag.decompose()  # hidden text is a common place for prompt-injection tricks
    root = soup.find("article") or soup.find("main") or soup.body or soup
    lines = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th", "pre", "blockquote"]):
        t = el.get_text(" ", strip=True)
        if len(t) < 2:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            t = "## " + t
        elif el.name == "li":
            t = "- " + t
        lines.append(t)
    text = "\n".join(dict.fromkeys(lines))  # drop exact duplicate lines
    if len(text) < 200:  # unusual layout (text in divs): fall back to all visible text if that finds much more
        alt = root.get_text("\n", strip=True)
        if len(alt) > 2 * len(text):
            text = alt
    return re.sub(r"\n{3,}", "\n\n", text)[:MAX_TEXT_CHARS], title


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages[:200])[:MAX_TEXT_CHARS * 4]


def docx_to_text(data: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)[:MAX_TEXT_CHARS * 4]


def read_url(url: str) -> dict:
    """Read a web page or online PDF. Returns {url, title, text}."""
    final, ctype, data = fetch(url)
    if ctype == "application/pdf" or final.lower().endswith(".pdf"):
        text, title = pdf_to_text(data), final.rsplit("/", 1)[-1]
    elif ctype.startswith("text/plain") or final.lower().endswith((".txt", ".md")):
        text, title = data.decode("utf-8", "replace")[:MAX_TEXT_CHARS], final.rsplit("/", 1)[-1]
    else:
        text, title = html_to_text(data, final)
    text = text.strip()
    if len(text) < 80:
        raise WebError("The page had almost no readable text (it may need JavaScript or a login).")
    return {"url": final, "title": title or final, "text": text}


def read_file(name: str, data: bytes) -> dict:
    low = name.lower()
    if low.endswith(".pdf"):
        text = pdf_to_text(data)
    elif low.endswith(".docx"):
        text = docx_to_text(data)
    elif low.endswith((".html", ".htm")):
        text, _ = html_to_text(data)
    elif low.endswith((".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log")):
        text = data.decode("utf-8", "replace")
    else:
        raise WebError("Supported files: PDF, Word (.docx), HTML, TXT, MD, CSV, JSON.")
    if len(text.strip()) < 20:
        raise WebError("No readable text found (a scanned PDF needs OCR first).")
    return {"url": f"file:{name}", "title": name, "text": text.strip()}


# ---------- Search ----------

def _ddg(query: str, n: int, cfg: dict) -> list[dict]:
    r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query}, headers={"User-Agent": UA}, timeout=20)
    if r.status_code != 200:
        raise WebError(f"DuckDuckGo returned HTTP {r.status_code}")
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for res in soup.select(".result"):
        a = res.select_one("a.result__a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if "uddg=" in href:  # DuckDuckGo wraps links in a redirect
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        if href.startswith("//"):
            href = "https:" + href
        if "duckduckgo.com/y.js" in href:  # ads
            continue
        snip = res.select_one(".result__snippet")
        out.append({"title": a.get_text(" ", strip=True), "url": href, "snippet": snip.get_text(" ", strip=True) if snip else ""})
        if len(out) >= n:
            break
    if not out:
        raise WebError("DuckDuckGo returned no results (it may be rate-limiting).")
    return out


def _brave(query: str, n: int, cfg: dict) -> list[dict]:
    r = httpx.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": n},
                  headers={"X-Subscription-Token": cfg.get("api_key", ""), "Accept": "application/json"}, timeout=20)
    if r.status_code != 200:
        raise WebError(f"Brave returned HTTP {r.status_code}")
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": re.sub("<[^>]+>", "", x.get("description", ""))}
            for x in r.json().get("web", {}).get("results", [])[:n]]


def _tavily(query: str, n: int, cfg: dict) -> list[dict]:
    key = cfg.get("api_key", "")
    r = httpx.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"},
                   json={"api_key": key, "query": query, "max_results": n}, timeout=30)
    if r.status_code != 200:
        raise WebError(f"Tavily returned HTTP {r.status_code}")
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")[:400]}
            for x in r.json().get("results", [])[:n]]


def _searxng(query: str, n: int, cfg: dict) -> list[dict]:
    base = (cfg.get("base_url") or "").rstrip("/")
    from . import settings
    if base and not settings.url_allowed(base):
        raise WebError("This SearXNG address isn't allowed on a shared server (it must be a public address).")
    if not base:
        raise WebError("SearXNG URL not set")
    r = httpx.get(f"{base}/search", params={"q": query, "format": "json"}, timeout=20)
    if r.status_code != 200:
        raise WebError(f"SearXNG returned HTTP {r.status_code}")
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
            for x in r.json().get("results", [])[:n]]


ENGINES = {"duckduckgo": _ddg, "brave": _brave, "tavily": _tavily, "searxng": _searxng}


def search(query: str, n: int = 6) -> list[dict]:
    """Search the web with the first engine that works, in the order set in Settings."""
    data = settings.load()
    order = data.get("search_order") or ["duckduckgo"]
    errors = []
    for eid in order:
        cfg = data.get("search", {}).get(eid, {})
        if SEARCH_PRESETS.get(eid, {}).get("needs_key") and not cfg.get("api_key"):
            errors.append(f"{eid}: no key")
            continue
        try:
            results = [r for r in ENGINES[eid](query, n, cfg) if r["url"].startswith("http")]
            if results:
                return [r | {"engine": eid} for r in results]
            errors.append(f"{eid}: no results")
        except (WebError, httpx.HTTPError, KeyError, ValueError) as exc:
            errors.append(f"{eid}: {exc}")
    raise WebError("Web search failed. " + "; ".join(errors))
