"""The authenticated update manifest, and every way it must refuse.

A manifest is the only thing standing between a signed preview build and an
arbitrary directory someone dropped on the machine. So the property under test
is not "a good manifest verifies"; it is that every single field it binds is
load-bearing, and that changing any one of them alone turns a pass into a
refusal with a named reason.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from coworker.update_channel import manifest as m

PRODUCT = "sourcecado"
KEY_ID = "test-preview-2026"


def _keypair() -> tuple[bytes, str]:
    """A signing seed and the base64 public key a client would trust."""
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


def _artifact(tmp_path, payload: bytes = b"Sourcecado.app payload"):
    path = tmp_path / "Sourcecado-0.0.2-macos-aarch64.zip"
    path.write_bytes(payload)
    return path


def _installed(**overrides) -> m.BuildIdentity:
    fields = {
        "product": PRODUCT,
        "channel": m.Channel.PREVIEW,
        "version": "0.0.1",
        "platform": "macos",
        "arch": "aarch64",
        "state_versions": {"people_db": 1, "conversation_db": 3},
    }
    fields.update(overrides)
    return m.BuildIdentity(**fields)


def _signed(artifact_path, **overrides) -> dict:
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
        "state_versions": {"people_db": 1, "conversation_db": 3},
        "released_at": "2026-08-27T12:00:00+00:00",
        "commit": "0" * 40,
    }
    fields.update(overrides)
    return m.build_manifest(**fields)


def _document(artifact_path, seed, **overrides) -> dict:
    return m.sign_manifest(_signed(artifact_path, **overrides), seed=seed, key_id=KEY_ID)


def _trust(public_b64: str, channel=m.Channel.PREVIEW) -> dict:
    return {str(channel): {KEY_ID: public_b64}}


def _verify(document, artifact_path, public_b64, **overrides):
    return m.verify_manifest(
        document,
        installed=_installed(**overrides.pop("installed", {})),
        artifact_path=artifact_path,
        trust=overrides["trust"] if "trust" in overrides else _trust(public_b64),
    )


# --- the happy path, so every refusal below is a real difference ----------


def test_a_signed_manifest_verifies_and_reports_what_it_binds(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    outcome = _verify(_document(artifact, seed), artifact, public)

    assert outcome.ok, outcome.detail
    assert outcome.refusal is None
    bound = outcome.manifest
    assert bound is not None
    assert bound.version == "0.0.2"
    assert bound.channel == str(m.Channel.PREVIEW)
    assert bound.platform == "macos"
    assert bound.arch == "aarch64"
    assert bound.artifact_sha256 == m.sha256_file(artifact)
    assert bound.key_id == KEY_ID


def test_the_manifest_binds_exactly_the_declared_field_set(tmp_path):
    seed, _ = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed)
    assert set(document["signed"]) == set(m.SIGNED_FIELDS)
    # Version, platform, architecture, checksum, and the signature itself.
    for required in (
        "version",
        "platform",
        "arch",
        "artifact_sha256",
        "channel",
    ):
        assert required in m.SIGNED_FIELDS
    assert document["signature"]["algorithm"] == m.SIGNATURE_ALGORITHM


# --- one changed field at a time -----------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "refusal"),
    [
        ("version", "0.0.1", m.Refusal.NOT_AN_UPGRADE),
        ("channel", "stable", m.Refusal.CHANNEL_MISMATCH),
        ("platform", "windows", m.Refusal.PLATFORM_MISMATCH),
        ("arch", "x86_64", m.Refusal.ARCH_MISMATCH),
        ("product", "not-sourcecado", m.Refusal.PRODUCT_MISMATCH),
        ("artifact_sha256", "f" * 64, m.Refusal.ARTIFACT_DIGEST_MISMATCH),
        ("artifact_size", 999999, m.Refusal.ARTIFACT_SIZE_MISMATCH),
        ("minimum_upgradable_version", "9.9.9", m.Refusal.UNSUPPORTED_UPGRADE_PATH),
    ],
)
def test_changing_one_bound_field_refuses_with_its_own_reason(
    tmp_path, field, value, refusal
):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    # Signed correctly. The signature is valid; the binding is what fails.
    outcome = _verify(_document(artifact, seed, **{field: value}), artifact, public)
    assert not outcome.ok
    assert outcome.refusal is refusal


def test_a_tampered_field_after_signing_breaks_the_signature(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed)
    document["signed"]["version"] = "9.9.9"
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.BAD_SIGNATURE


def test_a_replaced_artifact_refuses_even_with_a_valid_signature(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed)
    artifact.write_bytes(b"Sourcecado.app payload plus something else")
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    # Size changed too; either refusal is a refusal, but it must be one of them.
    assert outcome.refusal in {
        m.Refusal.ARTIFACT_DIGEST_MISMATCH,
        m.Refusal.ARTIFACT_SIZE_MISMATCH,
    }


def test_a_missing_artifact_refuses(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed)
    artifact.unlink()
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.ARTIFACT_MISSING


# --- the signature itself ------------------------------------------------


def test_a_key_that_is_not_trusted_for_this_channel_refuses(tmp_path):
    seed, _ = _keypair()
    _, other_public = _keypair()
    artifact = _artifact(tmp_path)
    outcome = _verify(_document(artifact, seed), artifact, other_public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.BAD_SIGNATURE


def test_a_key_trusted_only_for_stable_cannot_sign_a_preview_manifest(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    outcome = _verify(
        _document(artifact, seed),
        artifact,
        public,
        trust=_trust(public, channel=m.Channel.STABLE),
    )
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.UNTRUSTED_KEY


def test_an_empty_trust_store_refuses_everything(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    outcome = _verify(_document(artifact, seed), artifact, public, trust={})
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.UNTRUSTED_KEY


def test_an_unsigned_document_refuses(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed)
    document.pop("signature")
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.MALFORMED


def test_an_unknown_signature_algorithm_refuses(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed)
    document["signature"]["algorithm"] = "none"
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.UNKNOWN_ALGORITHM


def test_signature_bytes_are_not_reinterpreted_as_text(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed)
    document["signature"]["value"] = "not base64 at all !!!"
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.MALFORMED


# --- the closed field set ------------------------------------------------


def test_an_extra_field_in_a_signed_manifest_refuses(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed)
    document["signed"]["install_command"] = "curl | sh"
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    # An unknown field is caught before the signature is even considered, so a
    # future manifest cannot smuggle a field this build would ignore.
    assert outcome.refusal is m.Refusal.UNKNOWN_FIELD


def test_a_missing_field_refuses(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed)
    document["signed"].pop("arch")
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.MISSING_FIELD


def test_a_future_manifest_version_refuses(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed, manifest_version=m.MANIFEST_VERSION + 1)
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.UNSUPPORTED_MANIFEST_VERSION


def test_build_manifest_rejects_an_unknown_field_at_generation_time(tmp_path):
    artifact = _artifact(tmp_path)
    with pytest.raises(ValueError, match="install_command"):
        _signed(artifact, install_command="curl | sh")


# --- state compatibility is bound too ------------------------------------


def test_a_manifest_expecting_a_state_version_this_build_cannot_reach_refuses(
    tmp_path,
):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed, state_versions={"people_db": 9})
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.STATE_AHEAD_OF_MIGRATOR


def test_a_manifest_expecting_older_state_than_is_on_disk_refuses(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed, state_versions={"conversation_db": 2})
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.STATE_DOWNGRADE


def test_a_manifest_naming_a_store_this_build_has_never_heard_of_refuses(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed, state_versions={"ledger_of_the_future": 1})
    outcome = _verify(document, artifact, public)
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.UNKNOWN_STORE


# --- the shipped trust store ---------------------------------------------


def test_no_signing_key_is_shipped_yet_so_nothing_verifies_in_production(tmp_path):
    """A tripwire, not a preference.

    Sourcecado ships no update signing key, because no authorized Apple or
    Sourcecado identity has been issued yet. The trust store is therefore empty
    and every real manifest is refused. When a key is added, this test goes red
    and whoever added it must say so here.
    """
    assert m.TRUSTED_KEYS == {}
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    outcome = m.verify_manifest(
        _document(artifact, seed), installed=_installed(), artifact_path=artifact
    )
    assert not outcome.ok
    assert outcome.refusal is m.Refusal.UNTRUSTED_KEY


def test_registry_state_versions_reads_the_migration_registry():
    versions = m.registry_state_versions()
    from coworker import migrations

    assert versions == {
        spec.store_id: spec.current_version for spec in migrations.REGISTRY
    }
    assert versions["people_db"] >= 1


# --- canonical bytes -----------------------------------------------------


def test_key_order_does_not_change_what_was_signed(tmp_path):
    seed, public = _keypair()
    artifact = _artifact(tmp_path)
    document = _document(artifact, seed)
    shuffled = dict(reversed(list(document["signed"].items())))
    assert m.canonical_bytes(shuffled) == m.canonical_bytes(document["signed"])
    document["signed"] = shuffled
    assert _verify(document, artifact, public).ok


def test_canonical_bytes_are_domain_separated(tmp_path):
    artifact = _artifact(tmp_path)
    signed = _signed(artifact)
    assert m.canonical_bytes(signed).startswith(m.SIGNING_CONTEXT)
    assert json.loads(
        m.canonical_bytes(signed)[len(m.SIGNING_CONTEXT) + 1 :].decode("utf-8")
    ) == signed
