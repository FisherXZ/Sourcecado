"""A redacted diagnostic bundle for one failed or interrupted run.

A bundle is the most dangerous artifact Sourcecado produces, because its whole
purpose is to be handed to someone else. Everything here is arranged around
that one fact.

Three independent layers stand between local state and a bundle:

1. The run receipt's closed field allowlist, upstream in `run_receipt.py`.
2. The state report's bounded, redacted output contract, upstream in
   `doctor.py`, whose findings this module copies without widening.
3. This module's own projection plus `bundle_redaction`, which imports neither
   of the first two. A scan that leaned on the layers above it would keep
   passing after one of them changed.

Free text in a bundle is limited to the bounded runtime notes a run recorded
(`reason`, `error_summary`) and the bounded findings the state report already
redacts. Everything else is an identifier, an enum, a count, or a timestamp.
Prompts, message bodies, source titles and URLs, tool arguments and results,
command output, credentials, authorization material, private reasoning, and raw
home paths have no field to land in.

Two properties are structural rather than aspirational:

**Fail closed.** If the scan matches, nothing is produced. Not a redacted
bundle, not a warning file. A match is evidence that something reached the
document by a path this module did not model, and refusing is the only honest
response.

**No partial artifact.** The archive is assembled entirely in memory and
scanned there. Only complete, scanned bytes are ever written, into a private
temporary directory on the destination filesystem, and only `os.replace` puts
them in place. Any failure removes that directory, so no half-written file can
be mistaken for a bundle.

This module opens no network client and imports nothing that could. It is also
the offline inspector: `python -m coworker.diagnostic_bundle inspect <archive>`
verifies the manifest and every checksum with no Sourcecado state at all.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coworker.bundle_redaction import ScanMatch, scan, scan_text, scrub

BUNDLE_VERSION = 1
ARCHIVE_PREFIX = "sourcecado-diagnostic-"
MANIFEST_NAME = "manifest.json"
CHECKSUMS_NAME = "checksums.txt"

# Every evidence file in a bundle. The manifest and the checksum list are
# generated from these and are not themselves evidence.
MEMBERS = (
    "connectors.json",
    "environment.json",
    "health.json",
    "logs.json",
    "run.json",
    "state.json",
    "subject.json",
)

# How much history a bundle carries. Bounds are counts, never durations: a
# time window would make two exports of identical state differ.
HEALTH_WINDOW_RUNS = 50
HEALTH_RECENT = 20
MAX_LOG_RECORDS = 100
MAX_ENTRIES = 200

PREVIEW_FINDINGS = 8
PREVIEW_LOG_RECORDS = 12
MAX_PREVIEW_CHARS = 16000

_ID = 256
_SHORT = 128
_SUMMARY = 512
_LIST = 20

# A fixed member timestamp. A real one would vary between two exports of the
# same state, which criterion 6 forbids.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
MEMBER_MODE = 0o600
_DIRECTORY_MODE = 0o700

LOG_RECORD_FIELDS = frozenset(
    {
        "index",
        "type",
        "state",
        "turn_id",
        "event_id",
        "tool_call_id",
        "tool_name",
        "ok",
        "action",
        "status",
        "provider",
        "model",
        "reason_length",
        "attempt",
        "delay_ms",
        "resolution",
        "execution_status",
        "decision",
        "scope",
        "requested_at",
        "resolved_at",
        "message_length",
        "delta_length",
        "text_length",
    }
)

EVIDENCE_CATEGORIES = (
    {
        "id": "subject",
        "title": "What this bundle is about",
        "description": (
            "The exact run or state-integrity finding the export started from, "
            "and the list of categories below."
        ),
    },
    {
        "id": "environment",
        "title": "Versions and platform",
        "description": (
            "Sourcecado's version and slice, the Python runtime, and the "
            "operating system family, release, and machine architecture. The "
            "machine's hostname is not included."
        ),
    },
    {
        "id": "run",
        "title": "Run lifecycle and timing",
        "description": (
            "Lifecycle codes for the run, model attempts and their durations, "
            "tool calls and their durations, token counts, approval decisions "
            "resolved against the owner-native record, and how the run ended. "
            "No prompt, no message body, no tool argument, no tool result."
        ),
    },
    {
        "id": "health",
        "title": "Bounded health history",
        "description": (
            f"The last {HEALTH_WINDOW_RUNS} runs by state, trigger, and "
            "outcome, so a reader can tell an isolated failure from a pattern. "
            "Bounded by count, never by a time window."
        ),
    },
    {
        "id": "state",
        "title": "State integrity and migration state",
        "description": (
            "Every local store's version against the registry, the pending "
            "migration steps, and the integrity findings, copied from the "
            "state report's own bounded and redacted output."
        ),
    },
    {
        "id": "connectors",
        "title": "Connector status",
        "description": (
            "Each connector's identity, status, and missing permissions. No "
            "authorization material and no connected account address."
        ),
    },
    {
        "id": "logs",
        "title": "Redacted log records",
        "description": (
            f"Up to {MAX_LOG_RECORDS} structured event records from the run's "
            "session, projected onto a closed field list. Assistant text, tool "
            "arguments, tool results, and error text are carried as lengths."
        ),
    },
)

EXCLUDED = (
    "Prompts, persona text, and message bodies.",
    "Source excerpts, source titles, and source URLs.",
    "Tool arguments, tool results, and command output.",
    "Credentials, authorization headers, and OAuth grants.",
    "Private model reasoning.",
    "Raw home paths. Anything anchored in a home directory reads as <home>.",
    "The machine's hostname and the connected account's email address.",
)


class BundleScanFailed(Exception):
    """The pre-export scan matched, so nothing was produced.

    The message names categories and locations. It never carries the matched
    value: a refusal that printed the secret would be the leak it prevented.
    """

    def __init__(self, matches: tuple[ScanMatch, ...] | list[ScanMatch]) -> None:
        self.matches = tuple(matches)
        detail = ", ".join(
            f"{match.category} at {match.location}" for match in self.matches
        )
        super().__init__(f"diagnostic bundle refused: {detail}")


@dataclass(frozen=True, kw_only=True)
class BundleSubject:
    """The exact thing an export starts from."""

    kind: str
    run_id: str | None = None
    check: str | None = None
    store_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "run":
            return {"kind": "run", "run_id": _text(self.run_id, _ID) or ""}
        return {
            "kind": _text(self.kind, _SHORT) or "",
            "check": _text(self.check, _SHORT),
            "store_id": _text(self.store_id, _SHORT),
        }


@dataclass(frozen=True, kw_only=True)
class BundleSources:
    """Everything a bundle reads, resolved by the caller.

    Passing these in rather than reaching for them keeps this module free of
    the state directory, the run store, and the connector layer, so the whole
    projection can be tested against exact inputs.
    """

    application: dict[str, Any]
    doctor: dict[str, Any]
    connectors: list[dict[str, Any]]
    events: list[dict[str, Any]]
    state_root: Path
    receipt: dict[str, Any] | None = None
    recent_runs: dict[str, Any] = field(default_factory=dict)
    registered_secrets: frozenset[str] = frozenset()
    home: Path = field(default_factory=Path.home)


# --- the document --------------------------------------------------------


def build_document(
    subject: BundleSubject, sources: BundleSources
) -> dict[str, dict[str, Any]]:
    """Project every source onto the bundle's own allowlist.

    Pure: no clock, no randomness, no filesystem. Two calls against identical
    state return equal documents, which is what makes criterion 6 a property of
    the code rather than a claim about it.
    """
    document = {
        "subject.json": _subject(subject, sources),
        "environment.json": _environment(sources),
        "run.json": _run(sources.receipt),
        "health.json": _health(sources.recent_runs),
        "state.json": _state(sources.doctor),
        "connectors.json": _connectors(sources.connectors),
        "logs.json": _logs(sources.events),
    }
    return scrub(document, home=sources.home, state_root=sources.state_root)


def _subject(subject: BundleSubject, sources: BundleSources) -> dict[str, Any]:
    included = {
        "subject": True,
        "environment": True,
        "run": sources.receipt is not None,
        "health": bool((sources.recent_runs or {}).get("runs")),
        "state": bool(sources.doctor),
        "connectors": bool(sources.connectors),
        "logs": bool(sources.events),
    }
    return {
        "bundle_version": BUNDLE_VERSION,
        "subject": subject.to_dict(),
        "evidence_categories": [
            {**category, "included": included[category["id"]]}
            for category in EVIDENCE_CATEGORIES
        ],
        "excluded": list(EXCLUDED),
    }


def _environment(sources: BundleSources) -> dict[str, Any]:
    application = sources.application or {}
    return {
        "application": {
            "name": _text(application.get("name"), _SHORT),
            "version": _text(application.get("version"), _SHORT),
            "slice": _number(application.get("slice")),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        # `platform.node()` is the machine's hostname and is deliberately absent.
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
    }


_RUN_FIELDS = (
    "run_id",
    "session_id",
    "person_id",
    "parent_run_id",
    "trigger",
    "state",
    "goal_fingerprint",
    "created_at",
    "updated_at",
    "finished_at",
    "version",
)
_ATTEMPT_FIELDS = (
    "attempt_id",
    "provider",
    "model_id",
    "status",
    "error_class",
    "duration_ms",
    "outcome",
    "first_sequence",
    "last_sequence",
)
_CALL_FIELDS = (
    "tool_call_id",
    "tool_name",
    "status",
    "error_class",
    "duration_ms",
    "item_count",
    "lifecycle",
    "first_sequence",
    "last_sequence",
)
_DECISION_FIELDS = (
    "approval_id",
    "requested",
    "requested_sequence",
    "tool_name",
    "decision",
    "state",
    "execution_status",
    "evidence",
)
_RECOVERY_FIELDS = ("sequence", "kind", "state", "tool_call_id", "created_at")
_NOTE_FIELDS = ("sequence", "kind")
# Every free-text field a run recorded, carried as a length and never as a
# value. `reason` and `error_summary` are bounded and redacted at write time,
# but both are written from caller text: a body, a traceback, or a path lands
# in them whenever the runtime summarises what went wrong. `error_class` and
# the lifecycle codes carry the diagnostic signal instead.
_RUN_TEXT_LENGTHS = (
    ("reason", "reason_length"),
    ("error_summary", "error_summary_length"),
)


def _run(receipt: dict[str, Any] | None) -> dict[str, Any]:
    """Re-project the receipt onto this module's own field list.

    The receipt is already an allowlist. Naming the fields again here is the
    point: if a field is added upstream, it does not silently join every bundle
    an operator ever shares.
    """
    if not receipt:
        return {
            "run": None,
            "record": None,
            "prompt": None,
            "model_attempts": None,
            "usage": None,
            "tools": None,
            "sources": None,
            "artifacts": None,
            "approvals": None,
            "recovery": None,
            "rationale": None,
            "outcome": None,
        }
    run = receipt.get("run") or {}
    record = receipt.get("record") or {}
    prompt = receipt.get("prompt") or {}
    attempts = receipt.get("model_attempts") or {}
    tools = receipt.get("tools") or {}
    approvals = receipt.get("approvals") or {}
    outcome = receipt.get("outcome") or {}
    result = outcome.get("result") or {}
    projected_run = _pick(run, _RUN_FIELDS)
    # A duration measured against "now" would differ between two exports of one
    # unchanged run. Only a finished run has a duration a bundle may carry.
    if run.get("finished_at"):
        projected_run["duration_ms"] = _number(run.get("duration_ms"))
    return {
        "run": projected_run,
        "record": {
            "complete": bool(record.get("complete")),
            "checkpoints_stored": _number(record.get("checkpoints_stored")) or 0,
            "checkpoints_expected": _number(record.get("checkpoints_expected")) or 0,
            "pruned_through_sequence": _number(record.get("pruned_through_sequence")),
            "damaged": bool(record.get("damaged")),
            "unsupported": _strings(record.get("unsupported")),
        },
        "prompt": {
            "evidence": _text(prompt.get("evidence"), _SHORT),
            "persona_id": _text(prompt.get("persona_id"), _SHORT),
            "prompt_version": _text(prompt.get("prompt_version"), _SHORT),
        },
        "model_attempts": {
            "evidence": _text(attempts.get("evidence"), _SHORT),
            "attempt_count": _number(attempts.get("attempt_count")) or 0,
            "distinct_models": _number(attempts.get("distinct_models")) or 0,
            "attempts": _entries(attempts.get("attempts"), _ATTEMPT_FIELDS),
        },
        "usage": {
            "evidence": _text((receipt.get("usage") or {}).get("evidence"), _SHORT),
            "totals": {
                str(key)[:_SHORT]: value
                for key, value in ((receipt.get("usage") or {}).get("totals") or {}).items()
                if _number(value) is not None
            },
        },
        "tools": {
            "evidence": _text(tools.get("evidence"), _SHORT),
            "pending_count": _number(tools.get("pending_count")) or 0,
            "unknown_count": _number(tools.get("unknown_count")) or 0,
            "calls": _entries(tools.get("calls"), _CALL_FIELDS, lengths=_RUN_TEXT_LENGTHS),
        },
        # Reference titles and URLs are document content. A bundle counts them
        # by provider and never names them.
        "sources": _references(receipt.get("sources"), "provider"),
        "artifacts": _references(receipt.get("artifacts"), "artifact_type"),
        "approvals": {
            "evidence": _text(approvals.get("evidence"), _SHORT),
            # `actor` names a person and is deliberately dropped.
            "decisions": _entries(approvals.get("decisions"), _DECISION_FIELDS),
        },
        "recovery": {
            "evidence": _text((receipt.get("recovery") or {}).get("evidence"), _SHORT),
            "events": _entries(
                (receipt.get("recovery") or {}).get("events"),
                _RECOVERY_FIELDS,
                lengths=_RUN_TEXT_LENGTHS,
            ),
        },
        "rationale": {
            "evidence": _text((receipt.get("rationale") or {}).get("evidence"), _SHORT),
            "notes": _entries(
                (receipt.get("rationale") or {}).get("notes"),
                _NOTE_FIELDS,
                lengths=_RUN_TEXT_LENGTHS,
            ),
        },
        "outcome": {
            "evidence": _text(outcome.get("evidence"), _SHORT),
            "state": _text(outcome.get("state"), _SHORT),
            "open": bool(outcome.get("open")),
            "finished_at": _text(outcome.get("finished_at"), _SHORT),
            "duration_ms": _number(outcome.get("duration_ms")),
            # `error` and `message_id` are dropped: the class says what failed,
            # and the free-text error is where a path or a body would ride out.
            "result": {
                "status": _text(result.get("status"), _SHORT),
                "error_class": _text(result.get("error_class"), _SHORT),
                "text_length": _number(result.get("text_length")),
            }
            if result
            else None,
        },
    }


def _references(section: Any, group: str) -> dict[str, Any]:
    """Counts by provider or type. Never an id, a title, or a URL."""
    block = section or {}
    refs = block.get("refs") if isinstance(block, dict) else None
    tally: dict[str, dict[str, int]] = {}
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict):
            continue
        key = _text(ref.get(group), _SHORT) or "unknown"
        entry = tally.setdefault(key, {"count": 0, "stale_count": 0})
        entry["count"] += 1
        if ref.get("stale"):
            entry["stale_count"] += 1
    return {
        "evidence": _text(block.get("evidence"), _SHORT) if isinstance(block, dict) else None,
        "count": sum(entry["count"] for entry in tally.values()),
        "stale_count": sum(entry["stale_count"] for entry in tally.values()),
        "providers": [
            {group: key, **tally[key]} for key in sorted(tally)[:MAX_ENTRIES]
        ],
    }


def _health(page: Any) -> dict[str, Any]:
    """Recent run outcomes, bounded by count so the window cannot drift."""
    runs = (page or {}).get("runs") if isinstance(page, dict) else None
    rows = [row for row in (runs or []) if isinstance(row, dict)]
    states: dict[str, int] = {}
    triggers: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    for row in rows:
        _tally(states, _text(row.get("state"), _SHORT))
        _tally(triggers, _text(row.get("trigger"), _SHORT))
        _tally(outcomes, _text(row.get("outcome_status"), _SHORT))
    return {
        "runs_considered": len(rows),
        "window_runs": HEALTH_WINDOW_RUNS,
        "truncated": bool((page or {}).get("truncated")) if isinstance(page, dict) else False,
        "states": states,
        "triggers": triggers,
        "outcomes": outcomes,
        "recent": [
            {
                "run_id": _text(row.get("run_id"), _ID),
                "trigger": _text(row.get("trigger"), _SHORT),
                "state": _text(row.get("state"), _SHORT),
                "outcome_status": _text(row.get("outcome_status"), _SHORT),
                "created_at": _text(row.get("created_at"), _SHORT),
                "finished_at": _text(row.get("finished_at"), _SHORT),
                "duration_ms": _number(row.get("duration_ms")),
                "source_count": _number(row.get("source_count")) or 0,
                "artifact_count": _number(row.get("artifact_count")) or 0,
                "approval_count": _number(row.get("approval_count")) or 0,
            }
            for row in rows[:HEALTH_RECENT]
        ],
    }


_STORE_FIELDS = (
    "store_id",
    "kind",
    "version_channel",
    "present",
    "version",
    "target_version",
    "status",
)
_FINDING_FIELDS = (
    "check",
    "store_id",
    "severity",
    "repair",
    "summary",
    "record_count",
    "blocking",
)


def _state(report: Any) -> dict[str, Any]:
    """Copy the state report's own contract. Never widen it."""
    source = report if isinstance(report, dict) else {}
    stores = [item for item in (source.get("stores") or []) if isinstance(item, dict)]
    findings = []
    for item in (source.get("findings") or [])[:MAX_ENTRIES]:
        if not isinstance(item, dict):
            continue
        projected = _pick(item, _FINDING_FIELDS)
        projected["blocking"] = bool(item.get("blocking"))
        projected["detail"] = _strings(item.get("detail"))
        findings.append(projected)
    return {
        "healthy": bool(source.get("healthy")),
        "blocked": bool(source.get("blocked")),
        "stores": [_pick(item, _STORE_FIELDS) for item in stores[:MAX_ENTRIES]],
        "findings": findings,
        "migration": {
            "target_versions": {
                str(item.get("store_id")): _number(item.get("target_version"))
                for item in stores
                if item.get("store_id")
            },
            "pending": [
                {
                    "store_id": _text(item.get("store_id"), _SHORT),
                    "from_version": _number(item.get("version")),
                    "to_version": _number(item.get("target_version")),
                }
                for item in stores
                if item.get("version") is not None
                and item.get("version") != item.get("target_version")
            ],
        },
        "dependencies": [
            {
                "name": _text(item.get("name"), _SHORT),
                "required": bool(item.get("required")),
                "present": bool(item.get("present")),
            }
            for item in (source.get("dependencies") or [])
            if isinstance(item, dict)
        ],
        "proposed_repairs": [
            {
                "action": _text(item.get("action"), _SHORT),
                "store_id": _text(item.get("store_id"), _SHORT),
                "record_count": _number(item.get("record_count")) or 0,
            }
            for item in (source.get("proposed_repairs") or [])
            if isinstance(item, dict)
        ],
    }


