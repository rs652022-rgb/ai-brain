"""Start the AI Brain: python run.py, then open http://127.0.0.1:8765

On a server (Docker), BRAIN_MULTIUSER=1 turns on sign-up and login and BRAIN_HOST=0.0.0.0 makes it
reachable; put it behind HTTPS (Caddy or a Cloudflare Tunnel, see DEPLOY.md).
"""
import os
import webbrowser
from threading import Timer

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("BRAIN_PORT", "8765"))
    host = os.environ.get("BRAIN_HOST", "127.0.0.1")
    server = os.environ.get("BRAIN_MULTIUSER") == "1"
    url = f"http://127.0.0.1:{port}"
    print(f"\n  AI Brain is starting at {url}" + ("  (web version: accounts on)" if server else "") + "\n  Press Ctrl+C to stop.\n")
    if os.environ.get("BRAIN_NO_BROWSER") != "1" and not server:
        Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("brain.server:app", host=host, port=port, log_level="warning",
                proxy_headers=server, forwarded_allow_ips="*" if server else None)
