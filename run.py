"""Start the AI Brain: python run.py, then open http://127.0.0.1:8765"""
import os
import webbrowser
from threading import Timer

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("BRAIN_PORT", "8765"))
    url = f"http://127.0.0.1:{port}"
    print(f"\n  AI Brain is starting at {url}\n  Press Ctrl+C to stop.\n")
    if os.environ.get("BRAIN_NO_BROWSER") != "1":
        Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("brain.server:app", host="127.0.0.1", port=port, log_level="warning")
