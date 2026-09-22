"""Criterion 7: the four surfaces an update can leak a credential through.

Artifacts, logs, manifests, and diagnostic bundles. The manifest is the new one
and the most likely to leak, because it is assembled from build metadata --
a commit, a file name, whatever a workflow passes -- and build metadata is
exactly where a runner path or a pasted token ends up.

Every test here plants a credential and then proves it did not arrive. The
matcher is `coworker/bundle_redaction.py` in all four cases, because a second
matcher drifts and the one that drifts is the one that misses.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).parent))

import update_fixtures as fx  # noqa: E402
from coworker import bundle_redaction  # noqa: E402
from coworker.update_channel import UpdateStatus, apply_update  # noqa: E402
from state_fixtures import (  # noqa: E402
    PLANTED_API_KEY,
    PLANTED_BEARER,
    PLANTED_CANARIES,
)

KEY_ENV = "SOURCECADO_UPDATE_SIGNING_KEY"
KEY_ID = "test-preview-2026"


def _cli():
    """Load packaging/update_manifest.py the way a workflow runs it."""
    path = Path(__file__).resolve().parents[1] / "packaging" / "update_manifest.py"
    spec = importlib.util.spec_from_file_location("sourcecado_update_manifest", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def signing_key(monkeypatch):
    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    encoded = base64.b64encode(seed).decode("ascii")
    monkeypatch.setenv(KEY_ENV, encoded)
    return encoded, base64.b64encode(public).decode("ascii")


def _generate_argv(artifact: Path, output: Path, **overrides) -> list[str]:
    fields = {
        "--artifact": str(artifact),
        "--output": str(output),
        "--version": "0.0.2",
        "--minimum-from": "0.0.1",
        "--released-at": "2026-08-27T12:00:00+00:00",
        "--commit": "0" * 40,
        "--key-id": KEY_ID,
    }
    fields.update(overrides)
    argv = ["generate"]
    for name, value in fields.items():
        argv += [name, str(value)]
    return argv


# --- surface 1: the manifest ---------------------------------------------


def test_a_credential_in_build_metadata_never_reaches_a_manifest(
    tmp_path, signing_key, capsys
):
    cli = _cli()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    output = tmp_path / "update.json"

    code = cli.main(_generate_argv(artifact, output, **{"--commit": PLANTED_API_KEY}))

    assert code == cli.EXIT_LEAK
    assert not output.exists(), "a manifest that matched the scan was written anyway"
    error = capsys.readouterr().err
    assert "issued_credential" in error
    assert PLANTED_API_KEY not in error, "the refusal repeated the value it caught"


def test_a_home_path_in_build_metadata_never_reaches_a_manifest(
    tmp_path, signing_key, capsys
):
    cli = _cli()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    output = tmp_path / "update.json"

    code = cli.main(
        _generate_argv(
            artifact,
            output,
            **{"--artifact-name": "/Users/releasebot/build/Sourcecado.zip"},
        )
    )

    assert code == cli.EXIT_LEAK
    assert not output.exists()
    assert "home_path" in capsys.readouterr().err


def test_the_signing_key_itself_cannot_be_copied_into_a_manifest(
    tmp_path, signing_key, capsys
):
    """The registered scan catches the build's own environment, not just patterns."""
    cli = _cli()
    encoded, _public = signing_key
    artifact = fx.artifact(tmp_path, version="0.0.2")
    output = tmp_path / "update.json"

    code = cli.main(_generate_argv(artifact, output, **{"--commit": encoded}))

    assert code == cli.EXIT_LEAK
    assert not output.exists()
    error = capsys.readouterr().err
    assert "registered_secret" in error
    assert encoded not in error


def test_a_clean_manifest_is_written_and_then_verifies(tmp_path, signing_key, capsys):
    cli = _cli()
    _encoded, public = signing_key
    artifact = fx.artifact(tmp_path, version="0.0.2")
    output = tmp_path / "update.json"

    assert cli.main(_generate_argv(artifact, output)) == cli.EXIT_OK
    assert output.is_file()

    code = cli.main(
        [
            "verify",
            "--manifest",
            str(output),
            "--artifact",
            str(artifact),
            "--installed-version",
            "0.0.1",
            "--trust",
            f"{KEY_ID}={public}",
        ]
    )
    assert code == cli.EXIT_OK
    assert "OK" in capsys.readouterr().out


