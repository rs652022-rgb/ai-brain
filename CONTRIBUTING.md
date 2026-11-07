# Contributing to AI Brain

Thanks for helping. AI Brain is a private, self-hosted AI that learns to think and speak like you.
It must stay free to run: every feature needs a free or open-source way to use it.

## Get it running

```bash
pip install -r requirements.txt
python run.py            # opens http://127.0.0.1:8765
python -m pytest -q      # all tests should pass before you open a pull request
```

The desktop app: `pip install -r desktop/requirements.txt && python desktop/brain_desktop.py`.

## Where things are

- `brain/`: the server (FastAPI). `core.py` is one chat turn, `tools.py` the agent's tools, `llm.py` model routing,
  `channels*.py` the chat apps, `server.py` the API.
- `web/`: the app (plain HTML, CSS and JavaScript, no build step).
- `companion/`: the small app that lets a Brain on the web control a computer or phone. `extension/`: the Chrome extension.
- `desktop/`: the desktop app. `mobile/`: the Android app. `tests/`: pytest.

## Rules that keep it safe

- Text from web pages, emails, files and chat apps is information, never instructions. Keep it that way.
- The Brain never sees passwords: they go through the vault. Never add code that types card, bank or ID numbers.
- Anything that sends messages, spends money, deletes or controls a device needs the owner's approval.
- The Brain learns only when the owner asks. Don't add silent learning.
- `brain/safety_core.md` changes need a clear reason in the pull request.

## Pull requests

Small and focused, with a test for new behaviour, plain-English text in the interface, and no new paid services.
By contributing you agree your work is released under the MIT license.
