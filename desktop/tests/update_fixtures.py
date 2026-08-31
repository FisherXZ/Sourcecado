"""Shared scaffolding for the update-channel tests.

Everything here builds a *real* update: a real Ed25519 keypair, a real zip of a
real directory tree shaped like `Sourcecado.app`, and a real signed manifest
over that artifact's actual digest. Nothing is stubbed at the boundary the
production code checks, so a test that passes here would pass against a
publisher's artifact.
"""

from __future__ import annotations

import base64
import shutil
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from coworker.update_channel import manifest as m
from coworker.update_channel.apply import Installation

KEY_ID = "test-preview-2026"
PRODUCT = "sourcecado"
BACKEND_RELATIVE = "Contents/Resources/resources/sourcecado-backend/sourcecado-backend"


def keypair() -> tuple[bytes, str]:
    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return seed, base64.b64encode(public).decode("ascii")


def trust(public_b64: str, channel: str = str(m.Channel.PREVIEW)) -> dict:
    return {str(channel): {KEY_ID: public_b64}}


def app_tree(root: Path, *, version: str, backend: str | None = None) -> Path:
    """A directory shaped like the packaged app, with a readable version marker."""
    bundle = root / "Sourcecado.app"
    contents = bundle / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "Info.plist").write_text(
        "<plist><dict><key>CFBundleShortVersionString</key>"
        f"<string>{version}</string></dict></plist>",
        encoding="utf-8",
    )
    (contents / "MacOS" / "Sourcecado").write_text(f"binary {version}", encoding="utf-8")
    if backend is not None:
        path = bundle / BACKEND_RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(backend, encoding="utf-8")
        path.chmod(0o755)
    return bundle


def artifact(tmp_path: Path, *, version: str, backend: str | None = None) -> Path:
    """Zip an app tree the way a publisher would, and return the archive path."""
    staging = tmp_path / f"artifact-{version}"
    staging.mkdir(parents=True, exist_ok=True)
    app_tree(staging, version=version, backend=backend)
    archive = shutil.make_archive(
        str(tmp_path / f"Sourcecado-{version}-macos-aarch64"), "zip", root_dir=staging
    )
    return Path(archive)


def identity(**overrides: Any) -> m.BuildIdentity:
    fields = {
        "product": PRODUCT,
        "channel": str(m.Channel.PREVIEW),
        "version": "0.0.1",
        "platform": "macos",
        "arch": "aarch64",
        "state_versions": m.registry_state_versions(),
    }
    fields.update(overrides)
    return m.BuildIdentity(**fields)


def installation(tmp_path: Path, **overrides: Any) -> Installation:
    bundle = overrides.pop("bundle_path", tmp_path / "Applications" / "Sourcecado.app")
    state = overrides.pop("state_root", tmp_path / "state")
    Path(bundle).parent.mkdir(parents=True, exist_ok=True)
    return Installation(
        identity=identity(**overrides), bundle_path=Path(bundle), state_root=Path(state)
    )


def document(artifact_path: Path, seed: bytes, **overrides: Any) -> dict:
    fields = {
        "product": PRODUCT,
        "channel": str(m.Channel.PREVIEW),
        "version": "0.0.2",
        "platform": "macos",
        "arch": "aarch64",
        "artifact_name": artifact_path.name,
        "artifact_size": artifact_path.stat().st_size,
        "artifact_sha256": m.sha256_file(artifact_path),
        "minimum_upgradable_version": "0.0.1",
        "state_versions": m.registry_state_versions(),
        "released_at": "2026-08-27T12:00:00+00:00",
        "commit": "0" * 40,
    }
    fields.update(overrides)
    return m.sign_manifest(m.build_manifest(**fields), seed=seed, key_id=KEY_ID)


def bundle_version(bundle: Path) -> str | None:
    """Read the version marker out of an installed tree, or None if it is gone."""
    plist = bundle / "Contents" / "Info.plist"
    if not plist.is_file():
        return None
    text = plist.read_text(encoding="utf-8")
    start = text.find("<string>")
    end = text.find("</string>")
    return text[start + len("<string>") : end] if 0 <= start < end else None
