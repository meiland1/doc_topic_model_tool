"""PyInstaller entry script.

Kept separate from qualitative_themes/app.py so the frozen app imports the
package normally. All the real startup logic lives in app.main().
"""
import multiprocessing
import sys

if __name__ == "__main__":
    # Needed on Windows/macOS so library worker processes don't re-launch the GUI.
    multiprocessing.freeze_support()
    from qualitative_themes.app import main
    sys.exit(main())
