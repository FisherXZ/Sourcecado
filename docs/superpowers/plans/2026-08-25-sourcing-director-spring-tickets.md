# Sourcecado Sourcing Director Spring — Ticket Proposal

**Status:** Draft for implementation planning on 2026-08-25  
**Spec:** `docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md`  
**Worktree:** `/Users/fisher/Documents/GitHub2026/sc-spring` on `feat/sourcing-director-spring`  
**Scope:** Club backend domain under `desktop/coworker/` and `desktop/tests/`. GUI tickets are listed and blocked. Do not edit `desktop/surfaces/gui/` in this pass.

The June 8 OS design is prior art, not this product. It is an earlier version with the wrong scope. Borrow identity, citations, and a durable record of work. Do not rebuild the weekly packet, ranked shortlist, or drafts-only Gmail product.

Local Club is the proving ground. The destination is real sourcing officers as soon as the job works. Shared login is still not this spring. Do not shape the domain as Fisher's personal EA.

## Settled approach

Chat is home. The person file is the product object. The board is how the assistant reports.

A **person file** is the comprehensive context layer for one person we may work. It is not an email thread. It is the ledger of everything Sourcecado learned from the connectors a sourcing director actually uses:

- Apollo
- Gmail
- Calendar
- Google Drive
- web research
- meeting notes (Granola)

A later officer opens that file, reads the log plus a short handoff, then chats against it.

A **sequence** is a person we are working. Company is a tag on that person. Do not call it a deal. States: Open → In conversation → Done.

**Send** is approve-to-send in Sourcecado. That replaces drafts-only for this spring. Send the reviewed Gmail draft. Do not create-and-send behind the director's back.

**Enrich** is manual and director-driven. Search never returns emails. Getting a real address spends Apollo credits on purpose, one person at a time.

## Why this split

The spec's use cases are the source of truth. Locked conditions are constraints, not the backlog.

`ConversationStore` / `store.py` / `turn.py` / `server.py` are merge-conflict hotspots with the parallel UI/UX pass on `feat/club-desktop`. As of 2026-08-25 that pass has **done** DU-01–06, DU-15, and DU-17, still uncommitted on `feat/club-desktop`. Remaining DU-07–14, DU-16, DU-18–19 are presentation, Scheduled, and cleanup. They do not block backend SD-01–11.

SD-01 and SD-02 add new files only. Later tickets may touch `tools.py`, `permissions.py`, `gmail.py`, and `turn.py` in this worktree. They must not edit GUI sources while DU-07–14 / DU-16 are in that tree.

Club already has Apollo search/enrich, Gmail search/read/draft, Drive, Calendar, and Granola MCP. It has no person store, no ledger, no board, no `gmail_send`, and no web research tool.

## Story map

| Story | Officer outcome |
|---|---|
| U1 Intake | Name a target, get Apollo people, keep the ones worth a file. |
| U2 Send | Draft from Apollo fields plus the target, enrich on purpose, send from Sourcecado. |
| U3 Motion | See Open / In conversation / Done without reconstructing Gmail. |
| U4 Brief | Walk into the room with the living brief, not a fresh research project. |
| U5 Pickup | Open the person file, see the full log and handoff, then chat. |

## Dispatch rules

1. Give each agent exactly one SD ticket and this document plus the spec.
2. Implement test-first in `desktop/tests/`. Run `cd desktop && .venv/bin/pytest -q` after every ticket.
3. Work only in `/Users/fisher/Documents/GitHub2026/sc-spring`. Do not edit `/Users/fisher/Documents/GitHub2026/Sourcecado`.
4. Do not edit `desktop/surfaces/gui/` until SD-12 / SD-13. The shell (DU-02) already exists. SD-12 still waits on person/board APIs. SD-13 still waits on DU-12. Do not start GUI in this worktree while the other tree still owns `route.ts` / `GlobalRail.tsx` for DU-16.
5. Do not add tables to `ConversationStore`. Person data lives in `people.db` via `coworker/people.py`.
6. Do not add assistant-ui, shadcn, Tailwind, or React 19.
7. Do not invent HubSpot objects, auto-send, auto-enrich, or a weekly ranking run.
8. Ledger payloads never include OAuth tokens, API keys, or raw Authorization headers.