def _connectors(connectors: Any) -> dict[str, Any]:
    """Identity, status, and missing permissions. No grant, no address."""
    projected = []
    for item in (connectors or [])[:MAX_ENTRIES]:
        if not isinstance(item, dict):
            continue
        health = item.get("health") if isinstance(item.get("health"), dict) else {}
        recovery = item.get("recovery") if isinstance(item.get("recovery"), dict) else {}
        projected.append(
            {
                "id": _text(item.get("id"), _SHORT),
                "title": _text(item.get("title"), _SHORT),
                "status": _text(item.get("status"), _SHORT),
                "catalog_group": _text(item.get("catalog_group"), _SHORT),
                "health_category": _text(health.get("category"), _SHORT),
                "health_label": _text(health.get("label"), _SHORT),
                "recovery_category": _text(recovery.get("category"), _SHORT),
                "required_scopes": _strings(item.get("required_scopes")),
                "missing_scopes": _strings(item.get("missing_scopes")),
                "supported_actions": _strings(item.get("supported_actions")),
                "available_actions": _strings(item.get("available_actions")),
            }
        )
    return {"connectors": projected}


_LOG_SCALARS = (
    ("type", _SHORT),
    ("state", _SHORT),
    ("event_id", _ID),
    ("action", _SHORT),
    ("status", _SHORT),
    ("provider", _SHORT),
    ("model", _SHORT),
    ("resolution", _SHORT),
    ("execution_status", _SHORT),
    ("decision", _SHORT),
    ("scope", _SHORT),
    ("requested_at", _SHORT),
    ("resolved_at", _SHORT),
)
_LOG_NUMBERS = ("attempt", "delay_ms")
_LOG_LENGTHS = (
    ("message", "message_length"),
    ("delta", "delta_length"),
    ("text", "text_length"),
    ("reason", "reason_length"),
)


