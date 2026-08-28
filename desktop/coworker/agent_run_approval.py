"""The fence between an approved external effect and a second attempt at it.

Some effects reach a real person and cost real money. `gmail_send` is the case
this module exists for. Between dispatching one and recording what happened
there is a window, and a process that dies inside it leaves behind a fact
nobody holds: the mail may have gone out, or it may not have. That is not
"failed" and it is not "succeeded". Calling it either is the mistake with no
undo, so the store refuses to.

An effect with a dispatch and no outcome is `ambiguous`: quarantined until a
person settles it. Nothing automatic moves it out. A retry, a resume, a second
owner, and a later restart all read the same row and all stop there.

Two vocabularies, deliberately disjoint.

- `succeeded` and `failed` are what the dispatching process observed. Only that
  process may write them, and only from `dispatched`.
- `resolved_succeeded`, `resolved_failed`, and `abandoned` are what a person
  decided. The row cannot hold one of them without naming who decided it.

No value belongs to both sets, so a machine observation and a human judgement
can never be confused when the record is read back. The database enforces the
separation itself: `EFFECT_SCHEMA` carries triggers that abort an update
crossing from `ambiguous` into a machine outcome, an update that changes a
settled outcome, an insert that opens anywhere but `dispatched`, and any delete
at all. A code path that gets this wrong fails loudly rather than quietly.

Which tools need the fence is not decided here. `permissions.RETRY_SAFE` is the
one list of tools that can re-run without producing a second external effect,
and this module reads it. Everything outside it is consequential, so a tool the
permission module has never heard of is fenced rather than replayed.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Iterable

from coworker.agent_runs import redact_secrets
from coworker.permissions import RETRY_SAFE
from coworker.run_evidence import Evidence


class ReplayClass(StrEnum):
    """Whether attempting this tool again can produce a second external effect."""

    SAFE = "safe"
    CONSEQUENTIAL = "consequential"


class EffectStatus(StrEnum):
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # The dispatch was recorded and no outcome ever was. Quarantined.
    AMBIGUOUS = "ambiguous"
    RESOLVED_SUCCEEDED = "resolved_succeeded"
    RESOLVED_FAILED = "resolved_failed"
    ABANDONED = "abandoned"


# What the dispatching process saw. Reachable only from `dispatched`.
MACHINE_OUTCOMES = frozenset({EffectStatus.SUCCEEDED, EffectStatus.FAILED})
# What a person decided. Reachable only from `ambiguous`, and never without a name.
OPERATOR_OUTCOMES = frozenset(
    {
        EffectStatus.RESOLVED_SUCCEEDED,
        EffectStatus.RESOLVED_FAILED,
        EffectStatus.ABANDONED,
    }
)
SETTLED_EFFECT_STATUSES = MACHINE_OUTCOMES | OPERATOR_OUTCOMES
OPEN_EFFECT_STATUSES = frozenset({EffectStatus.DISPATCHED, EffectStatus.AMBIGUOUS})
EFFECT_STATUSES = SETTLED_EFFECT_STATUSES | OPEN_EFFECT_STATUSES

# The whole point of the module in one line: an outcome a machine may write and
# an outcome a person may write are never the same value.
assert MACHINE_OUTCOMES.isdisjoint(OPERATOR_OUTCOMES)
assert set(EffectStatus) == EFFECT_STATUSES

_MAX_NOTE = 512
_MAX_ID = 256


class AgentRunEffectFenced(RuntimeError):
    """A write would have moved an external effect along an edge that is closed."""


def replay_class(tool_name: str) -> ReplayClass:
    """Read `permissions.RETRY_SAFE`; treat everything else as consequential.

    Fail closed on purpose. A tool the permission module does not list is not a
    tool this module gets to guess about.
    """
    return (
        ReplayClass.SAFE if str(tool_name) in RETRY_SAFE else ReplayClass.CONSEQUENTIAL
    )


def is_replay_safe(tool_name: str) -> bool:
    return replay_class(tool_name) is ReplayClass.SAFE


def effect_fingerprint(tool_name: str, arguments: Any) -> str:
    """Identify one external effect without keeping what it said.

    A recipient, a subject, and a body are the operator's content and stay in
    the transcript and the approval record. What the run store needs is only
    whether a call it is about to make is the call it already dispatched, and a
    digest answers that.
    """
    canonical = json.dumps(
        arguments if arguments is not None else {},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(f"{tool_name}\n{canonical}".encode("utf-8")).hexdigest()


def is_quarantined(effect: dict[str, Any]) -> bool:
    return str(effect.get("status")) == EffectStatus.AMBIGUOUS


def unreported(effects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dispatched, never reported. These are what a restart must quarantine."""
    return [
        effect
        for effect in effects
        if str(effect.get("status")) == EffectStatus.DISPATCHED
    ]