## Dependency graph

```text
SD-01 person file store
   ├─> SD-02 ledger contract ──> SD-04 turn wiring ──> SD-06 draft opens sequence ──> SD-08 send
   │         │                         │                         │
   │         │                         ├─> SD-07 enrich          └─> SD-09 board API
   │         │                         │
   │         └─> SD-11 web research    └─> SD-05 brief + pickup API ──> SD-10 chat against the file
   │
   └─> SD-03 keep from Apollo ─────────┘

SD-12 board/person GUI  (DU-02 is done; wait on SD-05 / SD-09, and do not fight DU-16 for the rail)
SD-13 draft/send GUI    (after DU-12 and SD-08; send backend is not waiting on DU-12)
```

## Ticket index

| ID | Title | Type | Blocked by | Stories |
|---|---|---|---|---|
| SD-01 | Person file store | AFK | None | U3, U5 |
| SD-02 | Connector ledger contract | AFK | SD-01 | U4, U5 |
| SD-03 | Keep people from Apollo search | AFK | SD-01 | U1 |
| SD-04 | Write the ledger from a bound turn | AFK | SD-02, SD-03 | U4, U5 |
| SD-05 | Living brief and pickup API | AFK | SD-02 | U4, U5 |
| SD-06 | Draft onto a person and open the sequence | AFK | SD-04, SD-05 | U2, U3, U4 |
| SD-07 | Director-driven enrich onto the file | AFK | SD-04 | U2 |
| SD-08 | Approve-to-send the reviewed draft | AFK | SD-06 | U2 |
| SD-09 | Board API | AFK | SD-06 | U3 |
| SD-10 | Chat against the person file | AFK | SD-05 | U4, U5 |
| SD-11 | Web research tool that writes the ledger | AFK | SD-02 | U4, U5 |
| SD-12 | Board and person surfaces | AFK | DU-02, SD-05, SD-09 | U3, U5 |
| SD-13 | Draft and send domain UI | AFK | DU-12, SD-08 | U2 |

## Ticket bodies

### SD-01: Person file store

**Type:** AFK  
**User stories:** U3, U5  
**Plan:** `docs/superpowers/plans/2026-08-25-sd-01-person-file-store.md`

#### What to build

A backend-owned person file in a new sqlite database `people.db`, separate from `club.db`. Create, fetch, and update a person from an Apollo candidate without storing an email. Support sequence state, an append-only ledger, session binding, and a four-field handoff.

This ticket is the file. It does not add HTTP, tools, or GUI.

#### Acceptance criteria

- [ ] `PersonStore` lives in `desktop/coworker/people.py` and writes `people.db` under the Club base dir.
- [ ] `keep_from_apollo` is idempotent on `apollo_id`, stores no email, and leaves `sequence_state` unset.
- [ ] Company is a string tag on the person. There is no company table.
- [ ] `set_sequence` only accepts `open`, `in_conversation`, `done` and writes a ledger event.
- [ ] `list_board` returns those three buckets and omits people with no sequence.
- [ ] `append_event` accepts sources `apollo`, `gmail`, `calendar`, `drive`, `web`, `granola`, `sourcecado` and rejects anything else.
- [ ] `timeline` returns that person's events oldest-first and never another person's.
- [ ] `bind_session` / `person_for_session` are 1:1. Binding a session to a second person replaces the first.
- [ ] Handoff fields are `who`, `wanted`, `happened`, `they_want`.
- [ ] `cd desktop && .venv/bin/pytest -q` stays green.

#### Blocked by

None. Start immediately in this worktree.

### SD-02: Connector ledger contract

**Type:** AFK  
**User stories:** U4, U5  
**Plan:** `docs/superpowers/plans/2026-08-25-sd-02-person-ledger.md`  
TDD seams and slices live in that file.

#### What to build

A pure mapper from Club tool name + arguments + result into a ledger event. The living brief is this log, not an email summary. Cover Apollo, Gmail, Calendar, Drive, web research, and Granola meeting notes even when the current turn is not yet wired.

Do not call `PersonStore.append_event` from `turn.py` in this ticket.

#### Acceptance criteria

