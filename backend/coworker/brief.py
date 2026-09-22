"""The living brief: one bounded projection of a person file.

A successor should be able to read this and understand the relationship
without opening raw tool JSON or reconstructing it from Gmail. That is the
whole job, and it is why the person view and the person-bound chat prompt are
two *renderings* of one object rather than two summaries.

The shape is deliberate:

``person_brief`` reads the store once and returns a ``LivingBrief``.
``brief_payload`` renders it for the person view. ``prompt_context`` renders
it for the model. Neither renderer can see the person record or the timeline,
so neither can add a fact the other does not have. A second summary
implementation would have to reach past both of them in plain sight.

Two vocabularies, on two different questions, both already defined elsewhere:

* ``context_projection.ContextState`` answers "what is the state of this
  claim" - current, stale, conflicting, missing - and carries the projection's
  own ``truncated`` and ``sensitivity`` facets with it. The brief adopts it
  because the brief *is* a context projection; sharing the contract is what
  keeps the two surfaces from drifting.
* ``run_evidence.Evidence`` answers "what does this source support" -
  ``present``, ``partial`` for a truncated read, ``unsupported`` for a body
  this build cannot extract, ``missing`` for a refresh that failed. It is a
  property of a source reference, not of a claim.

Nothing is coined here that either of them already says.

Isolation is a boundary, not a filter. Every claim carries the ``person_id``
of the row it came from, and ``prepare_context_projection`` refuses a scope
mismatch. A foreign row therefore cannot be rendered - it raises before any
text exists - instead of being quietly dropped by a filter a later refactor
forgets. Restricted records never become a claim body at all: they are
counted, and the count is the claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from coworker.context_projection import (
    ContextAuthority,
    ContextCategory,
    ContextSourceRef,
    ContextState,
    PreparedContextProjection,
    ProjectionIdentity,
    ProjectionItem,
    prepare_context_projection,
)
from coworker.people import SOURCES
from coworker.run_evidence import Evidence

BRIEF_VERSION = "living-brief-v1"
CHAT_HANDOFF_FIELD_CHARS = 2_000

# How long a source reference stands before the claim it backs reads stale.
FRESH_FOR = timedelta(days=30)

# Newest timeline events that may become claims. The timeline itself is
# unbounded and stays on the person view; the brief is the bounded read.
EVIDENCE_CAP = 12

# 150 tokens of text, under the projection's 160-token per-item ceiling, so a
# long note is marked truncated rather than silently omitted for budget.
_MAX_CLAIM_CHARS = 600

# Record fields a connector can contradict. A disagreement on one of these is
# the conflict a director has to resolve before writing anything.
_IDENTITY_FIELDS = ("email", "title", "company")

# Gap slugs that keep the pre-existing `missing` labels truthful.
_GAP_LABELS = {
    "email": "email",
    "mail": "mail",
    "meeting-notes": "meeting notes",
}

_EXTRACTION_EVIDENCE = {
    "read": Evidence.PRESENT,
    "metadata_only": Evidence.PRESENT,
    "truncated": Evidence.PARTIAL,
    "unsupported": Evidence.UNSUPPORTED,
    "failed": Evidence.UNSUPPORTED,
}


@dataclass(frozen=True)
class BriefSource:
    """One source reference, and how far it can be trusted."""

    id: str
    provider: str
    locator: str | None
    title: str | None
    observed_at: str
    modified_at: str | None
    fresh: bool
    evidence: Evidence
    truncated: bool


@dataclass(frozen=True)
class LivingBrief:
    person_id: str
    projection: PreparedContextProjection
    sources: tuple[BriefSource, ...]
    restricted_source_count: int
    partial_sources: tuple[str, ...]
    dropped: int
    sequence_state: str | None
    last_contact: dict[str, Any]
    stored_handoff: dict[str, str] | None
    stored_handoff_version: int | None
    stored_handoff_saved_at: str | None
    stored_handoff_stale_fields: tuple[str, ...]
    version: int


# --- small helpers -------------------------------------------------------


def _now(value: Any = None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = _parse(value)
    return parsed or datetime.now(UTC)


def _parse(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _stamp(value: Any, *, fallback: datetime) -> str:
    """Normalize to an aware UTC ISO string.

    The person store writes two formats - sqlite's ``CURRENT_TIMESTAMP`` and
    an aware ISO string - and the projection sorts on these, so a naive value
    read as local time would reorder claims by hours.
    """
    return (_parse(value) or fallback).astimezone(UTC).isoformat()


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _bounded(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_CLAIM_CHARS:
        return text, False
    return text[:_MAX_CLAIM_CHARS].rstrip(), True


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _who(person: dict[str, Any]) -> str:
    name = " ".join(
        part for part in (person.get("first_name"), person.get("last_name")) if part
    )
    bits = [name] if name else []
    if person.get("title"):
        bits.append(str(person["title"]))
    who = ", ".join(bits)
    if person.get("company"):
        who = f"{who} at {person['company']}" if who else str(person["company"])
    return who


def _ref(
    *,
    id: str,
    provider: str,
    locator: str | None,
    observed_at: Any,
    fallback: datetime,
    modified_at: Any = None,
) -> ContextSourceRef:
    observed = _stamp(observed_at, fallback=fallback)
    fresh_until = (_parse(observed) or fallback) + FRESH_FOR
    return ContextSourceRef(
        id=id,
        provider=provider,
        locator=locator or "",
        observed_at=observed,
        modified_at=_stamp(modified_at, fallback=fallback) if modified_at else None,
        fresh_until=fresh_until.isoformat(),
    )


def _state(
    refs: tuple[ContextSourceRef, ...],
    now: datetime,
    *,
    conflicting: bool = False,
    missing: bool = False,
) -> ContextState:
    if missing:
        return ContextState.MISSING
    if conflicting:
        return ContextState.CONFLICTING
    if refs and all((_parse(ref.fresh_until) or now) <= now for ref in refs):
        return ContextState.STALE
    return ContextState.CURRENT


def _item(
    *,
    id: str,
    person_id: str,
    category: ContextCategory,
    authority: ContextAuthority,
    text: str,
    updated_at: str,
    refs: tuple[ContextSourceRef, ...] = (),
    conflicting: bool = False,
    missing: bool = False,
    now: datetime,
    truncated: bool = False,
) -> ProjectionItem:
    bounded, cut = _bounded(text)
    return ProjectionItem(
        id=id,
        category=category,
        text=bounded,
        tokens=_tokens(bounded),
        state=_state(refs, now, conflicting=conflicting, missing=missing),
        authority=authority,
        updated_at=updated_at,
        source_refs=refs,
        truncated=truncated or cut,
        person_id=person_id,
    )


# --- the claims ----------------------------------------------------------


def _mail_state(person: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Last contact and whether it is waiting on us.

    Uses the person record's own values when the store already computed them,
    so this stays one answer rather than a second opinion.
    """
    if "last_contact_direction" in person:
        return {
            "at": person.get("last_contact_at"),
            "direction": person.get("last_contact_direction"),
            "replied": bool(person.get("replied")),
            "follow_up": person.get("follow_up") or {"needed": False, "reason": None},
            "event": None,
        }
    direction: str | None = None
    at: str | None = None
    replied = False
    latest: dict[str, Any] | None = None
    for event in events:
        payload = _payload(event)
        created = _clean(event.get("created_at"))
        if event.get("kind") == "send" and payload.get("sent") is True:
            direction, at, latest = "outbound", created, event
        elif payload.get("direction") == "inbound":
            replied = True
            direction = "inbound"
            at = _clean(payload.get("received_at")) or created
            latest = event
    reason = "reply_unanswered" if direction == "inbound" else None
    return {
        "at": at,
        "direction": direction,
        "replied": replied,
        "follow_up": {"needed": reason is not None, "reason": reason},
        "event": latest,
    }