def _logs(events: Any) -> dict[str, Any]:
    """The structured event log, projected onto a closed field list.

    Anything a person or a model wrote — assistant text, an error message, a
    tool argument map, a tool result — is carried as a length and never as a
    value. `run_id` here is the turn identity the presentation log records; it
    is a different identifier space from the Agent Run id.
    """
    rows = [row for row in (events or []) if isinstance(row, dict)]
    counts: dict[str, int] = {}
    for row in rows:
        _tally(counts, _text(row.get("type"), _SHORT))
    records = []
    for index, row in enumerate(rows[-MAX_LOG_RECORDS:], start=max(0, len(rows) - MAX_LOG_RECORDS)):
        record: dict[str, Any] = {"index": index}
        for name, limit in _LOG_SCALARS:
            value = _text(row.get(name), limit)
            if value is not None:
                record[name] = value
        for name in _LOG_NUMBERS:
            value = _number(row.get(name))
            if value is not None:
                record[name] = value
        for name, target in _LOG_LENGTHS:
            if isinstance(row.get(name), str):
                record[target] = len(row[name])
        if isinstance(row.get("ok"), bool):
            record["ok"] = row["ok"]
        turn = _text(row.get("run_id"), _ID)
        if turn is not None:
            record["turn_id"] = turn
        call = _text(row.get("id"), _ID)
        if call is not None:
            record["tool_call_id"] = call
        name = _text(row.get("name"), _SHORT)
        if name is not None:
            record["tool_name"] = name
        records.append(record)
    return {
        "total_records": len(rows),
        "record_limit": MAX_LOG_RECORDS,
        "truncated": len(rows) > MAX_LOG_RECORDS,
        "counts": counts,
        "records": records,
    }


