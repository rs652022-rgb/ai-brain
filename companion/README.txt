Brain Companion
===============

Lets your Brain on the web use this computer (mouse, keyboard, screen, apps, files you allow)
and an Android phone connected to it. Only you can link it, and only to your own account.

1. In your Brain (in the browser): Control -> Your computers -> Make a pairing code.
2. Start the companion:
     Windows:        double-click start-companion.bat
     macOS / Linux:  run ./start-companion.sh
     Menu-bar / tray version (no window): start-companion.bat --tray  or  ./start-companion.sh --tray
   (Needs Python 3.10+ from python.org the first time. The .exe version needs nothing.)
3. Enter your Brain's web address and the pairing code. Leave the window open.

Stop it at any moment: close the window or press Ctrl+C. Push the mouse into a screen corner to
stop the current action. To link a different account: start it with --unlink.

macOS: allow "Accessibility" and "Screen Recording" for Terminal (or the app) in
System Settings -> Privacy & Security, or it can't click or see the screen.
