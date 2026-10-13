# Tracked mail shares person storage and separates collection from analysis

Status: Accepted — 2026-10-12

## Context

Gmail activity must survive failed refreshes and restarts without duplicate
timeline events, wrong-person message text or false freshness. A reply sent
directly in Gmail is evidence, but cannot supply Sourcecado send approval.
Automatic collection and later analysis need independent progress boundaries.

## Decision

Keep account/message evidence, collection progress and per-person analysis
revisions in `people.db` under the registered schema 3 → 4 migration. Commit each
message's evidence, timeline projection and revision in one PersonStore
transaction. Advance collection checkpoints only after a completed bounded pass;
failed passes preserve prior progress and safely replay committed messages.

Attribute metadata before reading bounded bodies through a read-only boundary.
Keep approval receipts authoritative while adding collected metadata to their
existing event. Compare source time and thread identity for incoming reminders.

## Alternatives considered

A separate mail database would split ownership, timeline and revision writes
across transaction boundaries. Storing bodies in timeline payloads would expose
more private content to ordinary API/UI consumers. Reusing the inbound cursor as
an outgoing or analysis boundary would hide uncollected or unanalyzed evidence.

## Consequences

Replays preserve one message event and unchanged evidence revisions. Bodies stay
local and untrusted; completeness is explicit. Collection can succeed while
analysis remains pending. Scheduling and analysis execution belong to #208 and
later work. See [the collection contract](../gmail-activity.md).