def _event_ref(event: dict[str, Any], *, fallback: datetime) -> ContextSourceRef:
    return _ref(
        id=f"{event.get('source')}:{event.get('event_id')}",
        provider=str(event.get("source") or "sourcecado"),
        locator=str(event.get("event_id") or ""),
        observed_at=event.get("created_at"),
        fallback=fallback,
    )


def _attachment_ref(record: dict[str, Any], *, fallback: datetime) -> ContextSourceRef:
    fields = record.get("fields") or {}
    provider = str(fields.get("provider") or "sourcecado")
    locator = str(
        fields.get("drive_id") or fields.get("message_id") or record["id"]
    )
    return _ref(
        id=f"{record['id']}",
        provider=provider,
        locator=locator,
        observed_at=record.get("created_at"),
        modified_at=fields.get("modified_time"),
        fallback=fallback,
    )


def _claims(
    person: dict[str, Any], events: list[dict[str, Any]], *, now: datetime
) -> tuple[list[ProjectionItem], int]:
    pid = str(person["person_id"])
    record_stamp = _stamp(person.get("updated_at"), fallback=now)
    record_ref = _ref(
        id=f"sourcecado:{pid}",
        provider="sourcecado",
        locator=pid,
        observed_at=person.get("updated_at"),
        fallback=now,
    )
    items: list[ProjectionItem] = []

    def add(**kwargs: Any) -> None:
        kwargs.setdefault("person_id", pid)
        items.append(_item(now=now, **kwargs))

    def gap(slug: str, text: str, *, refs: tuple[ContextSourceRef, ...] = ()) -> None:
        add(
            id=f"gap:{slug}:{pid}",
            category=ContextCategory.PERSON_EVIDENCE,
            authority=ContextAuthority.DERIVED,
            text=text,
            updated_at=record_stamp,
            refs=refs,
            missing=True,
        )

    # Identity.
    who = _who(person)
    email = _clean(person.get("email"))
    add(
        id=f"identity:{pid}",
        category=ContextCategory.PERSON_EVIDENCE,
        authority=ContextAuthority.SOURCECADO_RECORD,
        text=f"{who} · {email}" if email else (who or "Unnamed person"),
        updated_at=record_stamp,
        refs=(record_ref,),
    )

    # Why Sourcecado is working this person - the director's own words.
    target = _clean(person.get("target"))
    if target:
        add(
            id=f"target:{pid}",
            category=ContextCategory.SEQUENCE_STATE,
            authority=ContextAuthority.DIRECTOR,
            text=target,
            updated_at=record_stamp,
            refs=(record_ref,),
        )
    else:
        gap("target", "Why Sourcecado is working this person is not recorded.")

    # Current state and outreach outcome.
    sequence = _clean(person.get("sequence_state"))
    add(
        id=f"sequence:{pid}",
        category=ContextCategory.SEQUENCE_STATE,
        authority=ContextAuthority.SOURCECADO_RECORD,
        text=f"Sequence: {sequence.replace('_', ' ')}" if sequence else "Sequence: not started",
        updated_at=record_stamp,
        refs=(record_ref,),
    )
    outcome = _clean(person.get("outcome"))
    if outcome:
        add(
            id=f"outcome:{pid}",
            category=ContextCategory.SEQUENCE_STATE,
            authority=ContextAuthority.SOURCECADO_RECORD,
            text=f"Outcome: {outcome}",
            updated_at=record_stamp,
            refs=(record_ref,),
        )
    else:
        add(
            id=f"outcome:{pid}",
            category=ContextCategory.SEQUENCE_STATE,
            authority=ContextAuthority.DERIVED,
            text="No outreach outcome is recorded yet.",
            updated_at=record_stamp,
            missing=True,
        )

    # Last contact.
    mail = _mail_state(person, events)
    if mail["direction"]:
        contact_event = mail.get("event")
        add(
            id=f"contact:{pid}",
            category=ContextCategory.SEQUENCE_STATE,
            authority=ContextAuthority.SOURCECADO_RECORD,
            text=f"Last contact: {mail['direction']} on {mail['at'] or 'an unrecorded date'}",
            updated_at=_stamp(mail["at"], fallback=now),
            refs=(
                (_event_ref(contact_event, fallback=now),)
                if contact_event is not None
                else (record_ref,)
            ),
        )
    else:
        gap("contact", "No outreach has been sent and no reply has arrived.")

    # Learned evidence, newest first, bounded.
    recent = events[-EVIDENCE_CAP:]
    dropped = max(0, len(events) - len(recent))
    sources_present = {str(event.get("source")) for event in events}
    for event in recent:
        summary = _clean(event.get("summary"))
        if not summary:
            continue
        actor = str(event.get("actor") or "assistant")
        source = str(event.get("source") or "sourcecado")
        authority = (
            ContextAuthority.DIRECTOR
            if actor == "director"
            else ContextAuthority.SOURCECADO_RECORD
            if source == "sourcecado"
            else ContextAuthority.CONNECTOR
        )
        add(
            id=f"evidence:{event.get('event_id')}",
            person_id=str(event.get("person_id") or pid),
            category=ContextCategory.PERSON_EVIDENCE,
            authority=authority,
            text=summary,
            updated_at=_stamp(event.get("created_at"), fallback=now),
            refs=(_event_ref(event, fallback=now),),
        )

    # Meeting notes are untrusted third-party text; they are quoted as such.
    notes_missing = False
    for event in recent:
        payload = _payload(event)
        if event.get("kind") != "meeting":
            continue
        if payload.get("notes_present") is False:
            notes_missing = True
        excerpt = _clean(payload.get("notes_excerpt"))
        if not excerpt:
            continue
        refs = [_event_ref(event, fallback=now)]
        meeting_ref = payload.get("source_ref")
        if isinstance(meeting_ref, dict) and meeting_ref.get("id"):
            refs.append(
                _ref(
                    id=str(meeting_ref["id"]),
                    provider=str(meeting_ref.get("provider") or event.get("source")),
                    locator=str(meeting_ref.get("id")),
                    observed_at=event.get("created_at"),
                    fallback=now,
                )
            )
        add(
            id=f"notes:{event.get('event_id')}",
            person_id=str(event.get("person_id") or pid),
            category=ContextCategory.PERSON_EVIDENCE,
            authority=ContextAuthority.CONNECTOR,
            text=f"Meeting notes (untrusted): {excerpt}",
            updated_at=_stamp(event.get("created_at"), fallback=now),
            refs=tuple(refs),
        )

    # What this person wants, in their own words where we have them.
    wants = _wants(recent, now=now)
    if wants is not None:
        text, refs, stamp, holder = wants
        add(
            id=f"wants:{pid}",
            person_id=holder or pid,
            category=ContextCategory.PERSON_EVIDENCE,
            authority=ContextAuthority.CONNECTOR,
            text=text,
            updated_at=stamp,
            refs=refs,
        )
    else:
        gap("wants", "What this person wants is not recorded.")

    # Disagreements between the record and a connector.
    for event in recent:
        payload = _payload(event)
        for field in _IDENTITY_FIELDS:
            recorded = _clean(person.get(field))
            reported = _clean(payload.get(field))
            if not recorded or not reported:
                continue
            if recorded.casefold() == reported.casefold():
                continue
            add(
                id=f"conflict:{field}:{event.get('event_id')}",
                person_id=str(event.get("person_id") or pid),
                category=ContextCategory.PERSON_EVIDENCE,
                authority=ContextAuthority.DERIVED,
                text=(
                    f"{field} disagrees: the person file says {recorded}; "
                    f"{event.get('source')} says {reported}"
                ),
                updated_at=_stamp(event.get("created_at"), fallback=now),
                refs=(record_ref, _event_ref(event, fallback=now)),
                conflicting=True,
            )

    # Attachments the store already holds. Restricted records never reach this
    # loop with a body: `PersonStore.get` withholds them and reports a count.
    for record in person.get("attachments") or []:
        fields = record.get("fields") or {}
        holder = str(record.get("person_id") or pid)
        ref = _attachment_ref(record, fallback=now)
        stamp = _stamp(record.get("updated_at"), fallback=now)
        if record.get("type") == "artifact":
            title = _clean(fields.get("title")) or "Untitled artifact"
            url = _clean(fields.get("url"))
            add(
                id=f"artifact:{record['id']}",
                person_id=holder,
                category=ContextCategory.PERSON_EVIDENCE,
                authority=ContextAuthority.DIRECTOR,
                text=f"Artifact: {title}" + (f" ({url})" if url else ""),
                updated_at=stamp,
                refs=(ref,),
            )
        elif record.get("type") == "knowledge_gap":
            question = (
                _clean(fields.get("question"))
                or _clean(fields.get("reason"))
                or _clean(fields.get("kind"))
                or "An open question is recorded without its text."
            )
            add(
                id=f"gap:{record['id']}",
                person_id=holder,
                category=ContextCategory.PERSON_EVIDENCE,
                authority=ContextAuthority.DERIVED,
                text=question,
                updated_at=stamp,
                refs=(ref,),
                missing=True,
            )
        elif record.get("type") == "source_ref":
            title = _clean(fields.get("title")) or "Untitled source"
            provider = _clean(fields.get("provider")) or "sourcecado"
            note = ""
            if fields.get("out_of_scope"):
                note = " — outside the browsed folder"
            elif str(fields.get("extraction_status") or "") in {
                "unsupported",
                "failed",
            }:
                note = " — body could not be extracted"
            add(
                id=f"source:{record['id']}",
                person_id=holder,
                category=ContextCategory.PERSON_EVIDENCE,
                authority=ContextAuthority.CONNECTOR,
                text=f"{provider}: {title}{note}",
                updated_at=stamp,
                refs=(ref,),
                truncated=bool(fields.get("truncated")),
            )

    # Gaps the brief can see for itself.
    if not email:
        gap("email", "No email address is recorded for this person.")
    if "gmail" not in sources_present:
        gap("mail", "No Gmail evidence is filed for this person.")
    if notes_missing:
        gap("meeting-notes", "A meeting is attached with no notes.")
    withheld = int(person.get("restricted_source_count") or 0)
    if withheld:
        gap(
            "restricted",
            f"{withheld} restricted source reference(s) are withheld from this brief.",
        )
    return items, dropped


