#!/usr/bin/env python3
"""Generate and verify the signed update manifest for a preview artifact.

Two subcommands, and they are deliberately separable. `generate` runs where the
signing key is, which is inside a protected CI job. `verify` runs anywhere, needs
no key, and is what an operator or a release reviewer uses to check that the
manifest on the release page really describes the file they downloaded.

Generation refuses rather than redacts. The manifest is assembled from build
metadata -- a commit, a file name, a timestamp, whatever the workflow passes --
and build metadata is exactly the kind of text that carries a runner path, an
environment variable, or a token someone pasted into a commit message. So the
assembled manifest and every attestation file named on the command line are
scanned with `coworker/bundle_redaction.py`, the same matcher the diagnostic
bundle fails closed on, and a match stops the build with nothing written.

The signing key is read from an environment variable and never appears in the
manifest, in the output, or in an error message.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.update_channel import manifest as m  # noqa: E402
from coworker.update_channel.redaction import (  # noqa: E402
    refusal_summary,
    registered_secrets,
    scan,
    scan_text,
)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_LEAK = 2


def _scan_everything(
    signed: dict, attestations: list[Path], *, state_root: Path, home: Path
) -> tuple:
    """Scan the manifest and the files that ship beside it. Report every match."""
    registered = registered_secrets(environ=os.environ)
    matches = list(
        scan(
            signed,
            registered=registered,
            home=home,
            state_root=state_root,
            location="manifest",
        )
    )
    for path in attestations:
        if not path.is_file():
            continue
        matches.extend(
            scan_text(
                path.read_text(encoding="utf-8", errors="replace"),
                registered=registered,
                home=home,
                state_root=state_root,
                location=f"artifact:{path.name}",
            )
        )
    return tuple(matches)


def generate(args: argparse.Namespace) -> int:
    artifact = Path(args.artifact)
    if not artifact.is_file():
        print(f"error: no artifact at {artifact.name}", file=sys.stderr)
        return EXIT_REFUSED

    signed = m.build_manifest(
        product=args.product,
        channel=args.channel,
        version=args.version,
        platform=args.platform,
        arch=args.arch,
        artifact_name=args.artifact_name or artifact.name,
        artifact_size=artifact.stat().st_size,
        artifact_sha256=m.sha256_file(artifact),
        minimum_upgradable_version=args.minimum_from,
        state_versions=m.registry_state_versions(),
        released_at=args.released_at,
        commit=args.commit,
    )

    matches = _scan_everything(
        signed,
        [Path(item) for item in args.attest],
        state_root=Path(args.state_root),
        home=Path(args.home),
    )
    if matches:
        # The categories and locations, never the values. Nothing is written.
        print(
            "error: refusing to write a manifest that matched the credential "
            f"scan: {refusal_summary(matches)}",
            file=sys.stderr,
        )
        return EXIT_LEAK

    raw = os.environ.get(args.key_env, "")
    if not raw:
        print(f"error: {args.key_env} is not set", file=sys.stderr)
        return EXIT_REFUSED
    try:
        seed = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        print(f"error: {args.key_env} is not base64", file=sys.stderr)
        return EXIT_REFUSED
    if len(seed) != 32:
        print(
            f"error: {args.key_env} is not a 32-byte Ed25519 seed", file=sys.stderr
        )
        return EXIT_REFUSED

    document = m.sign_manifest(signed, seed=seed, key_id=args.key_id)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {output.name} for {args.product} {args.version} ({args.channel})")
    print(f"  artifact  {signed['artifact_name']}")
    print(f"  sha256    {signed['artifact_sha256']}")
    print(f"  key id    {args.key_id}")
    return EXIT_OK


def verify(args: argparse.Namespace) -> int:
    try:
        document = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"REFUSED malformed: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    trust: dict[str, dict[str, str]] = {}
    for entry in args.trust:
        key_id, _, public = entry.partition("=")
        if not key_id or not public:
            print(f"error: --trust wants KEY_ID=BASE64, got {entry!r}", file=sys.stderr)
            return EXIT_REFUSED
        trust.setdefault(args.channel, {})[key_id] = public

    installed = m.BuildIdentity(
        product=args.product,
        channel=args.channel,
        version=args.installed_version,
        platform=args.platform,
        arch=args.arch,
        state_versions=m.registry_state_versions(),
    )
    outcome = m.verify_manifest(
        document,
        installed=installed,
        artifact_path=Path(args.artifact),
        trust=trust or None,
    )
    if not outcome.ok or outcome.manifest is None:
        print(f"REFUSED {outcome.refusal}: {outcome.detail}", file=sys.stderr)
        return EXIT_REFUSED
    bound = outcome.manifest
    print(
        f"OK   {bound.product} {bound.version} ({bound.channel}) for "
        f"{bound.platform}/{bound.arch}"
    )
    print(f"OK   artifact {bound.artifact_name} matches sha256 {bound.artifact_sha256}")
    print(f"OK   signed by {bound.key_id}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("generate", help="sign a manifest for a built artifact")
    make.add_argument("--artifact", required=True)
    make.add_argument("--artifact-name", default="")
    make.add_argument("--output", required=True)
    make.add_argument("--channel", default=str(m.Channel.PREVIEW))
    make.add_argument("--product", default=m.PRODUCT)
    make.add_argument("--version", required=True)
    make.add_argument("--platform", default="macos")
    make.add_argument("--arch", default="aarch64")
    make.add_argument("--minimum-from", required=True)
    make.add_argument("--released-at", required=True)
    make.add_argument("--commit", required=True)
    make.add_argument(
        "--attest",
        action="append",
        default=[],
        help="a text file shipped beside the artifact, scanned before signing",
    )
    make.add_argument("--key-env", default="SOURCECADO_UPDATE_SIGNING_KEY")
    make.add_argument("--key-id", required=True)
    make.add_argument("--state-root", default=str(Path.home() / ".sourcecado"))
    make.add_argument("--home", default=str(Path.home()))
    make.set_defaults(handler=generate)

    check = sub.add_parser("verify", help="check a manifest against an artifact")
    check.add_argument("--manifest", required=True)
    check.add_argument("--artifact", required=True)
    check.add_argument("--channel", default=str(m.Channel.PREVIEW))
    check.add_argument("--product", default=m.PRODUCT)
    check.add_argument("--installed-version", default="0.0.0")
    check.add_argument("--platform", default="macos")
    check.add_argument("--arch", default="aarch64")
    check.add_argument(
        "--trust",
        action="append",
        default=[],
        help="KEY_ID=BASE64_PUBLIC_KEY, repeatable",
    )
    check.set_defaults(handler=verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
