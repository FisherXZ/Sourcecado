"""Build the Sourcecado app icon ladder from brand/app-icon.png.

The master is a tight crop of the flight mascot. `npx tauri icon` writes the
PNG/.icns/.ico sizes Tauri bundles; this script also copies 128px to the GUI
favicon so the Vite window and the rail mark stay in lockstep with the dock.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "brand" / "app-icon.png"
GUI = ROOT / "desktop" / "surfaces" / "gui"
ICONS = GUI / "src-tauri" / "icons"
FAVICON = GUI / "public" / "favicon.png"
TAURI_ICON_ARGS = ["npx", "tauri", "icon", "src-tauri/icons/icon-source.png"]


def tauri_icon_command(
    *,
    gui: Path = GUI,
    node_arch: str | None = None,
    platform_name: str = sys.platform,
) -> list[str]:
    """Use the architecture of the Tauri binding npm installed on macOS."""
    if platform_name != "darwin":
        return TAURI_ICON_ARGS.copy()
    if node_arch is None:
        node_arch = subprocess.run(
            ["node", "-p", "process.arch"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    installed = {
        arch
        for arch in ("arm64", "x64")
        if (gui / "node_modules" / "@tauri-apps" / f"cli-darwin-{arch}").is_dir()
    }
    if node_arch in installed or len(installed) != 1:
        return TAURI_ICON_ARGS.copy()
    target = installed.pop()
    arch_flag = "-x86_64" if target == "x64" else f"-{target}"
    return ["/usr/bin/arch", arch_flag, *TAURI_ICON_ARGS]


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"missing icon master: {SOURCE}")
    ICONS.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, ICONS / "icon-source.png")
    subprocess.run(
        tauri_icon_command(),
        cwd=GUI,
        check=True,
    )
    FAVICON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICONS / "128x128.png", FAVICON)
    print("rendered")


if __name__ == "__main__":
    main()