- [ ] `event_from_tool` in `desktop/coworker/ledger.py` returns `None` for tools that are not file-worthy (`now`, `remember`, `memory_*`, `load_skill`, `apollo_search_people`).
- [ ] Successful `gmail_search`, `gmail_read`, `gmail_draft`, `drive_search`, `drive_read`, `calendar_list`, `calendar_create`, `calendar_update`, `apollo_enrich_contact`, and `mcp__granola__*` reads produce a typed event with `source`, `kind`, `summary`, `payload`, and `tool`.
- [ ] Failed tools that are file-worthy produce an event with `kind` `error` and a plain-language summary. Raw transport errors stay in `payload.detail`, not the summary.
- [ ] Web research has a stable shape for `web_search` / `web_fetch` so SD-11 does not redesign the contract.
- [ ] Payloads never include tokens, API keys, or Authorization headers.
- [ ] Mapper unit tests do not open sqlite.

#### Blocked by

- SD-01

### SD-03: Keep people from Apollo search

**Type:** AFK  
**User stories:** U1  
**Plan:** `docs/superpowers/plans/2026-08-25-sd-03-to-11-backend.md` (SD-03 through SD-11)

#### What to build

A `people_keep` tool the assistant can call after `apollo_search_people`. The director named the target. The assistant searched. The director curated. This tool files the kept rows.

Search stays auto and still returns no emails. Keep does not enrich and does not open a sequence.

#### Acceptance criteria

- [ ] `people_keep` is AUTO. It takes `people` (Apollo search rows) and optional `target`.
- [ ] Each row becomes a person file via `keep_from_apollo`. Duplicates of the same `apolloId` do not fork files.
- [ ] The tool result lists `person_id` plus the stored identity fields. No email field.
- [ ] KERNEL / persona copy tells the model to search, then keep, and never to invent the target.
- [ ] Pytest covers keep, idempotent re-keep, empty list, and missing Apollo fields.

#### Blocked by

- SD-01

### SD-04: Write the ledger from a bound turn

**Type:** AFK  
**User stories:** U4, U5

#### What to build

After a successful or failed tool execute in `run_turn`, if the session is bound to a person, append the SD-02 event to that file. Binding happens when the turn keeps a person, drafts to a person, or the session was already bound.

This ticket may edit `turn.py` and `tools.py` in this worktree only. Do not take UI/UX event-spine changes from the other tree.

#### Acceptance criteria

- [ ] A bound session's Gmail/Drive/Calendar/Granola/Apollo-enrich results land on that person and only that person.
- [ ] Unbound sessions do not create people or ledger rows, except `people_keep` which creates files and binds if a single person is kept into an unbound session that was already bound, or leaves binding to an explicit later bind.
- [ ] `people_keep` of one person binds that session to that person. Keep of many people does not guess a bind.
- [ ] Switching sessions cannot append to the wrong person.
- [ ] Pytest covers bound vs unbound, two people, and a failed Drive read that still records an error event.

#### Blocked by

- SD-02
- SD-03

### SD-05: Living brief and pickup API

**Type:** AFK  
**User stories:** U4, U5

#### What to build

Project the living brief from the ledger. Serve the pickup payload: identity, sequence, brief, handoff, timeline.

The brief starts as a product object when the first draft lands (SD-06). This ticket builds the projection and `GET /v1/people/{id}` so pickup is readable before the GUI exists.

The brief is not "the last email." It is a short, sourced picture of who they are, why we are writing, what we have already learned from every connector, and what is still missing.

#### Acceptance criteria

- [ ] `build_brief(person, events) -> dict` returns `who`, `why`, `learned`, `missing`, and `sources` (the connector names that contributed).
- [ ] A person with only Apollo identity has a thin brief and `missing` that can name email, mail, notes, or files.
- [ ] A person with Apollo + Gmail + Drive + Granola events names those sources and does not drop the successful ones when another source is absent.
- [ ] `GET /v1/people/{id}` returns `{ person, brief, timeline }` and 404s for unknown ids.
- [ ] Response never includes connector tokens or secrets.
- [ ] Auth is the existing Club token header.

#### Blocked by

- SD-02

### SD-06: Draft onto a person and open the sequence

**Type:** AFK  
**User stories:** U2, U3, U4

#### What to build

