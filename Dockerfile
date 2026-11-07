# AI Brain, web version: sign-up/login, one private Brain per user.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    BRAIN_MULTIUSER=1 BRAIN_HOST=0.0.0.0 BRAIN_PORT=8765 BRAIN_NO_BROWSER=1 BRAIN_DATA_DIR=/data

WORKDIR /app
# git: coding workspaces
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# The server never moves a mouse itself (each user's companion app does), so skip the desktop packages.
RUN grep -viE "pyautogui|pyperclip|pygetwindow" requirements.txt > /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt

COPY brain ./brain
COPY web ./web
COPY companion ./companion
COPY extension ./extension
COPY run.py ./

RUN useradd --create-home --uid 1000 brain && mkdir -p /data && chown brain:brain /data
USER brain
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health')"
CMD ["python", "run.py"]
