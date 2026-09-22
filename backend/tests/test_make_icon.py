"""The checked-in icon master must regenerate on the operator's Mac."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _make_icon():
    path = Path(__file__).resolve().parents[1] / "packaging" / "make_icon.py"
    spec = importlib.util.spec_from_file_location("sourcecado_make_icon", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_icon_command_uses_the_installed_tauri_architecture(tmp_path: Path) -> None:
    gui = tmp_path / "gui"
    (gui / "node_modules" / "@tauri-apps" / "cli-darwin-arm64").mkdir(
        parents=True
    )

    command = _make_icon().tauri_icon_command(
        gui=gui,
        node_arch="x64",
        platform_name="darwin",
    )

    assert command == [
        "/usr/bin/arch",
        "-arm64",
        "npx",
        "tauri",
        "icon",
        "src-tauri/icons/icon-source.png",
    ]