When `gmail_draft` is allowed for a bound person (or an explicit `person_id`), attach the draft to the file, set sequence to `open` if it was unset, and seed the living brief.

Draft still asks. Draft still does not send.

#### Acceptance criteria

- [ ] Allowed draft on a person with no sequence moves them to `open` and onto the board.
- [ ] A second draft does not reset `in_conversation` or `done` back to `open`.
- [ ] Ledger has a `gmail` / `draft` event with recipient, subject, draft id, and `sent: false`.
- [ ] Denied draft writes no person state change and no successful draft event.
- [ ] Pytest uses `FakeGmail`. `gmail.sends` stays empty.

#### Blocked by

- SD-04
- SD-05

### SD-07: Director-driven enrich onto the file

**Type:** AFK  
**User stories:** U2

#### What to build

`apollo_enrich_contact` writes the real email onto the bound person after Allow. It still asks. It still spends credits only when allowed. It does not enrich a list.

#### Acceptance criteria

- [ ] Enrich without a bound person or `person_id` fails closed with a clear error. It does not create a new person.
- [ ] Allowed enrich stores `email` on the person and a ledger `apollo` / `enrich` event.
- [ ] Denied enrich does not store an email.
- [ ] Search results still have no email field.
- [ ] Pytest uses `FakeHttp`. No live Apollo.

#### Blocked by

- SD-04

### SD-08: Approve-to-send the reviewed draft

**Type:** AFK  
**User stories:** U2

#### What to build

Add `gmail_send`. It sends an existing Gmail draft by id after Allow. This replaces the drafts-only guardrail for this spring.

OAuth already has `gmail.send`. `FakeGmail.send` currently raises. Change that in tests. Live client calls Gmail `users.drafts.send`.

#### Acceptance criteria

- [ ] `gmail_send` is ASK. Unknown `gmail_send` without the new permission entry must not silently auto-run.
- [ ] Allow sends the draft, records `sent: true`, and writes a ledger `gmail` / `send` event.
- [ ] Deny does not send.
- [ ] Missing draft id or unbound person fails visibly.
- [ ] KERNEL no longer says drafts never send. It says send requires Allow.
- [ ] Pytest: FakeGmail records the send; live client is fake-HTTP only.

#### Blocked by

- SD-06

### SD-09: Board API

**Type:** AFK  
**User stories:** U3

#### What to build

`GET /v1/board` returns Open / In conversation / Done. `POST /v1/people/{id}/sequence` moves a person. The director or the assistant can move them. This is not Scheduled.

#### Acceptance criteria

- [ ] Board lists only people with a sequence.
- [ ] Move is idempotent and writes a `sourcecado` / `state` ledger event with actor.
- [ ] Invalid state is 400. Unknown person is 404.
- [ ] Waiting / empty board is `{ "open": [], "in_conversation": [], "done": [] }`, not an error.
- [ ] No HubSpot fields (amount, stage, pipeline, close date).

#### Blocked by

- SD-06

### SD-10: Chat against the person file

**Type:** AFK  
**User stories:** U4, U5

#### What to build

When a session is bound to a person, the system prompt includes the living brief and a bounded recent timeline. Pickup is: open the file, then talk.

This is how a later officer uses the ledger. Do not dump raw JSON payloads into the prompt.

#### Acceptance criteria

- [ ] Bound session prompt contains who / why / learned / missing and recent event summaries.
- [ ] Unbound sessions do not mention a person file.
- [ ] Prompt size is capped. Oldest timeline rows drop first.
- [ ] Prompt contains no tokens, emails of unrelated people, or other persons' events.
- [ ] Pytest asserts prompt contents with a FakeProvider turn.

#### Blocked by

- SD-05

### SD-11: Web research tool that writes the ledger

**Type:** AFK  
**User stories:** U4, U5

#### What to build

Club has no web research tool today. Add `web_search` (and `web_fetch` if the hosted SSRF guard can be adapted from `archive/hosted-web/` without coupling the active runtime to the archive) so the person file can cite the public web.

The mapper in SD-02 already names the source `web`. This ticket adds the tool, AUTO for search, and ledger writes through SD-04.

#### Acceptance criteria

