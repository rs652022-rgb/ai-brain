"""One shared, kept-open HTTP connection pool for AI providers.

Opening a new HTTPS connection costs a DNS lookup and a TLS handshake (about 0.2 to 0.5 s) on every
request. Keeping connections open between messages removes that wait from every reply.
Use it like the httpx module: netpool.post(...), netpool.stream(...), netpool.get(...).
Cookies are never kept, so nothing set by one provider or one user's request leaks into another.
"""
import threading
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx
from httpx import *  # noqa: F401,F403  (Timeout, Response, Request, HTTPError... like the httpx module)

_lock = threading.Lock()
_client: httpx.Client | None = None


def _pool() -> httpx.Client:
    global _client
    with _lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(
                limits=httpx.Limits(max_connections=40, max_keepalive_connections=20, keepalive_expiry=90),
                cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
                follow_redirects=False)
        return _client


def post(url: str, **kw) -> httpx.Response:
    return _pool().post(url, **kw)


def get(url: str, **kw) -> httpx.Response:
    return _pool().get(url, **kw)


def stream(method: str, url: str, **kw):
    return _pool().stream(method, url, **kw)


def close() -> None:
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None
