"""Build the Sourcecado app icon ladder from brand/app-icon.png.

The master is a tight crop of the flight mascot. `npx tauri icon` writes the
PNG/.icns/.ico sizes Tauri bundles; this script also copies 128px to the GUI
favicon so the Vite window and the rail mark stay in lockstep with the dock.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "brand" / "app-icon.png"
GUI = ROOT / "desktop" / "surfaces" / "gui"
ICONS = GUI / "src-tauri" / "icons"
FAVICON = GUI / "public" / "favicon.png"


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"missing icon master: {SOURCE}")
    ICONS.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, ICONS / "icon-source.png")
    subprocess.run(
        ["npx", "tauri", "icon", "src-tauri/icons/icon-source.png"],
        cwd=GUI,
        check=True,
    )
    FAVICON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICONS / "128x128.png", FAVICON)
    print("rendered")


if __name__ == "__main__":
    main()