# --- the preview ---------------------------------------------------------


def build_preview(document: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A bounded summary the operator reviews before deciding to save."""
    subject = document.get("subject.json") or {}
    state = document.get("state.json") or {}
    logs = document.get("logs.json") or {}
    run = (document.get("run.json") or {}).get("run") or {}
    outcome = (document.get("run.json") or {}).get("outcome") or {}
    health = document.get("health.json") or {}
    connectors = (document.get("connectors.json") or {}).get("connectors") or []
    preview = {
        "bundle_version": BUNDLE_VERSION,
        "subject": subject.get("subject"),
        "evidence_categories": subject.get("evidence_categories") or [],
        "excluded": subject.get("excluded") or [],
        "members": list(MEMBERS),
        "counts": {
            "log_records": len(logs.get("records") or []),
            "findings": len(state.get("findings") or []),
            "connectors": len(connectors),
            "runs_considered": health.get("runs_considered") or 0,
        },
        "run": {
            "run_id": run.get("run_id"),
            "state": outcome.get("state"),
            "outcome_status": (outcome.get("result") or {}).get("status"),
            "finished_at": outcome.get("finished_at"),
        }
        if run
        else None,
        "state": {"healthy": state.get("healthy"), "blocked": state.get("blocked")},
        "findings": [
            {
                "check": item.get("check"),
                "store_id": item.get("store_id"),
                "severity": item.get("severity"),
                "summary": item.get("summary"),
            }
            for item in (state.get("findings") or [])[:PREVIEW_FINDINGS]
        ],
        "log_records": list((logs.get("records") or [])[:PREVIEW_LOG_RECORDS]),
    }
    while len(json.dumps(preview)) > MAX_PREVIEW_CHARS:
        if preview["log_records"]:
            preview["log_records"].pop()
        elif preview["findings"]:
            preview["findings"].pop()
        else:
            break
    return preview


# --- packaging -----------------------------------------------------------


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _serialize(payload: Any) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def archive_members(
    document: dict[str, dict[str, Any]], *, bundle_id: str, generated_at: str
) -> dict[str, bytes]:
    """Every file that will go into the archive, as exact bytes.

    `checksums.txt` covers the evidence files and `manifest.json` covers
    everything except itself, so the only bytes that move between two exports
    of one state are the package identity inside `manifest.json`.
    """
    members = {name: _serialize(payload) for name, payload in document.items()}
    checksums = "".join(
        f"{sha256_bytes(members[name])}  {name}\n" for name in sorted(members)
    )
    members[CHECKSUMS_NAME] = checksums.encode("utf-8")
    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "package": {"bundle_id": bundle_id, "generated_at": generated_at},
        "entries": [
            {
                "name": name,
                "sha256": sha256_bytes(members[name]),
                "size_bytes": len(members[name]),
            }
            for name in sorted(members)
        ],
    }
    members[MANIFEST_NAME] = _serialize(manifest)
    return members


def _pack(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(filename=name, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = MEMBER_MODE << 16
            archive.writestr(info, members[name])
    return buffer.getvalue()


def archive_bytes(
    document: dict[str, dict[str, Any]], *, bundle_id: str, generated_at: str
) -> bytes:
    return _pack(archive_members(document, bundle_id=bundle_id, generated_at=generated_at))


# --- export --------------------------------------------------------------


def scan_bundle(
    document: dict[str, dict[str, Any]], sources: BundleSources
) -> tuple[ScanMatch, ...]:
    """The refusal check on its own, so a preview refuses on the same terms."""
    return scan(
        document,
        registered=sources.registered_secrets,
        home=sources.home,
        state_root=sources.state_root,
    )


def export_bundle(
    subject: BundleSubject,
    sources: BundleSources,
    *,
    destination_dir: str | Path,
    bundle_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble, scan, and only then put one complete file in place.

    Two scans run before anything touches the disk: one over the projected
    document, and one over the exact bytes of every member, which catches
    anything the manifest or the serialisation introduces. Either match raises
    and nothing is written.
    """
    destination = Path(destination_dir)
    document = build_document(subject, sources)
    _refuse_on_match(scan_bundle(document, sources))

    identity = bundle_id or uuid.uuid4().hex
    generated = (now or datetime.now(UTC)).isoformat()
    members = archive_members(document, bundle_id=identity, generated_at=generated)
    matches: list[ScanMatch] = []
    for name in sorted(members):
        matches.extend(
            scan_text(
                members[name].decode("utf-8", errors="replace"),
                registered=sources.registered_secrets,
                home=sources.home,
                state_root=sources.state_root,
                location=name,
            )
        )
    _refuse_on_match(tuple(matches))

    payload = _pack(members)
    name = f"{ARCHIVE_PREFIX}{identity}.zip"
    target = destination / name
    # Created only now. A refusal leaves no directory behind either, so an
    # empty folder never suggests an export that half happened.
    destination.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(dir=destination, prefix=".sourcecado-bundle-"))
    try:
        os.chmod(workspace, _DIRECTORY_MODE)
        staged = workspace / name
        descriptor = os.open(
            staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, MEMBER_MODE
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, target)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    os.chmod(target, MEMBER_MODE)
    return {
        "bundle_id": identity,
        "generated_at": generated,
        "path": str(target),
        "sha256": sha256_bytes(payload),
        "size_bytes": len(payload),
        "members": sorted(members),
    }