def quarantined(effects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [effect for effect in effects if is_quarantined(effect)]


def external_effect_evidence(effects: Iterable[dict[str, Any]]) -> Evidence | None:
    """The evidence value an unsettled effect forces, or None if none does.

    Only the direction this module can decide alone. Whether silence about
    external effects means `absent`, `missing`, or `expired` depends on the
    whole record, and `run_evidence.absence_evidence` already owns that call.
    """
    items = list(effects)
    if any(is_quarantined(effect) for effect in items):
        return Evidence.AMBIGUOUS
    if unreported(items):
        return Evidence.MISSING
    return None


def operator_decision(decision: str) -> EffectStatus:
    """Map a person's verdict onto the operator half of the vocabulary."""
    try:
        status = EffectStatus(str(decision))
    except ValueError as exc:
        raise ValueError(f"unknown operator decision {decision!r}") from exc
    if status not in OPERATOR_OUTCOMES:
        raise ValueError(
            f"{decision!r} is a machine outcome; a person settles a quarantined "
            "effect with one of "
            f"{sorted(str(value) for value in OPERATOR_OUTCOMES)}"
        )
    return status


def bounded_note(value: Any) -> str | None:
    if value is None:
        return None
    text = redact_secrets(str(value))[:_MAX_NOTE].strip()
    return text or None


def bounded_actor(value: Any) -> str:
    text = redact_secrets(str(value or "")).strip()[:_MAX_ID]
    if not text or "\n" in text:
        raise ValueError("an operator decision must name who made it")
    return text


def _quoted(values: Iterable[str]) -> str:
    return ",".join(f"'{value}'" for value in sorted(str(item) for item in values))


# The fence, expressed where no Python path can route around it. Every rule
# below is also enforced in `AgentRunRepository`; it is repeated here because a
# raw SQL edit, a future method, or a bug in one of those paths must still hit
# a wall rather than turn "we do not know" into "it worked".
EFFECT_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS agent_run_effects (
    effect_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_call_id TEXT,
    approval_id TEXT,
    replay_class TEXT NOT NULL,
    arguments_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    dispatched_by TEXT NOT NULL,
    dispatched_at TEXT NOT NULL,
    settled_at TEXT,
    outcome_ref TEXT,
    reason TEXT,
    resolved_by TEXT,
    resolved_note TEXT,
    resolved_at TEXT,
    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id),
    CHECK (status IN ({_quoted(EFFECT_STATUSES)})),
    CHECK (replay_class IN ({_quoted(ReplayClass)})),
    -- A person's verdict cannot exist in this table without the person.
    CHECK ((status IN ({_quoted(OPERATOR_OUTCOMES)})) = (resolved_by IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS agent_run_effects_by_run
    ON agent_run_effects(run_id, status);
CREATE INDEX IF NOT EXISTS agent_run_effects_open
    ON agent_run_effects(status, dispatched_at);

CREATE TRIGGER IF NOT EXISTS agent_run_effects_open_as_dispatched
BEFORE INSERT ON agent_run_effects
WHEN NEW.status <> '{EffectStatus.DISPATCHED}'
BEGIN
    SELECT RAISE(ABORT, 'an external effect record opens as dispatched');
END;

CREATE TRIGGER IF NOT EXISTS agent_run_effects_quarantine_is_operator_only
BEFORE UPDATE OF status ON agent_run_effects
WHEN OLD.status = '{EffectStatus.AMBIGUOUS}'
 AND NEW.status NOT IN ({_quoted({EffectStatus.AMBIGUOUS, *OPERATOR_OUTCOMES})})
BEGIN
    SELECT RAISE(ABORT,
        'a quarantined external effect is settled by a person, never by code');
END;

CREATE TRIGGER IF NOT EXISTS agent_run_effects_settled_is_final
BEFORE UPDATE OF status ON agent_run_effects
WHEN OLD.status IN ({_quoted(SETTLED_EFFECT_STATUSES)})
 AND NEW.status <> OLD.status
BEGIN
    SELECT RAISE(ABORT, 'a settled external effect never changes outcome');
END;

CREATE TRIGGER IF NOT EXISTS agent_run_effects_are_never_deleted
BEFORE DELETE ON agent_run_effects
BEGIN
    SELECT RAISE(ABORT,
        'an external effect record is evidence that something left the machine');
END;
"""