def test_a_written_manifest_passes_the_bundle_scan(tmp_path, signing_key):
    cli = _cli()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    output = tmp_path / "update.json"
    assert cli.main(_generate_argv(artifact, output)) == cli.EXIT_OK

    matches = bundle_redaction.scan_text(
        output.read_text(encoding="utf-8"),
        registered=PLANTED_CANARIES,
        home=tmp_path / "home",
        state_root=tmp_path / "state",
        location="update.json",
    )
    assert matches == ()


# --- surface 2: the artifact and what ships beside it --------------------


def test_a_credential_in_an_attestation_file_stops_the_manifest(
    tmp_path, signing_key, capsys
):
    cli = _cli()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    provenance = tmp_path / "provenance.txt"
    provenance.write_text(
        f"commit: abc123\nrunner_token: {PLANTED_BEARER}\n", encoding="utf-8"
    )
    output = tmp_path / "update.json"

    code = cli.main(
        _generate_argv(artifact, output) + ["--attest", str(provenance)]
    )

    assert code == cli.EXIT_LEAK
    assert not output.exists()
    error = capsys.readouterr().err
    assert "artifact:provenance.txt" in error
    assert PLANTED_BEARER not in error


def test_a_clean_attestation_file_does_not_stop_the_manifest(tmp_path, signing_key):
    cli = _cli()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    provenance = tmp_path / "provenance.txt"
    provenance.write_text("commit: abc123\nrunner: macOS 15 arm64\n", encoding="utf-8")
    output = tmp_path / "update.json"

    code = cli.main(_generate_argv(artifact, output) + ["--attest", str(provenance)])
    assert code == cli.EXIT_OK


# --- surface 3: what the updater tells the operator -----------------------


def _rolled_back_with(tmp_path, message: str):
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.1")
    fx.app_tree(install.bundle_path.parent, version="0.0.1")

    def explode(_installation):
        raise RuntimeError(message)

    return apply_update(
        fx.document(artifact, seed),
        installation=install,
        artifact_path=artifact,
        trust=fx.trust(public),
        runs=None,
        health_check=explode,
        drain_timeout=0.0,
        sleep=lambda _: None,
    )


def test_a_credential_in_a_failure_message_is_withheld_from_guidance(tmp_path):
    outcome = _rolled_back_with(
        tmp_path, f"the backend rejected X-Club-Token: {PLANTED_API_KEY}"
    )

    assert outcome.status is UpdateStatus.ROLLED_BACK
    assert PLANTED_API_KEY not in outcome.guidance
    assert "withheld" in outcome.guidance
    # The sentence the operator needs still survives the withholding.
    assert "0.0.1" in outcome.guidance


def test_a_home_path_in_a_failure_message_is_anchored_not_printed(tmp_path):
    outcome = _rolled_back_with(
        tmp_path, "could not execute /Users/operator/Library/Sourcecado/backend"
    )
    assert "/Users/operator" not in outcome.guidance
    assert "<home>" in outcome.guidance


# --- surface 4: what a diagnostic bundle could pick up --------------------


def test_the_whole_update_outcome_passes_the_scan_a_bundle_would_apply(tmp_path):
    outcome = _rolled_back_with(
        tmp_path, f"the backend rejected X-Club-Token: {PLANTED_API_KEY}"
    )

    matches = bundle_redaction.scan(
        outcome.to_dict(),
        registered=PLANTED_CANARIES,
        home=Path("/Users/operator"),
        state_root=tmp_path / "state",
        location="update",
    )
    assert matches == (), f"an update outcome would refuse a bundle: {matches}"


def test_the_outcome_is_plain_json_so_a_bundle_can_carry_it(tmp_path):
    outcome = _rolled_back_with(tmp_path, "the backend exited immediately")
    assert json.loads(json.dumps(outcome.to_dict()))["status"] == "rolled_back"
