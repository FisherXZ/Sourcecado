#!/usr/bin/env bash
# Freezes the Python backend into a self-contained native directory and drops
# it where tauri.conf.json's `bundle.resources` expects one:
#   desktop/surfaces/gui/src-tauri/resources/sourcecado-backend/
#
# Built with PyInstaller --onedir, not --onefile: a onefile build's bootloader
# forks a second process to run the actual interpreter and hands back the
# bootloader's PID, so killing that PID orphans the real backend. onedir has
# no extraction step and no fork, so the spawned PID is the one that answers
# requests and the one a later kill() actually stops (see lib.rs).
#
# The frozen backend embeds the interpreter and all locked dependencies, so
# the packaged app never shells out to `python -m coworker.run` or
# `desktop/.venv`.
set -euo pipefail

DESKTOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_DIR="$DESKTOP_DIR/surfaces/gui/src-tauri/resources"

BUILD_VENV="$(mktemp -d)/backend-build-venv"
WORKPATH="$(mktemp -d)"
trap 'rm -rf "$BUILD_VENV" "$WORKPATH"' EXIT

echo "==> building sourcecado-backend"
python3 -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --quiet --require-hashes -r "$DESKTOP_DIR/requirements.lock"
"$BUILD_VENV/bin/python" -m pip install --quiet "pyinstaller==6.16.0"

"$BUILD_VENV/bin/pyinstaller" \
  --onedir \
  --clean \
  --noconfirm \
  --name sourcecado-backend \
  --paths "$DESKTOP_DIR" \
  --add-data "$DESKTOP_DIR/coworker/personas:coworker/personas" \
  --add-data "$DESKTOP_DIR/coworker/builtin_skills:coworker/builtin_skills" \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.loops.uvloop \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.http.h11_impl \
  --hidden-import uvicorn.protocols.http.httptools_impl \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.protocols.websockets.websockets_impl \
  --hidden-import uvicorn.lifespan.on \
  --distpath "$WORKPATH/dist" \
  --workpath "$WORKPATH/build" \
  --specpath "$WORKPATH/spec" \
  "$DESKTOP_DIR/packaging/backend_entry.py"

mkdir -p "$RESOURCE_DIR"
rm -rf "$RESOURCE_DIR/sourcecado-backend"
cp -R "$WORKPATH/dist/sourcecado-backend" "$RESOURCE_DIR/sourcecado-backend"
chmod +x "$RESOURCE_DIR/sourcecado-backend/sourcecado-backend"
echo "==> wrote $RESOURCE_DIR/sourcecado-backend/"