def _refuse_on_match(matches: tuple[ScanMatch, ...]) -> None:
    if matches:
        raise BundleScanFailed(matches)


# --- offline inspection --------------------------------------------------


def inspect_archive(path: str | Path) -> dict[str, Any]:
    """Verify a bundle's manifest and every checksum, with no state at all."""
    archive_path = Path(path)
    problems: list[str] = []
    with zipfile.ZipFile(archive_path) as archive:
        names = sorted(archive.namelist())
        if MANIFEST_NAME not in names:
            return {
                "path": str(archive_path),
                "bundle_version": None,
                "package": None,
                "entries": [],
                "ok": False,
                "problems": [f"{MANIFEST_NAME} is missing"],
            }
        manifest = json.loads(archive.read(MANIFEST_NAME))
        payloads = {name: archive.read(name) for name in names}

    entries = manifest.get("entries") or []
    listed = sorted(entry.get("name") for entry in entries)
    expected = sorted(name for name in payloads if name != MANIFEST_NAME)
    if listed != expected:
        problems.append(
            f"the manifest lists {listed} but the archive holds {expected}"
        )

    verified = []
    for entry in entries:
        name = entry.get("name")
        payload = payloads.get(name)
        if payload is None:
            problems.append(f"{name} is listed in the manifest but not in the archive")
            verified.append({**entry, "ok": False})
            continue
        digest = sha256_bytes(payload)
        ok = digest == entry.get("sha256") and len(payload) == entry.get("size_bytes")
        if not ok:
            problems.append(f"{name} does not match its manifest checksum or size")
        verified.append(
            {
                "name": name,
                "sha256": entry.get("sha256"),
                "size_bytes": entry.get("size_bytes"),
                "ok": ok,
            }
        )

    problems.extend(_checksum_problems(payloads))
    return {
        "path": str(archive_path),
        "bundle_version": manifest.get("bundle_version"),
        "package": manifest.get("package"),
        "entries": verified,
        "ok": not problems,
        "problems": problems,
    }


