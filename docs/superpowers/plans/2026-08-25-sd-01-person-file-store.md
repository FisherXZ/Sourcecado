# SD-01 Person file store

Worktree: `/Users/fisher/Documents/GitHub2026/sc-spring`  
Branch: `feat/sourcing-director-spring`  
Spec: `docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md`  
Tickets: `docs/superpowers/plans/2026-08-25-sourcing-director-spring-tickets.md`

## Goal

A sourcing person can be filed, retrieved, moved on a three-bucket board, given a timeline, bound to a chat session, and handed off. No HTTP. No tools. No GUI.

## How to build

TDD. One failing test at a public seam, then only enough code to pass it. Do not write the whole test file first. Do not query sqlite, `._conn`, or private helpers. A second `PersonStore` on the same `tmp_path` is how you prove persistence.

New files only: `desktop/coworker/people.py`, `desktop/tests/test_people.py`. Do not touch `store.py` or `desktop/surfaces/gui/`.

## Seams

Confirm these before coding. Tests live only here.

| Seam | Public API | Behavior under test |
|---|---|---|
| File | `keep_from_apollo` → `get` / `get_by_apollo_id` | Apollo candidate becomes a retrievable person. No email. Re-keep of the same Apollo id is the same person. |
| Board | `set_sequence` → `list_board` | Unworked people are off the board. Open / In conversation / Done are the only buckets. |
| Ledger | `append_event` → `timeline` | Events stick to one person, oldest first, and reject unknown sources. |
| Bind | `bind_session` → `person_for_session` | A chat session points at one person. Re-bind replaces. |
| Handoff | `set_handoff` → `get` | Four fields round-trip: who, wanted, happened, they_want. |

Persistence is not a sixth seam. After any write, construct a new `PersonStore(tmp_path)` and read through the same API.

## Interface

```python
PersonStore(base_dir)  # people.db under base_dir, never club.db

keep_from_apollo(*, apollo_id, first_name, last_name_obfuscated, title, company, target=None) -> person
get(person_id) -> person | None
get_by_apollo_id(apollo_id) -> person | None
set_sequence(person_id, state, *, actor) -> person   # actor is director | assistant
list_board() -> {"open": [...], "in_conversation": [...], "done": [...]}
append_event(person_id, *, source, kind, summary, payload=None, actor="assistant", session_id=None, run_id=None, tool=None) -> event
timeline(person_id) -> list[event]
bind_session(session_id, person_id) -> None
person_for_session(session_id) -> person_id | None
set_handoff(person_id, *, who, wanted, happened, they_want) -> person
```

Person keys: `person_id`, `apollo_id`, `first_name`, `last_name`, `title`, `company`, `email`, `linkedin_url`, `phone`, `sequence_state`, `target`, `handoff_who`, `handoff_wanted`, `handoff_happened`, `handoff_they_want`, `created_at`, `updated_at`.

`email` / `linkedin_url` / `phone` exist so later tickets do not migrate. `keep_from_apollo` leaves them unset.

Event keys: `event_id`, `person_id`, `source`, `kind`, `summary`, `payload`, `actor`, `session_id`, `run_id`, `tool`, `created_at`. `payload` is a dict.

Sources: `apollo`, `gmail`, `calendar`, `drive`, `web`, `granola`, `sourcecado`.  
Sequence: `open`, `in_conversation`, `done`.  
Actor: `director`, `assistant`.

Company is a string on the person. No company table.

## Vertical slices

Work top to bottom. Each slice is one test name, then the code to make it pass, then the next.

1. **Keeping an Apollo row files a person without an email.**  
   Keep Alyssa / Partner / Codeology. `get` returns that identity. `email` is `None`. `sequence_state` is `None`.

2. **Keeping the same Apollo id updates the file instead of forking.**  
   Second keep with a new title and target. `get_by_apollo_id` returns the original `person_id`. Title and target are the new values. Still no email.

3. **A person with no sequence is not on the board.**  
   `list_board()` is three empty lists.

4. **Moving a person to open puts them on the board.**  
   `set_sequence(..., "open", actor="director")`. Board `open` contains that person. Other buckets empty.

5. **Unknown sequence, actor, or person is rejected.**  
   `won`, `crm`, and a made-up id raise `ValueError`. Board unchanged.

6. **A Gmail event on Ada does not show up on Alonzo.**  
   Append mail then file on Ada, meeting on Alonzo. Ada's timeline is mail then file. Alonzo's is meeting. Payload round-trips as a dict.

7. **HubSpot is not a source.**  
   `append_event(..., source="hubspot")` raises `ValueError`.

8. **A session can be rebound to a different person.**  
   Bind chat-1 to Ada, then to Alonzo. `person_for_session("chat-1")` is Alonzo. Unknown session is `None`. Unknown person raises.

9. **Handoff four fields survive get.**  
   Set who / wanted / happened / they_want. `get` returns the same four strings.

After each slice: `cd desktop && .venv/bin/pytest tests/test_people.py -q`. After the last slice: full `pytest -q`.

## Not in this ticket

HTTP, `people_keep`, enrich, send, brief projection, turn wiring, GUI, `ConversationStore` tables.