- [ ] Search returns titles, URLs, and snippets. No scraping of LinkedIn as a v1 path.
- [ ] Bound-session search appends a `web` / `search` event with result count and URLs.
- [ ] SSRF rules from the hosted `web_fetch` work are copied, not ignored, if fetch ships.
- [ ] Missing Tavily/key is a clear tool error, not a crash.
- [ ] Live smoke is skip-gated. Default pytest uses fakes.

#### Blocked by

- SD-02

### SD-12: Board and person surfaces

**Type:** AFK  
**User stories:** U3, U5

#### What to build

Add `#/board` and `#/people/:id` to the Warm Operator rail after the UI/UX shell (DU-02) has merged. Board is three buckets. Person page is the pickup file: handoff, brief, timeline.

Do not start this ticket in parallel with the UI/UX pass.

#### Acceptance criteria

- [ ] Rail lists Board above Scheduled. Chat remains the default home.
- [ ] Board restore and refresh work. Empty state is "no one in motion."
- [ ] Person route survives refresh. Brief panel can sit beside chat once the shell exists.
- [ ] No new visual system. `DESIGN.md` Warm Operator only.
- [ ] Do not restyle assistant-ui. That belongs to the other pass.

#### Blocked by

- DU-02 (done, still uncommitted on `feat/club-desktop`)
- SD-05
- SD-09
- Coordinate rail edit with DU-16 (Scheduled is still open)

### SD-13: Draft and send domain UI

**Type:** AFK  
**User stories:** U2

#### What to build

Extend the DU-12 Gmail draft card with Allow-to-send. Until DU-12 lands, officers can still send via the approval inbox from SD-08.

#### Acceptance criteria

- [ ] Draft card still shows recipient, subject, body, and Not sent until send succeeds.
- [ ] Send is Allow once. Deny does not send.
- [ ] After send, the card is a sent receipt, not a draft.
- [ ] No approve-to-send in the UI/UX tree before this ticket.

#### Blocked by

- DU-12 merged
- SD-08

## Parallel execution lanes

### Lane A: file and ledger

`SD-01 -> SD-02 -> SD-04 -> {SD-06, SD-07} -> SD-08`

Keep this sequential once turn wiring starts. SD-01 and SD-02 are file-only and safe.

### Lane B: intake and board

`SD-03` after SD-01. `SD-09` after SD-06. `SD-05` after SD-02 can overlap SD-03.

### Lane C: chat pickup and web

`SD-10` after SD-05. `SD-11` after SD-02; merge before expecting web rows in the brief.

### Lane D: GUI

Nothing until DU-02 / DU-12 merge. Then SD-12 and SD-13.

## Recommended launch order

1. SD-01, then SD-02. These only add files.
2. SD-03 and SD-05 in parallel after SD-01 / SD-02.
3. SD-04, then SD-06 and SD-07.
4. SD-08 and SD-09 after SD-06.
5. SD-10 after SD-05 and SD-04.
6. SD-11 whenever after SD-02; earlier is better for the brief.
7. SD-12 / SD-13 only after the UI/UX shell and draft card exist.

## Explicitly not in this spring

- Team tenancy and hosted shared login.
- Auto-send and auto-enrich.
- Sourcecado sending follow-ups on its own.
- HubSpot deals, amounts, forecasts, pipelines.
- Weekly autonomous ranking as the product.
- LinkedIn / Apify v2.
- Calendar delete, Drive writes, Granola writes.
- Chat polish, assistant-ui, queue/cancel. That is the other pass.
- Editing `feat/club-desktop` uncommitted GUI work.

## Merge caution

`feat/sourcing-director-spring` branched from committed Club `fec5700`. The UI/UX pass is uncommitted on `feat/club-desktop`. Prefer adding files. When a ticket must edit `turn.py` or `server.py`, rebase onto UI/UX only after that pass has merged those files, or keep the spring edits small and listed in the PR.

## Approval questions

1. Is `people.db` beside `club.db` the right isolation, versus tables inside `club.db` through a second module?
2. Should `people_keep` of a single person bind the current chat, or is bind always explicit?
3. Is SD-11 (Club web search) in this spring, or do we record web only after a later tool lands?
4. Should SD-08 send via Gmail `users.drafts.send` (reuse the draft) as planned, or `users.messages.send`?