def _wants(
    events: list[dict[str, Any]], *, now: datetime
) -> tuple[str, tuple[ContextSourceRef, ...], str, str | None] | None:
    """The newest thing the person said about what they want, with its source."""
    for event in reversed(events):
        payload = _payload(event)
        said = None
        if payload.get("direction") == "inbound":
            said = _clean(payload.get("snippet")) or _clean(event.get("summary"))
        elif event.get("kind") == "meeting":
            said = _clean(payload.get("notes_excerpt"))
        if not said:
            continue
        return (
            f"What they want: {said}",
            (_event_ref(event, fallback=now),),
            _stamp(event.get("created_at"), fallback=now),
            str(event.get("person_id")) if event.get("person_id") else None,
        )
    return None


# --- assembling and rendering --------------------------------------------


def _identity(person: dict[str, Any], session_id: str | None) -> ProjectionIdentity:
    return ProjectionIdentity(
        persona_id="sourcing",
        session_id=session_id or "",
        person_id=str(person["person_id"]),
        target=_clean(person.get("target")),
        prompt_version=BRIEF_VERSION,
        effective_tools_hash="",
    )


def _source_table(
    projection: PreparedContextProjection,
    person: dict[str, Any],
    *,
    partial_sources: tuple[str, ...],
    now: datetime,
) -> tuple[BriefSource, ...]:
    """Every source a selected claim points at, plus the ones that failed."""
    extraction: dict[str, str] = {}
    titles: dict[str, str] = {}
    truncated: set[str] = set()
    for record in person.get("attachments") or []:
        if record.get("type") != "source_ref":
            continue
        fields = record.get("fields") or {}
        extraction[record["id"]] = str(fields.get("extraction_status") or "read")
        title = _clean(fields.get("title"))
        if title:
            titles[record["id"]] = title
        if fields.get("truncated"):
            truncated.add(record["id"])

    rows: dict[str, BriefSource] = {}
    for item in projection.items:
        for ref in item.source_refs:
            if ref.id in rows:
                continue
            status = extraction.get(ref.id)
            rows[ref.id] = BriefSource(
                id=ref.id,
                provider=ref.provider,
                locator=ref.locator or None,
                title=titles.get(ref.id),
                observed_at=ref.observed_at,
                modified_at=ref.modified_at,
                fresh=(_parse(ref.fresh_until) or now) > now,
                evidence=(
                    _EXTRACTION_EVIDENCE.get(status, Evidence.PRESENT)
                    if status is not None
                    else (Evidence.PARTIAL if item.truncated else Evidence.PRESENT)
                ),
                truncated=item.truncated or ref.id in truncated,
            )
    for provider in partial_sources:
        row_id = f"refresh:{provider}"
        rows[row_id] = BriefSource(
            id=row_id,
            provider=provider,
            locator=None,
            title=None,
            observed_at=now.isoformat(),
            modified_at=None,
            fresh=False,
            evidence=Evidence.MISSING,
            truncated=False,
        )
    return tuple(rows.values())


