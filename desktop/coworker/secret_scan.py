"""Non-printing scan for a registered secret's absence from Sourcecado's local state.

Built for GitHub issue #38: after a credential is revoked, this answers one
question without ever risking the credential itself -- does the value still
appear anywhere in the conversations, transcripts, events, and logs this
machine keeps? It does not inspect the vault the credential came from; it
looks everywhere else a copy-paste, a chat message, a logged error, or a
pasted command summary could have carried it.

Reuse, not reinvention. Matching runs on `coworker.bundle_redaction.scan_text`,
the same matcher the diagnostic bundle export already refuses on. A second
matcher would drift from the first, and the one that drifts is the one that
misses a leak.

Every durable store this build knows about comes from
`coworker.migrations.REGISTRY`, so a store added later is scanned the next
time this runs without anyone updating a hand-written list here. The vault
stores -- `secrets`, `mcp_config`, `dotenv` -- are skipped on purpose:
`coworker/migrations.py` marks each one `secret_bearing`, they exist to hold
the *current* credential, and `secrets.json`'s own registry entry says it is
"Never read into a report". This scan reads it once, to learn the value to
search for, and never opens it as a target.

Output discipline matches `coworker/doctor.py`: bounded, and never the
matched value. A finding names a store id, a record or file identity -- a
table and rowid, a transcript file and line number, a JSON document's path --
and a count. Never the text that matched, never a window of characters
around it, and never the search term itself.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coworker import migrations
from coworker.bundle_redaction import registered_secret_values, scan_text
from coworker.migrations import StoreKind, open_readonly, state_root, store_path, store_present

STATE_LABEL = "<state>"
MAX_DETAIL_ROWS = 8
MAX_REPORT_CHARS = 16000


class NoRegisteredSecret(Exception):
    """Nothing was found to search for. Never a reason to report 'clean'."""


@dataclass(frozen=True)
class StoreFinding:
    """One store where the value showed up. Never the value; never a snippet."""

    store_id: str
    count: int
    detail: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"store_id": self.store_id, "count": self.count, "detail": list(self.detail)}


@dataclass(frozen=True)
class ScanReport:
    generated_at: str
    secret_key: str | None
    needle_count: int
    stores_scanned: tuple[str, ...]
    findings: tuple[StoreFinding, ...]
    unreadable: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "state_root": STATE_LABEL,
            "secret_key": self.secret_key,
            "values_searched": self.needle_count,
            "stores_scanned": list(self.stores_scanned),
            "clean": self.clean,
            "findings": [item.to_dict() for item in self.findings],
            "unreadable": list(self.unreadable),
        }

    def render(self) -> str:
        return _render(self)


# --- reading the registered-secret store ----------------------------------


def _load_needles(root: Path, secret_key: str | None) -> frozenset[str]:
    """Every value worth searching for, read straight from the vault.

    An owner names a key, such as `apollo`, never the value itself -- so the
    value is never typed into a shell, never lands in a history file, and
    never has to be pasted into a ticket. Omitting the key searches for every
    value the vault currently holds.
    """
    spec = migrations.spec_for("secrets")
    if not store_present(root, spec):
        return frozenset()
    try:
        payload = json.loads(store_path(root, spec).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(payload, dict):
        return frozenset()
    if secret_key is not None:
        if secret_key not in payload:
            return frozenset()
        payload = {secret_key: payload[secret_key]}
    return registered_secret_values(payload)


# --- per-store scanning ----------------------------------------------------


def _bounded(identities: list[str]) -> tuple[str, ...]:
    if len(identities) <= MAX_DETAIL_ROWS:
        return tuple(identities)
    kept = identities[: MAX_DETAIL_ROWS - 1]
    kept.append(f"and {len(identities) - (MAX_DETAIL_ROWS - 1)} more")
    return tuple(kept)


def _relative(path: Path, root: Path) -> str:
    try:
        return f"{STATE_LABEL}/{path.relative_to(root)}"
    except ValueError:
        return STATE_LABEL


def _matched(
    text: str, needles: frozenset[str], *, home: Path, root: Path, location: str
) -> bool:
    matches = scan_text(text, registered=needles, home=home, state_root=root, location=location)
    return any(match.category == "registered_secret" for match in matches)


def _scan_sqlite(spec: migrations.StoreSpec, root: Path, needles: frozenset[str], home: Path) -> list[str]:
    identities: list[str] = []
    conn = open_readonly(store_path(root, spec))
    try:
        for table in sorted(migrations.table_names(conn)):
            try:
                rows = conn.execute(f'SELECT rowid AS "_rowid", * FROM "{table}"')
            except sqlite3.DatabaseError:
                continue
            for row in rows:
                for key in row.keys():
                    if key == "_rowid":
                        continue
                    value = row[key]
                    if value is None:
                        continue
                    location = f"{table}.{key}"
                    if _matched(str(value), needles, home=home, root=root, location=location):
                        identities.append(f"{table}.{key} rowid {row['_rowid']}")
    finally:
        conn.close()
    return identities


def _scan_jsonl_file(path: Path, root: Path, needles: frozenset[str], home: Path) -> list[str]:
    identities: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    relative = _relative(path, root)
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if _matched(line, needles, home=home, root=root, location=f"{relative}:{number}"):
            identities.append(f"{relative} line {number}")
    return identities


def _scan_jsonl_store(spec: migrations.StoreSpec, root: Path, needles: frozenset[str], home: Path) -> list[str]:
    if spec.kind is StoreKind.JSONL_DIR:
        files = sorted(store_path(root, spec).glob("*.jsonl"))
    else:
        path = store_path(root, spec)
        files = [path] if path.is_file() else []
    identities: list[str] = []
    for path in files:
        identities.extend(_scan_jsonl_file(path, root, needles, home))
    return identities


def _scan_one_file(spec: migrations.StoreSpec, root: Path, needles: frozenset[str], home: Path) -> list[str]:
    """A JSON document or an opaque file that is not secret-bearing: one blob of text."""
    path = store_path(root, spec)
    text = path.read_text(encoding="utf-8", errors="replace")
    relative = _relative(path, root)
    if _matched(text, needles, home=home, root=root, location=relative):
        return [relative]
    return []


def _scan_directory(spec: migrations.StoreSpec, root: Path, needles: frozenset[str], home: Path) -> list[str]:
    identities: list[str] = []
    for path in sorted(store_path(root, spec).rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = _relative(path, root)
        if _matched(text, needles, home=home, root=root, location=relative):
            identities.append(relative)
    return identities


def _scan_store(spec: migrations.StoreSpec, root: Path, needles: frozenset[str], home: Path) -> list[str] | None:
    """None means the store could not be read; distinct from a clean empty list."""
    try:
        if spec.kind is StoreKind.SQLITE:
            return _scan_sqlite(spec, root, needles, home)
        if spec.kind in (StoreKind.JSONL_DIR, StoreKind.JSONL_LOG):
            return _scan_jsonl_store(spec, root, needles, home)
        if spec.kind in (StoreKind.JSON_DOCUMENT, StoreKind.OPAQUE_FILE):
            return _scan_one_file(spec, root, needles, home)
        if spec.kind is StoreKind.DIRECTORY:
            return _scan_directory(spec, root, needles, home)
    except (OSError, sqlite3.Error, UnicodeError):
        return None
    return []  # pragma: no cover - every current StoreKind is handled above


# --- the scan ---------------------------------------------------------------


def scan_state(
    root: str | Path,
    *,
    secret_key: str | None = None,
    home: Path | None = None,
) -> ScanReport:
    """Scan every durable, non-vault store for a registered secret's value.

    Raises `NoRegisteredSecret` when there is nothing to search for -- an
    unknown key, or an empty vault -- so a scan can never report "clean" for
    a search it never actually ran.
    """
    root = Path(root).expanduser()
    home = home if home is not None else Path.home()
    needles = _load_needles(root, secret_key)
    if not needles:
        raise NoRegisteredSecret(
            f"no registered secret found under key {secret_key!r}"
            if secret_key is not None
            else "no registered secret values found to search for"
        )

    stores_scanned: list[str] = []
    findings: list[StoreFinding] = []
    unreadable: list[str] = []
    for spec in migrations.REGISTRY:
        if spec.secret_bearing or not store_present(root, spec):
            continue
        stores_scanned.append(spec.store_id)
        identities = _scan_store(spec, root, needles, home)
        if identities is None:
            unreadable.append(spec.store_id)
        elif identities:
            findings.append(StoreFinding(spec.store_id, len(identities), _bounded(identities)))

    return ScanReport(
        generated_at=datetime.now(UTC).isoformat(),
        secret_key=secret_key,
        needle_count=len(needles),
        stores_scanned=tuple(stores_scanned),
        findings=tuple(findings),
        unreadable=tuple(unreadable),
    )


# --- rendering ---------------------------------------------------------------


def _render(report: ScanReport) -> str:
    lines = ["Sourcecado secret scan", f"state directory {STATE_LABEL}", ""]
    lines.append(f"secret key       {report.secret_key or '(all registered)'}")
    lines.append(f"values searched  {report.needle_count}")
    lines.append(f"stores scanned   {len(report.stores_scanned)}")
    if report.unreadable:
        lines.append(f"unreadable       {', '.join(report.unreadable)}")
    lines.append("")
    if report.clean:
        lines.append("result           clean -- no match found")
    else:
        lines.append("result           MATCH FOUND -- value present in current state")
        for item in report.findings:
            lines.append(f"  {item.store_id:<24} {item.count} match(es)")
            for entry in item.detail:
                lines.append(f"      - {entry}")
    rendered = "\n".join(lines) + "\n"
    if len(rendered) > MAX_REPORT_CHARS:
        rendered = rendered[: MAX_REPORT_CHARS - 32].rstrip() + "\n... output truncated\n"
    return rendered


# --- command line -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coworker.secret_scan",
        description=(
            "Confirm a registered secret's absence from Sourcecado's local "
            "conversations, transcripts, events, and logs. Never prints the "
            "value; reports store, location, and count only."
        ),
    )
    parser.add_argument(
        "--secret-key",
        help="key in the registered-secret store to search for, e.g. apollo; "
        "omit to search for every registered value",
    )
    parser.add_argument("--state", help="state directory (defaults to CLUB_STATE_DIR)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    root = Path(args.state).expanduser() if args.state else state_root()

    try:
        report = scan_state(root, secret_key=args.secret_key)
    except NoRegisteredSecret as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        # A scan that crashes must not let a traceback carry a fragment of
        # what it was looking for. Diagnosing the cause is not this tool's job.
        print("secret scan failed; nothing was reported", file=sys.stderr)
        return 2

    print(json.dumps(report.to_dict(), indent=2) if args.json else report.render(), end="")
    return 0 if report.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