def _checksum_problems(payloads: dict[str, bytes]) -> list[str]:
    listing = payloads.get(CHECKSUMS_NAME)
    if listing is None:
        return [f"{CHECKSUMS_NAME} is missing"]
    problems: list[str] = []
    for line in listing.decode("utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        payload = payloads.get(name)
        if payload is None:
            problems.append(f"{name} is listed in {CHECKSUMS_NAME} but not present")
        elif sha256_bytes(payload) != digest:
            problems.append(f"{name} does not match its line in {CHECKSUMS_NAME}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coworker.diagnostic_bundle",
        description="Inspect a Sourcecado diagnostic bundle offline.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    inspector = subcommands.add_parser("inspect", help="verify manifest and checksums")
    inspector.add_argument("archive")
    inspector.add_argument("--json", action="store_true", help="print the raw report")
    arguments = parser.parse_args(argv)

    report = inspect_archive(arguments.archive)
    if arguments.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1
    package = report.get("package") or {}
    print(f"archive        {report['path']}")
    print(f"bundle version {report.get('bundle_version')}")
    print(f"bundle id      {package.get('bundle_id')}")
    print(f"generated at   {package.get('generated_at')}")
    print(f"entries        {len(report['entries'])}")
    for entry in report["entries"]:
        mark = "ok  " if entry["ok"] else "FAIL"
        print(f"  {mark} {entry['sha256']}  {entry['name']}  ({entry['size_bytes']} bytes)")
    for problem in report["problems"]:
        print(f"problem        {problem}")
    print("result         " + ("verified" if report["ok"] else "FAILED"))
    return 0 if report["ok"] else 1


# --- small shared coercions ----------------------------------------------


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value[:limit].strip()
    return trimmed or None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _strings(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    kept = []
    for item in values[:_LIST]:
        text = _text(item, _SUMMARY)
        if text is not None:
            kept.append(text)
    return kept


def _pick(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Named scalars only. A nested structure never escapes a source."""
    picked: dict[str, Any] = {}
    for name in fields:
        value = payload.get(name)
        if isinstance(value, bool):
            picked[name] = value
        elif isinstance(value, (int, float)):
            picked[name] = value
        elif isinstance(value, str):
            picked[name] = _text(value, _SUMMARY)
        else:
            picked[name] = None
    return picked


def _entries(
    values: Any,
    fields: tuple[str, ...],
    *,
    lengths: tuple[tuple[str, str], ...] = (),
) -> list[dict[str, Any]]:
    if not isinstance(values, (list, tuple)):
        return []
    entries = []
    for item in values[:MAX_ENTRIES]:
        if not isinstance(item, dict):
            continue
        entry = _pick(item, fields)
        for name, target in lengths:
            entry[target] = _length(item.get(name))
        entries.append(entry)
    return entries


def _length(value: Any) -> int | None:
    """How much text there was, never the text."""
    return len(value) if isinstance(value, str) else None


def _tally(counts: dict[str, int], key: str | None) -> None:
    if key is None:
        return
    counts[key] = counts.get(key, 0) + 1


if __name__ == "__main__":
    sys.exit(main())