def _failed_sources(refresh: dict[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(refresh, dict):
        return ()
    sources = refresh.get("sources")
    if not isinstance(sources, dict):
        return ()
    return tuple(
        sorted(
            name
            for name, state in sources.items()
            if isinstance(state, dict) and state.get("status") == "failed"
        )
    )


def project(
    person: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    session_id: str | None = None,
    refresh: dict[str, Any] | None = None,
    now: Any = None,
) -> LivingBrief:
    """Build the one bounded projection both surfaces read.

    Raises ``ValueError`` if an event or attachment belongs to somebody else:
    the projection boundary refuses it before any text is rendered.
    """
    moment = _now(now)
    items, dropped = _claims(person, events, now=moment)
    projection = prepare_context_projection(
        identity=_identity(person, session_id), items=tuple(items)
    )
    partial_sources = _failed_sources(refresh)
    omitted = sum(
        category.omitted_count for category in projection.diagnostics.categories
    )
    handoff = {
        "who": _clean(person.get("handoff_who")) or "",
        "wanted": _clean(person.get("handoff_wanted")) or "",
        "happened": _clean(person.get("handoff_happened")) or "",
        "they_want": _clean(person.get("handoff_they_want")) or "",
    }
    handoff_version, handoff_saved_at, stale_handoff_fields = _handoff_metadata(
        events, handoff
    )
    return LivingBrief(
        person_id=str(person["person_id"]),
        projection=projection,
        sources=_source_table(
            projection, person, partial_sources=partial_sources, now=moment
        ),
        restricted_source_count=int(person.get("restricted_source_count") or 0),
        partial_sources=partial_sources,
        dropped=dropped + omitted,
        sequence_state=_clean(person.get("sequence_state")),
        last_contact=_mail_state(person, events),
        stored_handoff=handoff if any(handoff.values()) else None,
        stored_handoff_version=handoff_version,
        stored_handoff_saved_at=handoff_saved_at,
        stored_handoff_stale_fields=stale_handoff_fields,
        version=int(person.get("version") or 1),
    )


_HANDOFF_FIELD_LABELS = {
    "handoff_who": "who",
    "handoff_wanted": "wanted",
    "handoff_happened": "happened",
    "handoff_they_want": "they_want",
}


def _handoff_metadata(
    events: list[dict[str, Any]],
    handoff: dict[str, str],
) -> tuple[int | None, str | None, tuple[str, ...]]:
    """Track each handoff field's save and later invalidations independently."""
    saves: list[tuple[int, int | None, str | None, set[str]]] = []
    reverts: list[tuple[int, int | None]] = []
    for index, event in enumerate(events):
        if event.get("source") == "sourcecado" and event.get("kind") == "revert":
            payload = _payload(event)
            try:
                to_version = int(payload.get("to_version"))
            except (TypeError, ValueError):
                to_version = None
            reverts.append((index, to_version))
            continue
        if event.get("source") != "sourcecado" or event.get("kind") != "patch":
            continue
        payload = _payload(event)
        fields = payload.get("fields")
        if not isinstance(fields, dict):
            continue
        handoff_fields = _HANDOFF_FIELD_LABELS.keys() & fields.keys()
        if not handoff_fields:
            continue
        try:
            saved_version = int(payload.get("version"))
        except (TypeError, ValueError):
            saved_version = None
        saves.append(
            (
                index,
                saved_version,
                _clean(event.get("created_at")),
                set(handoff_fields),
            )
        )
    if not saves:
        return None, None, ()

    revert_index: int | None = None
    effective_saves = list(saves)
    if reverts:
        revert_index, to_version = reverts[-1]
        before_revert = [
            save
            for save in saves
            if save[0] < revert_index
            and to_version is not None
            and save[1] is not None
            and int(save[1]) <= to_version
        ]
        after_revert = [save for save in saves if save[0] > revert_index]
        effective_saves = [*before_revert, *after_revert]
    if not effective_saves:
        return None, None, ()

    field_saves: dict[str, tuple[int, int | None, str | None, set[str]]] = {}
    for save in effective_saves:
        for field in save[3]:
            field_saves[field] = save
    required_fields = {
        field
        for field, label in _HANDOFF_FIELD_LABELS.items()
        if handoff.get(label)
    }
    unknown_fields = required_fields - field_saves.keys()
    latest_save = max(field_saves.values(), key=lambda save: save[0])
    saved_version = None if unknown_fields else latest_save[1]
    saved_at = None if unknown_fields else latest_save[2]

    stale: set[str] = set()
    for storage_field, label in _HANDOFF_FIELD_LABELS.items():
        saved = field_saves.get(storage_field)
        if saved is None:
            continue
        for index, event in enumerate(events[saved[0] + 1 :], start=saved[0] + 1):
            payload = _payload(event)
            fields = payload.get("fields")
            is_handoff_patch = (
                event.get("source") == "sourcecado"
                and event.get("kind") == "patch"
                and isinstance(fields, dict)
                and bool(_HANDOFF_FIELD_LABELS.keys() & fields.keys())
            )
            if is_handoff_patch or (
                event.get("source") == "sourcecado" and event.get("kind") == "revert"
            ):
                continue
            if (
                revert_index is not None
                and index < revert_index
                and event.get("source") == "sourcecado"
            ):
                continue
            invalidates = label == "happened"
            if label == "they_want":
                invalidates = bool(
                    payload.get("direction") == "inbound"
                    or event.get("kind") == "meeting"
                    or event.get("source") == "granola"
                )
            elif label == "who":
                invalidates = bool(
                    isinstance(fields, dict)
                    and {"first_name", "last_name", "title", "company"}
                    & fields.keys()
                ) or bool(
                    event.get("source") == "apollo"
                    and event.get("kind") == "enrich"
                )
            elif label == "wanted":
                invalidates = isinstance(fields, dict) and "target" in fields
            if invalidates:
                stale.add(label)
                break
    ordered = tuple(
        field for field in ("who", "wanted", "happened", "they_want") if field in stale
    )
    return saved_version, saved_at, ordered


def person_brief(
    people: Any,
    person_id: str,
    *,
    session_id: str | None = None,
    refresh: dict[str, Any] | None = None,
    now: Any = None,
) -> LivingBrief:
    """Read the store once and project it. The only entry both surfaces use.

    ``session_id`` is resolved from the store when the caller did not supply
    it, so the person view and the person-bound chat produce the same
    ``ProjectionIdentity`` and therefore the same binding hash.
    """
    person = people.get(person_id, expand_sources=True)
    if person is None:
        raise ValueError("unknown person")
    if session_id is None:
        try:
            session_id = people.session_for_person(person_id)
        except ValueError:
            session_id = None
    return project(
        person,
        people.timeline(person_id),
        session_id=session_id,
        refresh=refresh,
        now=now,
    )


def _section(brief: LivingBrief, prefix: str) -> list[ProjectionItem]:
    return [
        item for item in brief.projection.items if item.id.startswith(f"{prefix}:")
    ]


def _claim(item: ProjectionItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "text": item.text,
        "state": str(item.state),
        "authority": str(item.authority),
        "updated_at": item.updated_at,
        "truncated": item.truncated,
        "source_refs": [ref.id for ref in item.source_refs],
    }


def _first(items: list[ProjectionItem]) -> dict[str, Any] | None:
    return _claim(items[0]) if items else None


def _one(brief: LivingBrief, *prefixes: str) -> dict[str, Any] | None:
    """The first claim under any of these id prefixes, in the order given."""
    for prefix in prefixes:
        found = _first(_section(brief, prefix))
        if found is not None:
            return found
    return None


def _bound_handoff_fields(
    handoff: dict[str, Any], field_chars: int | None
) -> dict[str, Any]:
    bounded = dict(handoff)
    truncated: list[str] = []
    if field_chars is not None:
        limit = max(1, int(field_chars))
        for field in ("who", "wanted", "happened", "they_want"):
            value = str(bounded.get(field) or "")
            if len(value) <= limit:
                continue
            bounded[field] = value[:limit].rstrip() + "…"
            truncated.append(field)
    bounded["truncated_fields"] = truncated
    return bounded


def handoff_draft(
    brief: LivingBrief, *, field_chars: int | None = None
) -> dict[str, Any]:
    """The four-field handoff: what the director stored, or a draft to review.

    A generated draft names the claims it was built from, so a reviewer can
    check each field against live evidence before saving it. A stored handoff
    names its person-file version instead: that version's snapshot holds the
    sources that existed when it was written, which is the relationship a
    revert restores.
    """
    if brief.stored_handoff is not None:
        return _bound_handoff_fields(
            {
                **brief.stored_handoff,
                "generated": False,
                "source_refs": [],
                "version": brief.stored_handoff_version,
                "saved_at": brief.stored_handoff_saved_at,
                "stale": bool(brief.stored_handoff_stale_fields),
                "stale_fields": list(brief.stored_handoff_stale_fields),
                "freshness_unknown": brief.stored_handoff_version is None,
            },
            field_chars,
        )
    identity = _one(brief, "identity")
    target = _one(brief, "target", "gap:target")
    outcome = _one(brief, "outcome")
    wants = _one(brief, "wants", "gap:wants")
    happened_items = _section(brief, "evidence")[:4]
    happened = "; ".join(item.text for item in happened_items) or (
        outcome or {}
    ).get("text", "Nothing has happened yet.")
    used = [
        claim["id"] for claim in (identity, target, outcome, wants) if claim is not None
    ] + [item.id for item in happened_items]
    return _bound_handoff_fields(
        {
            "who": (identity or {}).get("text", ""),
            "wanted": (target or {}).get("text", ""),
            "happened": happened,
            "they_want": (wants or {}).get("text", ""),
            "generated": True,
            "source_refs": used,
            "version": brief.version,
            "saved_at": None,
            "stale": False,
            "stale_fields": [],
            "freshness_unknown": False,
        },
        field_chars,
    )


def brief_payload(
    brief: LivingBrief, *, handoff_field_chars: int | None = None
) -> dict[str, Any]:
    """Render the projection for the person view."""
    claims = [_claim(item) for item in brief.projection.items]
    learned = _section(brief, "evidence") + _section(brief, "notes")
    gaps = [claim for claim in claims if claim["state"] == ContextState.MISSING]
    conflicts = [
        claim for claim in claims if claim["state"] == ContextState.CONFLICTING
    ]
    missing_labels = [
        _GAP_LABELS[slug]
        for slug in _GAP_LABELS
        if any(claim["id"].startswith(f"gap:{slug}:") for claim in gaps)
    ]
    # The pre-existing `sources` list means "connectors that filed evidence",
    # so it reads the refs behind evidence claims rather than the whole source
    # table, which also holds the person record itself and failed refreshes.
    filed = {ref.provider for item in learned for ref in item.source_refs}
    providers = [source for source in SOURCES if source in filed]
    identity = _first(_section(brief, "identity")) or {"text": "", "source_refs": []}
    return {
        "version": BRIEF_VERSION,
        # The pre-existing shape, kept so the Board, the person header, and the
        # chat labels do not have to change.
        "who": _who_text(identity["text"]),
        "why": (_first(_section(brief, "target")) or {}).get("text", ""),
        "learned": [item.text for item in learned],
        "missing": missing_labels,
        "sources": providers,
        # The living brief.
        "identity": identity,
        "target": _first(_section(brief, "target")),
        "state": {
            "sequence": brief.sequence_state,
            "claim": _first(_section(brief, "sequence")),
        },
        "outcome": _first(_section(brief, "outcome")),
        "last_contact": {
            "at": brief.last_contact.get("at"),
            "direction": brief.last_contact.get("direction"),
            "replied": bool(brief.last_contact.get("replied")),
            "follow_up": brief.last_contact.get("follow_up")
            or {"needed": False, "reason": None},
            "claim": _first(_section(brief, "contact")),
        },
        "wants": _one(brief, "wants", "gap:wants")
        or {"text": "", "state": str(ContextState.MISSING), "source_refs": []},
        "evidence": [_claim(item) for item in learned],
        "conflicts": conflicts,
        "gaps": gaps,
        "artifacts": [_claim(item) for item in _section(brief, "artifact")],
        "claims": claims,
        "source_refs": [
            {
                "id": row.id,
                "provider": row.provider,
                "locator": row.locator,
                "title": row.title,
                "observed_at": row.observed_at,
                "modified_at": row.modified_at,
                "fresh": row.fresh,
                "evidence": str(row.evidence),
                "truncated": row.truncated,
            }
            for row in brief.sources
        ],
        "restricted_source_count": brief.restricted_source_count,
        "partial": bool(brief.partial_sources),
        "partial_sources": list(brief.partial_sources),
        "omitted": brief.dropped,
        "handoff": handoff_draft(brief, field_chars=handoff_field_chars),
        "person_version": brief.version,
    }


def _who_text(identity_text: str) -> str:
    """The header name, without the email the identity claim carries."""
    return identity_text.split(" · ", 1)[0]


_OMISSION = "- {count} further record(s) not shown here."


def prompt_context(brief: LivingBrief, *, budget_chars: int = 2_000) -> str:
    """Render the same projection for the person-bound chat.

    Every line the model sees is a claim from the projection, tagged with the
    source references that back it. When the budget cannot hold every claim
    the count of what was left out is stated rather than dropped in silence.
    """
    payload = brief_payload(brief)
    head = [
        "Person file:",
        f"who: {payload['who']}",
        f"why: {payload['why'] or 'not recorded'}",
        f"state: {payload['state']['sequence'] or 'not started'}",
    ]
    if payload["partial"]:
        head.append(
            "note: this brief is partial; "
            f"{', '.join(payload['partial_sources'])} could not be refreshed"
        )
    if brief.restricted_source_count:
        head.append(
            f"note: {brief.restricted_source_count} restricted source(s) withheld"
        )
    lines = list(head)
    used = sum(len(line) + 1 for line in lines)
    shown = 0
    body: list[str] = ["claims:"]
    used += len(body[0]) + 1
    # Room kept back for the line that admits what did not fit, so the count
    # can never be the thing the budget cuts.
    ceiling = budget_chars - len(_OMISSION.format(count=len(brief.projection.items)))
    for item in brief.projection.items:
        refs = " ".join(f"[{ref.id}]" for ref in item.source_refs)
        line = f"- ({item.state}) {item.text}" + (f" {refs}" if refs else "")
        if used + len(line) + 1 > ceiling:
            break
        body.append(line)
        used += len(line) + 1
        shown += 1
    left_out = len(brief.projection.items) - shown + brief.dropped
    if left_out:
        body.append(_OMISSION.format(count=left_out))
    return "\n".join(lines + body)


def build_brief(
    person: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    session_id: str | None = None,
    refresh: dict[str, Any] | None = None,
    now: Any = None,
) -> dict[str, Any]:
    """The person-view payload for an already-read person record."""
    return brief_payload(
        project(person, events, session_id=session_id, refresh=refresh, now=now)
    )
