# AI Brain desktop app

The whole Brain in its own window on Windows, macOS and Linux. No Python, no terminal.

- **Download:** GitHub → Actions → "Build desktop app" → the latest run → Artifacts
  (`AIBrain-Windows-Setup.exe`, `AIBrain-macOS.dmg`, `AIBrain-Linux.tar.gz`), or a release made from a `desktop-v*` tag.
- **Windows:** run the setup. If SmartScreen warns ("unknown publisher", because the app isn't code-signed), choose
  More info → Run anyway.
- **macOS:** open the .dmg and drag AI Brain to Applications. The first time, right-click the app → Open
  (it isn't notarised by Apple, which costs $99/year).
- **Linux:** unpack and run `AIBrain/AIBrain`. The window needs GTK WebKit (`sudo apt install gir1.2-webkit2-4.1`);
  without it the Brain opens in your browser.

Your memories and settings are kept in your user folder (not inside the app), so installing a new version keeps everything:
Windows `%APPDATA%\AI Brain`, macOS `~/Library/Application Support/AI Brain`, Linux `~/.local/share/ai-brain`.

First start: Settings → add your FreeLLMAPI key (free, pools many free models) and keep the model on `auto`.

Run from source instead: `pip install -r desktop/requirements.txt && python desktop/brain_desktop.py`.
