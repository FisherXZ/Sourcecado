"""The authenticated update manifest: what a build must prove before it installs.

An update is the one moment Sourcecado takes a directory it did not build and
puts it where the application lives. Everything that makes that safe is in this
file, and it is all one idea: nothing is trusted because of where it came from.

The manifest binds the artifact to a signature over a closed field set. Exact
version, channel, platform, architecture, size, and SHA-256 digest are all
inside the signed bytes, so an artifact that differs from the one the publisher
signed -- in content, in target, or in identity -- cannot be described by a
manifest that verifies.

Three properties are load-bearing:

- **The field set is closed.** A key this build does not know refuses the
  manifest rather than being ignored. A field that is ignored is a field an
  attacker gets for free the day a future build starts reading it.
- **The signature is checked over canonical bytes with a domain prefix**, so a
  signature made for anything else in Sourcecado cannot be replayed here.
- **Trust is scoped by channel.** A key authorized for `stable` cannot sign a
  `preview` manifest and the reverse. That is what makes the preview channel
  opt-in rather than a toggle: a stable installation has no key that can
  authenticate a preview manifest, so it cannot drift onto the channel by
  accident, and a preview installation cannot be handed a stable artifact.

Verification returns a `Verification`, never an exception, so the refusal
reason can be shown to an operator and logged. Every path that cannot reach a
positive answer refuses; there is no default-allow branch.

`TRUSTED_KEYS` is empty. Sourcecado has no update signing key yet, so in a real
installation every manifest is refused. That is the correct behaviour until an
authorized identity exists, and `docs/update-channel.md` records what must be
supplied to change it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MANIFEST_VERSION = 1
SIGNATURE_ALGORITHM = "ed25519"
# Domain separation. A signature over these bytes cannot be a signature over
# anything else Sourcecado ever signs, and the reverse.
SIGNING_CONTEXT = b"sourcecado.update-manifest.v1"

# The closed field set. Adding a field here is a manifest version bump.
SIGNED_FIELDS: tuple[str, ...] = (
    "manifest_version",
    "product",
    "channel",
    "version",
    "platform",
    "arch",
    "artifact_name",
    "artifact_size",
    "artifact_sha256",
    "minimum_upgradable_version",
    "state_versions",
    "released_at",
    "commit",
)

_TEXT_FIELDS = frozenset(
    {
        "product",
        "channel",
        "version",
        "platform",
        "arch",
        "artifact_name",
        "artifact_sha256",
        "minimum_upgradable_version",
        "released_at",
        "commit",
    }
)
_INT_FIELDS = frozenset({"manifest_version", "artifact_size"})

PRODUCT = "sourcecado"

# Channel -> key id -> base64 Ed25519 public key.
#
# Empty on purpose. No authorized signing identity has been issued, so no
# manifest can verify. `tests/test_update_manifest.py` holds a tripwire that
# goes red the day a key is added, so adding one cannot happen quietly.
TRUSTED_KEYS: dict[str, dict[str, str]] = {}


class Channel(StrEnum):
    STABLE = "stable"
    PREVIEW = "preview"


class Refusal(StrEnum):
    """Why a manifest was refused. One reason per way of being wrong."""

    MALFORMED = "malformed"
    MISSING_FIELD = "missing_field"
    UNKNOWN_FIELD = "unknown_field"
    UNSUPPORTED_MANIFEST_VERSION = "unsupported_manifest_version"
    UNKNOWN_ALGORITHM = "unknown_algorithm"
    UNTRUSTED_KEY = "untrusted_key"
    BAD_SIGNATURE = "bad_signature"
    PRODUCT_MISMATCH = "product_mismatch"
    CHANNEL_MISMATCH = "channel_mismatch"
    PLATFORM_MISMATCH = "platform_mismatch"
    ARCH_MISMATCH = "arch_mismatch"
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_SIZE_MISMATCH = "artifact_size_mismatch"
    ARTIFACT_DIGEST_MISMATCH = "artifact_digest_mismatch"
    NOT_AN_UPGRADE = "not_an_upgrade"
    UNSUPPORTED_UPGRADE_PATH = "unsupported_upgrade_path"
    UNKNOWN_STORE = "unknown_store"
    STATE_DOWNGRADE = "state_downgrade"
    STATE_AHEAD_OF_MIGRATOR = "state_ahead_of_migrator"


@dataclass(frozen=True)
class BuildIdentity:
    """What the running installation is, and what state versions it can reach.

    `state_versions` is this build's migration registry, not what is on disk.
    It answers "which schema can this build produce", which is the question a
    manifest's own `state_versions` has to agree with before anything installs.
    """

    product: str
    channel: str
    version: str
    platform: str
    arch: str
    state_versions: Mapping[str, int]


@dataclass(frozen=True)
class BoundManifest:
    """A manifest that verified, reduced to what the caller acts on."""

    key_id: str
    product: str
    channel: str
    version: str
    platform: str
    arch: str
    artifact_name: str
    artifact_size: int
    artifact_sha256: str
    state_versions: Mapping[str, int]
    released_at: str
    commit: str


@dataclass(frozen=True)
class Verification:
    ok: bool
    refusal: Refusal | None = None
    detail: str = ""
    manifest: BoundManifest | None = None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def registry_state_versions() -> dict[str, int]:
    """The version of every registered store this build knows how to produce.

    Imported here rather than at module scope so the packaging scripts can use
    the manifest format without pulling in the whole sidecar.
    """
    from coworker import migrations

    return {spec.store_id: spec.current_version for spec in migrations.REGISTRY}


def canonical_bytes(signed: Mapping[str, Any]) -> bytes:
    """Exactly what gets signed. Key order in the document is not part of it."""
    body = json.dumps(
        dict(signed),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return SIGNING_CONTEXT + b"\n" + body.encode("utf-8")


def build_manifest(**fields: Any) -> dict[str, Any]:
    """Assemble the signed half, refusing any field the format does not carry."""
    fields.setdefault("manifest_version", MANIFEST_VERSION)
    unknown = sorted(set(fields) - set(SIGNED_FIELDS))
    if unknown:
        raise ValueError(f"unknown manifest field(s): {', '.join(unknown)}")
    missing = sorted(set(SIGNED_FIELDS) - set(fields))
    if missing:
        raise ValueError(f"missing manifest field(s): {', '.join(missing)}")
    return {name: fields[name] for name in SIGNED_FIELDS}


def sign_manifest(
    signed: Mapping[str, Any], *, seed: bytes, key_id: str
) -> dict[str, Any]:
    """Sign the canonical bytes with a raw Ed25519 seed."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(bytes(seed))
    signature = private.sign(canonical_bytes(signed))
    return {
        "signed": dict(signed),
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "key_id": str(key_id),
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def parse_version(text: Any) -> tuple[int, ...] | None:
    """A dotted numeric version, or None. None always refuses; it never passes."""
    parts = str(text).split(".")
    if not parts or len(parts) > 4:
        return None
    try:
        numbers = tuple(int(part) for part in parts)
    except ValueError:
        return None
    if any(number < 0 for number in numbers):
        return None
    return numbers


def _refuse(refusal: Refusal, detail: str) -> Verification:
    return Verification(ok=False, refusal=refusal, detail=detail)


def _shape(document: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not isinstance(document, dict):
        return None
    signed = document.get("signed")
    signature = document.get("signature")
    if not isinstance(signed, dict) or not isinstance(signature, dict):
        return None
    return signed, signature


def _field_types(signed: Mapping[str, Any]) -> str | None:
    for name in _TEXT_FIELDS:
        if not isinstance(signed.get(name), str):
            return f"{name} must be a string"
    for name in _INT_FIELDS:
        value = signed.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return f"{name} must be a non-negative integer"
    versions = signed.get("state_versions")
    if not isinstance(versions, dict):
        return "state_versions must be an object"
    for store, version in versions.items():
        if not isinstance(store, str):
            return "state_versions keys must be strings"
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            return f"state_versions.{store} must be a non-negative integer"
    return None


def _state_refusal(
    declared: Mapping[str, int], known: Mapping[str, int]
) -> Verification | None:
    """Compare what the artifact expects against what this build can produce.

    Three ways to disagree, and all three refuse. A store this build has never
    heard of cannot be reasoned about at all. A version above what this build's
    registry reaches is a migration this process cannot perform, so installing
    the artifact would strand the state below the binary. A version below is a
    downgrade, which would strand the state above it.
    """
    for store, version in sorted(declared.items()):
        if store not in known:
            return _refuse(
                Refusal.UNKNOWN_STORE,
                f"the artifact names a store this build does not have: {store}",
            )
        current = int(known[store])
        if version > current:
            return _refuse(
                Refusal.STATE_AHEAD_OF_MIGRATOR,
                f"{store} would need version {version}; this build reaches {current}",
            )
        if version < current:
            return _refuse(
                Refusal.STATE_DOWNGRADE,
                f"{store} is at version {current}; the artifact expects {version}",
            )
    return None


def verify_manifest(
    document: Any,
    *,
    installed: BuildIdentity,
    artifact_path: str | Path,
    trust: Mapping[str, Mapping[str, str]] | None = None,
) -> Verification:
    """Decide whether this artifact may be installed over this installation.

    Every branch either refuses or falls through to the next check. There is no
    path that returns `ok` without having reached the end.
    """
    keys = TRUSTED_KEYS if trust is None else trust

    shape = _shape(document)
    if shape is None:
        return _refuse(Refusal.MALFORMED, "a manifest is a signed document")
    signed, signature = shape

    missing = sorted(set(SIGNED_FIELDS) - set(signed))
    if missing:
        return _refuse(Refusal.MISSING_FIELD, f"missing: {', '.join(missing)}")
    unknown = sorted(set(signed) - set(SIGNED_FIELDS))
    if unknown:
        return _refuse(Refusal.UNKNOWN_FIELD, f"unknown: {', '.join(unknown)}")
    type_error = _field_types(signed)
    if type_error is not None:
        return _refuse(Refusal.MALFORMED, type_error)

    if int(signed["manifest_version"]) != MANIFEST_VERSION:
        return _refuse(
            Refusal.UNSUPPORTED_MANIFEST_VERSION,
            f"manifest version {signed['manifest_version']}; this build reads "
            f"{MANIFEST_VERSION}",
        )

    if str(signature.get("algorithm")) != SIGNATURE_ALGORITHM:
        return _refuse(
            Refusal.UNKNOWN_ALGORITHM,
            f"signature algorithm {signature.get('algorithm')!r} is not accepted",
        )

    # The channel selects which keys govern the document, so it is settled
    # before any key is looked up. A preview installation never consults a
    # stable key, and a stable installation never consults a preview key.
    channel = str(signed["channel"])
    if channel != str(installed.channel):
        return _refuse(
            Refusal.CHANNEL_MISMATCH,
            f"this installation is on {installed.channel}; the artifact is {channel}",
        )

    key_id = str(signature.get("key_id") or "")
    public_b64 = dict(keys.get(channel, {})).get(key_id)
    if not public_b64:
        return _refuse(
            Refusal.UNTRUSTED_KEY,
            f"no key {key_id!r} is trusted for the {channel} channel",
        )

    try:
        public = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(str(public_b64), validate=True)
        )
        raw = base64.b64decode(str(signature.get("value") or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        return _refuse(Refusal.MALFORMED, f"signature could not be read: {exc}")
    try:
        public.verify(raw, canonical_bytes(signed))
    except InvalidSignature:
        return _refuse(
            Refusal.BAD_SIGNATURE, "the signature does not cover this manifest"
        )

    # Authentic. Now: is it for this installation, and is it this artifact?
    if str(signed["product"]) != str(installed.product):
        return _refuse(
            Refusal.PRODUCT_MISMATCH, f"the artifact is {signed['product']!r}"
        )
    if str(signed["platform"]) != str(installed.platform):
        return _refuse(
            Refusal.PLATFORM_MISMATCH,
            f"the artifact is for {signed['platform']}; this is {installed.platform}",
        )
    if str(signed["arch"]) != str(installed.arch):
        return _refuse(
            Refusal.ARCH_MISMATCH,
            f"the artifact is for {signed['arch']}; this is {installed.arch}",
        )

    offered = parse_version(signed["version"])
    running = parse_version(installed.version)
    floor = parse_version(signed["minimum_upgradable_version"])
    if offered is None or running is None or floor is None:
        return _refuse(Refusal.MALFORMED, "a version must be dotted numbers")
    if offered <= running:
        return _refuse(
            Refusal.NOT_AN_UPGRADE,
            f"version {signed['version']} is not newer than {installed.version}",
        )
    if running < floor:
        return _refuse(
            Refusal.UNSUPPORTED_UPGRADE_PATH,
            f"{signed['version']} upgrades from {signed['minimum_upgradable_version']} "
            f"or later; this is {installed.version}",
        )

    state_refusal = _state_refusal(
        dict(signed["state_versions"]), dict(installed.state_versions)
    )
    if state_refusal is not None:
        return state_refusal

    path = Path(artifact_path)
    # One signed file, not a directory. A tree has no single digest to bind.
    if not path.is_file():
        return _refuse(Refusal.ARTIFACT_MISSING, f"no artifact file at {path.name}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        return _refuse(Refusal.ARTIFACT_MISSING, f"artifact unreadable: {exc.strerror}")
    if size != int(signed["artifact_size"]):
        return _refuse(
            Refusal.ARTIFACT_SIZE_MISMATCH,
            f"the artifact is {size} bytes; the manifest says "
            f"{signed['artifact_size']}",
        )
    if sha256_file(path) != str(signed["artifact_sha256"]):
        return _refuse(
            Refusal.ARTIFACT_DIGEST_MISMATCH,
            "the artifact does not hash to what the manifest signed",
        )

    return Verification(
        ok=True,
        manifest=BoundManifest(
            key_id=key_id,
            product=str(signed["product"]),
            channel=channel,
            version=str(signed["version"]),
            platform=str(signed["platform"]),
            arch=str(signed["arch"]),
            artifact_name=str(signed["artifact_name"]),
            artifact_size=int(signed["artifact_size"]),
            artifact_sha256=str(signed["artifact_sha256"]),
            state_versions=dict(signed["state_versions"]),
            released_at=str(signed["released_at"]),
            commit=str(signed["commit"]),
        ),
    )
