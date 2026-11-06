"""Adds the permissions the Brain app uses (camera, location, microphone, notifications) and its icon."""
import shutil
import sys
from pathlib import Path

android, icon = Path(sys.argv[1]), Path(sys.argv[2])
manifest = android / "app/src/main/AndroidManifest.xml"
text = manifest.read_text()
perms = ["CAMERA", "RECORD_AUDIO", "MODIFY_AUDIO_SETTINGS", "ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION",
         "POST_NOTIFICATIONS", "INTERNET"]
add = "".join(f'    <uses-permission android:name="android.permission.{p}" />\n' for p in perms
              if f"android.permission.{p}" not in text)
text = text.replace("<application", add + "    <application", 1)
manifest.write_text(text)
res = android / "app/src/main/res"
for d in res.glob("mipmap-*"):
    for name in ("ic_launcher.png", "ic_launcher_round.png", "ic_launcher_foreground.png"):
        if (d / name).exists():
            shutil.copy(icon, d / name)
print("Android project patched.")
