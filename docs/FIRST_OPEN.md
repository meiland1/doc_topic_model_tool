How to open Topic Model Tool the first time
============================================

Topic Model Tool is free, open-source software. It isn't sold through an app
store, so Windows and macOS show a warning the first time you open it. This is
expected. You only need to do these steps once.

Your documents never leave your computer. After installing, the tool works
without an internet connection.


WINDOWS
-------

1. Double-click TopicModelTool-Setup.exe.
2. If a blue "Windows protected your PC" box appears, click "More info",
   then click "Run anyway".
3. Follow the installer. You do not need administrator rights.

Optional: if you also downloaded the theme-naming model (files ending in
.tmtpack.001, .tmtpack.002, .tmtpack.003), put all of them in the same
folder as the installer before you run it. They are installed automatically;
this can take several minutes.


MAC
---

1. Double-click the .dmg file you downloaded.
2. Drag "Topic Model Tool" onto the Applications folder.
3. Open Applications and double-click "Topic Model Tool".
   A message says macOS cannot verify the app. Click "Done" (or "OK").
4. Open System Settings, then click "Privacy & Security" in the sidebar.
5. Scroll down to the "Security" section. Next to the message about
   "Topic Model Tool", click "Open Anyway".
6. Click "Open Anyway" again and enter your Mac password.

From now on the app opens normally.

Optional: to add the theme-naming model, keep all its parts (.tmtpack.001,
.002, .003) together in Downloads, open the app, tick "Setup details" on the
start screen, click "Add theme-naming model…" and choose the .tmtpack.001 file.


CHECKING THE DOWNLOAD (for IT staff)
------------------------------------

Each release lists SHA-256 checksums in SHA256SUMS.txt.
Windows (PowerShell):  Get-FileHash .\TopicModelTool-Setup-<version>.exe
Mac (Terminal):        shasum -a 256 TopicModelTool-<version>-macOS-arm64.dmg
