# Sourcecado Desktop UI/UX Implementation Ticket Proposal

**Status:** Implemented and independently cleared on 2026-08-26
**Parent plan:** `docs/superpowers/plans/2026-08-25-desktop-uiux-improvement.md`
**Scope:** Desktop Python sidecar and `desktop/surfaces/gui/` only
**Prepared for:** One ticket per implementation agent or worktree

## Baseline

- `cd desktop && .venv/bin/pytest -q`: **149 passed, 1 skipped** on 2026-08-25.
- `cd desktop/surfaces/gui && npm run build`: **passed** on 2026-08-25.
- The GUI has no local component-test harness yet. DU-01 adds it before production UI replacement begins.

## Final implementation result

- All DU-01 through DU-19 slices shipped in the working branch.
- Final adversarial review cleared approval races, queue restart recovery, unsafe-retry convergence, and unknown approval outcomes after a crash.
- Final verification: **221 desktop tests passed, 1 skipped; 239 GUI tests passed; production build and diff check passed**.
- Live responsive QA passed at 375x812, 768x1024, 1024x768, 1280x720, 1440x900, and 812x375 short landscape.
- Deferred non-blockers: code-split the 691.52 kB GUI chunk, split large server/style modules when useful, and migrate from the deprecated Starlette TestClient dependency.

## Why the original T1-T11 need another split

The design plan is complete, but several implementation tasks combine work that cannot safely be assigned to one autonomous agent:

- Original T3 combines three different protocol transitions: durable event identity, active-run cancellation, and a persisted queue.
- Original T5 combines the generic activity lifecycle with four unrelated domain result renderers.
- Original T6 combines approval persistence, approval UI, and partial-failure retry policy.
- Original T10 postpones accessibility and responsive behavior even though those requirements must be acceptance criteria on every UI ticket.
- `App.tsx`, `api.ts`, `server.py`, `turn.py`, and `store.py` are current merge-conflict hotspots. Foundation tickets establish seams before parallel agents begin.

This proposal turns the work into narrow, independently verifiable slices. The two HITL tickets are explicit review gates. Every other ticket is AFK and should be implementable without a taste or architecture decision.

## Story map

The identifiers below map directly to the parent plan's user-journey storyboard.

| Story | Operator outcome |
|---|---|
| J1 Return | Restore the last destination and thread with clear active state. |
| J2 Start | Start or open a thread with useful starter actions and source status. |
| J3 Delegate | Send a task and immediately see credible run progress. |
| J4 Continue | Keep drafting, queue corrections, reorder work, or stop the run. |
| J5 Approve | Safely review and resolve a consequential action. |
| J6 Recover | Preserve successful work and recover only the failed source or step. |
| J7 Verify | Review structured outcomes, citations, artifacts, and full provenance. |
| J8 Resume | Leave and return without losing queues, receipts, results, or run state. |

## Dispatch rules

1. Give each agent exactly one DU ticket and its parent-plan link.
2. Implement behavior test-first: add the failing contract or component test before production code.
3. Merge blockers before starting a dependent ticket unless the agent is working in an isolated worktree against the blocker branch.
4. Do not let route agents rework the chat runtime or chat agents rework route data fetching.
5. Keep responsive and accessibility acceptance criteria inside every UI ticket. DU-18 verifies the whole surface; it is not the first time those requirements should be implemented.
6. Pin the assistant-ui version proven by DU-01. Later agents must not independently upgrade it.
7. Every AFK ticket must leave these commands green unless its scope explicitly adds a narrower command first:
   - `cd desktop && .venv/bin/pytest -q`
   - `cd desktop/surfaces/gui && npm test`
   - `cd desktop/surfaces/gui && npm run build`

## Dependency graph

```text
DU-01 assistant-ui proof ───────┐
                                ├─> DU-03 replayable event spine
DU-02 shell + navigation ───────┘              │
       │                                       v
       │                              DU-04 conversation cutover
       │                                  ├───────────────┐
       │                                  v               v
       │                              DU-05 cancel     DU-07 activity
       │                                  │               ├─> DU-10 inspector
       │                                  v               │       ├─> DU-11 Apollo
       │                              DU-06 queue          │       └─> DU-14 evidence
       │                                                  v
       ├─> DU-15 connections ───────────────────────> DU-09 recovery
       ├─> DU-17 skills/settings                  ^
       │                                          │
       └─> DU-16 scheduled <──────── DU-03 + DU-08 approvals
                                             ├─> DU-12 Gmail draft
                                             └─> DU-13 calendar event

DU-06 + DU-09 + DU-11..DU-17 ─> DU-18 responsive/a11y review ─> DU-19 cleanup
```

## Proposed ticket index

| ID | Title | Type | Blocked by | Stories |
|---|---|---|---|---|
| DU-01 | Prove the assistant-ui adapter against Sourcecado fixtures | HITL | None | J3, J4, J5, J8 |
| DU-02 | Build the unified shell, hash routes, and durable thread navigation | AFK | None | J1, J2, J8 |
| DU-03 | Land a versioned, replay-safe event spine for one text turn | AFK | DU-01, DU-02 | J3, J8 |
| DU-04 | Cut the conversation surface over to Warm Operator thread primitives | AFK | DU-03 | J2, J3, J8 |
| DU-05 | Stop an active run with a durable terminal acknowledgement | AFK | DU-04 | J4, J8 |
| DU-06 | Persist and drain a per-thread editable message queue | AFK | DU-05 | J4, J8 |
| DU-07 | Render one humanized, expandable tool-activity group | AFK | DU-04 | J3, J6, J7 |
| DU-08 | Resolve approvals into durable audit receipts | AFK | DU-05, DU-07 | J5, J8 |
| DU-09 | Recover a failed tool step without discarding successful work | AFK | DU-08, DU-15 | J6, J8 |
| DU-10 | Inspect tool, source, citation, and artifact provenance on demand | AFK | DU-07 | J7 |
| DU-11 | Render Apollo people results as a sourcing shortlist | AFK | DU-10 | J7 |
| DU-12 | Render Gmail draft results as review-ready artifacts | AFK | DU-08, DU-10 | J5, J7 |
| DU-13 | Render Calendar results as review-ready event artifacts | AFK | DU-08, DU-10 | J5, J7 |
| DU-14 | Render Drive and Granola results as an evidence set | AFK | DU-10 | J6, J7 |
| DU-15 | Ship a connected-first Connections catalog and detail route | AFK | DU-02 | J2, J6 |
| DU-16 | Ship Scheduled with routine creation and durable run receipts | AFK | DU-03, DU-08 | J1, J5, J8 |
| DU-17 | Ship Skills and Settings as durable product destinations | AFK | DU-02 | J1, J2 |
| DU-18 | Pass responsive, keyboard, screen-reader, and visual review | HITL | DU-06, DU-09, DU-11-DU-17 | J1-J8 |
| DU-19 | Remove the legacy monolith after parity is proven | AFK | DU-18 | J1-J8 |

## Ticket bodies

### DU-01: Prove the assistant-ui adapter against Sourcecado fixtures

**Type:** HITL
**User stories:** J3, J4, J5, J8

#### What to build

Add the GUI-local Vitest and Testing Library harness, pin `@assistant-ui/react`, and build an isolated `ExternalStoreRuntime` proof using recorded Sourcecado messages and events. Prove the adapter contract before replacing the production transcript. The proof must use custom Warm Operator components, not registry-generated shadcn or Tailwind components.

The final checkpoint is a short go/no-go report. A go requires every fixture below to pass. A no-go preserves the component and store boundaries but directs DU-03 onward to the existing reducer fallback described by the parent plan.

#### Acceptance criteria

- [ ] React 18 production build succeeds with a pinned assistant-ui version.
- [ ] The GUI package has deterministic component-test and watch commands.
- [ ] Legacy text/tool JSONL and proposed structured fixtures convert to stable `ThreadMessageLike` parts.
- [ ] Live and restored versions of text, tool, approval, partial, and cancelled states render with identical message and part identities.
- [ ] Switching fixtures between two threads cannot append a delta to the inactive thread.
- [ ] Approval callbacks and tool-result insertion update the intended tool call only.
- [ ] Queue plus cancel behavior is covered explicitly, including keeping queued work after cancel and preventing an automatic unintended dispatch.
- [ ] No assistant-ui Cloud, AI SDK, shadcn/ui, Tailwind, or React 19 dependency is added.
- [ ] The go/no-go result and pinned version are recorded in this ticket before merge.

#### Blocked by

None. Can start immediately.

### DU-02: Build the unified shell, hash routes, and durable thread navigation

**Type:** AFK
**User stories:** J1, J2, J8

#### What to build

Replace the thread-only rail and utility strips with the 232px Warm Operator app rail. Add deterministic hash routing for chat, Scheduled, Connections, Skills, and Settings. Preserve New chat, recent threads, pinned threads, search/Cmd-K, accessible rename and pin actions, active-route state, and last-open restoration end to end.

This ticket also reduces future conflicts: `App.tsx` becomes bootstrap-only, route stubs are established, and route-specific API seams are created so later agents do not all extend the current monolithic `api.ts`.

#### Acceptance criteria

- [ ] Direct navigation and refresh work for every route defined in the parent plan.
- [ ] Last destination and last thread restore without contradictory active states.
- [ ] New chat, open thread, rename, pin/unpin, recent ordering, and unavailable-thread states are persisted and tested.
- [ ] Cmd-K opens a labeled command/search surface that can navigate to destinations and threads.
- [ ] Rename and pin are available from keyboard-accessible controls; double-click is optional only.
- [ ] Every route stub has one `h1`; active navigation uses `aria-current="page"`.
- [ ] Boot skeleton, first-run, boot failure/retry, and cached-stale/reconnect states preserve navigation.
- [ ] At narrow widths the rail opens as a viewport-bounded edge sheet with a persistent trigger.
- [ ] Existing chat behavior remains available through the chat route during this structural change.

#### Blocked by

None. Can start immediately and in parallel with DU-01.

### DU-03: Land a versioned, replay-safe event spine for one text turn

**Type:** AFK
**User stories:** J3, J8

#### What to build

Create the canonical sidecar-to-GUI event envelope and prove it with the smallest complete path: one user message, streamed assistant text, terminal state, reload, and thread switching. Every event carries version, session, run, event, message, and part identity. The sidecar remains authoritative.

New conversation records must project cleanly into model history and UI state. Existing role/content/tool JSONL remains readable through a backward adapter; do not rewrite old conversation files during migration.

#### Acceptance criteria

- [ ] One typed event contract is shared by sidecar tests and the TypeScript client boundary.
- [ ] A streamed text turn uses stable IDs from start through terminal acknowledgement and restored history.
- [ ] Duplicate or replayed events are idempotent and cannot duplicate text.
- [ ] Events for a background or inactive session update only that session.
- [ ] Reconnect and reload reconstruct the same completed, failed, stopped, or interrupted state.
- [ ] Legacy transcripts still load and synthesize stable display parts without mutating their files.
- [ ] Provider requests receive only model-compatible history, never presentation-only fields.
- [ ] Malformed or unknown-version events become a recoverable notice rather than crashing the thread.

#### Blocked by

- DU-01
- DU-02

### DU-04: Cut the conversation surface over to Warm Operator thread primitives

**Type:** AFK
**User stories:** J2, J3, J8

#### What to build

Replace the legacy bubble transcript with custom assistant-ui thread, message, and composer primitives backed by the DU-03 store. Normal assistant prose has no card; user messages retain the compact avocado-tinted treatment. Render GFM content safely and preserve drafts across loading, failures, cancellation, route changes, and reload.

#### Acceptance criteria

- [ ] Live and restored user/assistant messages are visually and semantically identical.
- [ ] Lists, tables, links, code, long drafts, and streaming text render correctly and safely.
- [ ] Copy behavior works without exposing raw event or tool JSON.
- [ ] The transcript is a focusable `role="log"`; streaming does not place `aria-live` on the entire log.
- [ ] A dedicated polite status region announces run start, completion, failure, and cancellation.
- [ ] Empty, loading, failed-load, success, and missing-history-segment states match the parent plan.
- [ ] The composer remains editable while a run is active, even before DU-06 enables queued submission.
- [ ] Narrow layout uses readable 16px input text and has no horizontal overflow.

#### Blocked by

- DU-03

### DU-05: Stop an active run with a durable terminal acknowledgement

**Type:** AFK
**User stories:** J4, J8

#### What to build

Let the WebSocket command reader receive a cancel command while `run_turn` is active. Add a run coordinator and cooperative cancellation path addressed by session and run ID. Show immediate stopping feedback, then persist and emit one terminal stopped acknowledgement. Preserve the composer draft and any future queued work.

Synchronous tools that cannot be interrupted must report that Sourcecado is stopping after the current action; the run must not pretend to be stopped while that action is still executing.

#### Acceptance criteria

- [ ] Stop is available from run start until any terminal state.
- [ ] Cancel commands are scoped to a session/run and are safe to repeat.
- [ ] The sidecar emits stopping and exactly one stopped terminal acknowledgement.
- [ ] No assistant delta or new tool begins after the stopped acknowledgement.
- [ ] A non-interruptible active tool finishes or fails visibly before the stopped acknowledgement.
- [ ] Cancellation while waiting for approval closes the pending card without recording a denial.
- [ ] Reconnect and history reload show a durable cancelled receipt.
- [ ] Cancelling one thread cannot stop or corrupt another thread.

#### Blocked by

- DU-04

### DU-06: Persist and drain a per-thread editable message queue

**Type:** AFK
**User stories:** J4, J8

#### What to build

Keep submission available during a run by creating a sidecar-authoritative queue per thread. Support add, edit, keyboard/pointer move, remove, retry, and deterministic draining. The assistant-ui queue adapter is the interaction layer; persisted ordering, state, acknowledgements, and exactly-once dispatch remain sidecar responsibilities.

#### Acceptance criteria

- [ ] Submitting during a run creates a visible persisted queue item without altering the current turn.
- [ ] Add, edit, move, remove, and retry commands are acknowledged and idempotent.
- [ ] Keyboard controls can perform every reorder action available to pointer users.
- [ ] One terminal run state drains exactly one next item; duplicate terminal events cannot double-send it.
- [ ] Cancel pauses and preserves the queue; the next explicit send/resume follows the tested DU-01 policy.
- [ ] Failed, interrupted, offline, and reconnecting items retain their text and valid recovery actions.
- [ ] Queue state is isolated by thread and survives app/sidecar restart.
- [ ] Action columns do not jump as item status changes.

#### Blocked by

- DU-05

### DU-07: Render one humanized, expandable tool-activity group

**Type:** AFK
**User stories:** J3, J6, J7

#### What to build

Fold all tool work for an assistant turn into one quiet activity block. The collapsed receipt uses a human label, state, elapsed time, and result count. The expanded trace shows chronological high-level milestones, tool/source rows, and failures without revealing raw chain-of-thought. Establish stable registry extension slots for the four domain-renderer tickets.

#### Acceptance criteria

- [ ] No activity block renders for a turn with no tools.
- [ ] Running, completed, failed, denied, partial, interrupted, and restored groups are fixture-tested.
- [ ] The group is collapsed by default after completion and preserves its disclosure state predictably.
- [ ] Default labels use Sourcecado domain language rather than raw SDK/tool identifiers.
- [ ] Expanded rows show useful facts and counts, not private reasoning.
- [ ] Disclosures expose correct names, `aria-expanded`, and keyboard behavior.
- [ ] Generic unknown tools render safely without raw JSON in the default view.
- [ ] Apollo, Gmail, Calendar, and evidence extension slots are stable so DU-11-DU-14 do not redesign the registry.

#### Blocked by

- DU-04

### DU-08: Resolve approvals into durable audit receipts

**Type:** AFK
**User stories:** J5, J8

#### What to build

Persist the complete approval lifecycle and render the progressive-disclosure card from the parent plan. The pending card names the action and affected resource, shows only the fields needed for a safe decision, explains why permission is required, and offers Deny and Allow once. Resolution collapses into an audit receipt with decision, actor, timestamp, scope, and execution outcome.

#### Acceptance criteria

- [ ] Pending, submitting, allowed, denied, expired, cancelled, failed-submit, and resolved-elsewhere states are covered.
- [ ] Allow once and Deny are idempotent across WebSocket and Inbox HTTP resolution paths.
- [ ] A failed decision submission leaves actions available and explains recovery.
- [ ] Resolving elsewhere updates the open thread without a duplicate execution.
- [ ] Full arguments and policy details are behind an accessible disclosure.
- [ ] Focus moves to a newly required approval and returns to the composer after resolution.
- [ ] Restored history renders the same audit receipt and never reopens a resolved approval.
- [ ] Cancellation and expiry do not imply that the operator denied the action.

#### Blocked by

- DU-05
- DU-07

### DU-09: Recover a failed tool step without discarding successful work

**Type:** AFK
**User stories:** J6, J8

#### What to build

Represent failure source, failure class, retry safety, repair destination, and partial downstream state in the structured event log. Attach recovery to the failed row inside the activity group. Support retry failed step, repair connection, and continue without source. Preserve every successful step and mark the activity and answer Partial until recovery changes that state.

#### Acceptance criteria

- [ ] Connector, timeout, permission, validation, and unknown failure classes have plain-language UI.
- [ ] Successful tool rows and result content survive another source's failure.
- [ ] Safe/idempotent retry executes only the failed step and cannot duplicate prior successful work.
- [ ] Unsafe retry requires a fresh approval and never silently repeats a write.
- [ ] Repair opens the exact connector detail route and returning preserves the failed context.
- [ ] Continue without records the operator choice and allows the run to finish as Partial.
- [ ] Raw transport errors remain in Details/Inspector, not the default answer.
- [ ] Retry, reconnect, continue, app restart, and duplicate-command cases are covered end to end.

#### Blocked by

- DU-08
- DU-15

### DU-10: Inspect tool, source, citation, and artifact provenance on demand

**Type:** AFK
**User stories:** J7

#### What to build

Add compact inline citation/source controls and a contextual inspector for the selected tool call, source, citation, or artifact. Persist enough metadata to restore the same selection target after reload. The inspector shows arguments, results, source identity, timing, and artifact preview without crowding the transcript.

#### Acceptance criteria

- [ ] Selecting an inline tool/source/artifact opens matching detail with a stable accessible title.
- [ ] Switching selection cannot show stale detail from the prior object.
- [ ] Loading, empty-selection, success, failed-load, stale-cache, and truncated states are covered.
- [ ] Desktop panel, tablet overlay, and narrow full-screen modes preserve transcript scroll and focus.
- [ ] Escape/Close returns focus to the control that opened the inspector.
- [ ] Full payloads are readable but never rendered as default transcript content.
- [ ] External links use safe targets and clear source labels.

#### Blocked by

- DU-07

### DU-11: Render Apollo people results as a sourcing shortlist

**Type:** AFK
**User stories:** J7

#### What to build

Render Apollo search and enrichment outcomes as a compact candidate shortlist using the DU-07 registry and DU-10 provenance contract. Make result counts, candidate identity, title/company, enrichment state, and credit-sensitive actions understandable without exposing raw payloads.

#### Acceptance criteria

- [ ] Loading skeleton, no matches, populated, failed, partial, and restored states are tested.
- [ ] No-matches offers Adjust criteria and retains the original query context.
- [ ] Enrichment state and any credit-sensitive action are explicit before action.
- [ ] Partial results keep available candidates and identify the missing source/fields.
- [ ] Candidate/source controls open the correct inspector detail.
- [ ] Large result sets remain bounded and keyboard navigable.

#### Blocked by

- DU-10

### DU-12: Render Gmail draft results as review-ready artifacts

**Type:** AFK
**User stories:** J5, J7

#### What to build

Render Gmail draft arguments and results as a domain artifact with recipient, subject, body preview, source context, and explicit Not sent status. Keep creation behind DU-08 approval. This ticket does not add sending or approve-to-send.

#### Acceptance criteria

- [ ] Pending approval, creating, created, failed, partial-context, and restored draft states are tested.
- [ ] Recipient, subject, body, account, and Not sent status are clear before Allow once.
- [ ] Long bodies are safely clamped with accessible expansion.
- [ ] A created draft opens correct provenance/artifact detail without exposing tokens or headers.
- [ ] Denial/cancellation never renders a successful draft.
- [ ] No control can send the email.

#### Blocked by

- DU-08
- DU-10

### DU-13: Render Calendar results as review-ready event artifacts

**Type:** AFK
**User stories:** J5, J7

#### What to build

Render calendar list, create, and update outcomes as calendar event components with clear date/time/timezone, title, account, and action state. Create/update remains behind DU-08 approval; list remains read-only.

#### Acceptance criteria

- [ ] List loading, no events, populated, failed, partial, and restored states are covered.
- [ ] Create/update approval clearly names the calendar, event, date/time, and changed fields.
- [ ] Timezone and invalid/missing date data fail visibly rather than rendering a plausible wrong time.
- [ ] Allowed, denied, cancelled, and failed writes produce accurate receipts.
- [ ] Event/source controls open the correct inspector detail.
- [ ] No delete action is introduced.

#### Blocked by

- DU-08
- DU-10

### DU-14: Render Drive and Granola results as an evidence set

**Type:** AFK
**User stories:** J6, J7

#### What to build

Render connected-source search/read outcomes as an evidence set with recognizable source identity, result counts, excerpts, citation controls, and missing-source annotations. Drive and Granola can contribute together without one failed connector erasing the other's evidence.

#### Acceptance criteria

- [ ] Loading, no evidence, populated, failed, partial, stale, truncated, and restored states are covered.
- [ ] Every evidence row identifies its connector and source object.
- [ ] Excerpts are safely bounded and preserve an Open/Inspect path.
- [ ] One failed source leaves successful evidence visible and marks the set Partial.
- [ ] Citation controls select the exact source in the inspector.
- [ ] Sensitive raw payload fields are excluded from the default view.

#### Blocked by

- DU-10

### DU-15: Ship a connected-first Connections catalog and detail route

**Type:** AFK
**User stories:** J2, J6

#### What to build

Replace permanent connector strips with a dedicated catalog and detail route. Normalize connector status and recovery metadata at the API boundary. Show connected accounts first, then available connectors. Support search, connect/reconnect, OAuth return, scope repair, and honest disconnect behavior.

Google connectors share one account/profile. If disconnect removes the shared Google authorization, the UI must say that Gmail, Drive, and Calendar will all be affected before confirmation.

#### Acceptance criteria

- [ ] Catalog loading, empty search, connected, available, authorizing, scope-missing, degraded, failed, and reconnect-required states are covered.
- [ ] Search has a Clear search recovery and stays sticky on narrow viewports.
- [ ] Detail routes survive refresh and return from OAuth to the same connector.
- [ ] Popup blocked, callback mismatch, missing scope, and provider error states give a concrete next action.
- [ ] Connect/reconnect/disconnect actions refresh only the affected catalog state without losing the route.
- [ ] Shared Google disconnect scope is disclosed before confirmation.
- [ ] Connector status responses never include credentials, tokens, or secret values.
- [ ] At tablet/narrow widths detail replaces the list with a clear Back path.

#### Blocked by

- DU-02

### DU-16: Ship Scheduled with routine creation and durable run receipts

**Type:** AFK
**User stories:** J1, J5, J8

#### What to build

Promote existing schedule data into a dedicated route and close the empty-state gap with the smallest useful routine-creation flow. An operator can create a template-backed routine, see next run, run it now, inspect durable run receipts, and open the associated scheduled thread. Waiting approvals remain explicit and never claim completion.

#### Acceptance criteria

- [ ] Empty state offers Create automation and can produce a persisted routine through a tested API.
- [ ] Job list shows prompt/routine identity, cadence, next run, and current state.
- [ ] Run now handles success, already-running, failed, waiting-approval, and partial outcomes.
- [ ] Run receipts persist status, duration, result summary, artifacts, and scheduled thread identity.
- [ ] Waiting approvals contribute the correct Inbox badge and link to context.
- [ ] Opening a run restores its structured transcript without changing the user's last normal chat.
- [ ] Restart preserves routines, next-run time, receipts, and waiting state.
- [ ] This ticket does not add automatic resume after Inbox approval.

#### Blocked by

- DU-03
- DU-08

### DU-17: Ship Skills and Settings as durable product destinations

**Type:** AFK
**User stories:** J1, J2

#### What to build

Move the existing skills catalog, persona choice, model status, and operator identity out of transcript utility strips into dedicated Skills and Settings routes. Keep the routes informational/configurational; do not add skill editing, marketplace behavior, or primary-composer model selection.

#### Acceptance criteria

- [ ] Skills loading, empty, failed, and populated states use the existing sidecar catalog.
- [ ] Skill rows show human name and description without exposing implementation paths by default.
- [ ] Settings shows operator/persona, model configuration status, and safe current connector summary.
- [ ] Persona changes persist and update the active conversation header without reload.
- [ ] Route refresh and last-destination restoration work.
- [ ] Settings values and errors never expose secrets.
- [ ] Narrow layouts keep all actions at least 44x44px.

#### Blocked by

- DU-02

### DU-18: Pass responsive, keyboard, screen-reader, and visual review

**Type:** HITL
**User stories:** J1-J8

#### What to build

Run the parent plan's complete cross-surface verification after all feature slices land. Add missing automated accessibility and responsive regression coverage, capture the required viewport screenshots, and fix only integration gaps found by the review. The human checkpoint approves the final visual hierarchy against the six HTML decision artifacts.

#### Acceptance criteria

- [ ] Automated checks cover accessible names/roles, expanded state, focus return, status announcements, keyboard reorder, and reduced motion.
- [ ] Screenshots are captured at 375x812, 768x1024, 1024x768, 1280x720, and 1440x900.
- [ ] The full restore -> multi-tool run -> queue -> approval -> partial failure -> reconnect -> rich result -> inspector journey passes.
- [ ] Thread create/rename/pin/search and every durable route pass keyboard-only use.
- [ ] No horizontal overflow, hidden recovery action, raw Markdown, default raw JSON, or sub-44px touch action remains.
- [ ] Light and dark system themes retain WCAG AA contrast and Warm Operator hierarchy.
- [ ] Human review confirms the shipped surface matches D3-D8 rather than assistant-ui stock presentation.

#### Blocked by

- DU-06
- DU-09
- DU-11
- DU-12
- DU-13
- DU-14
- DU-15
- DU-16
- DU-17

### DU-19: Remove the legacy monolith after parity is proven

**Type:** AFK
**User stories:** J1-J8

#### What to build

Delete the legacy rendering and state path only after DU-18 passes. Leave `App.tsx` as bootstrap/composition, remove obsolete formatters and utility-strip styles, and keep exactly one source of truth for route, thread, run, queue, approval, and inspector state.

#### Acceptance criteria

- [ ] No production import reaches the legacy transcript, utility strips, item reducer, or obsolete format helpers.
- [ ] Duplicate event, session, queue, approval, and connector state owners are removed.
- [ ] Styles are split by tokens, shell, chat, route, and responsive responsibility without dead selectors.
- [ ] Legacy transcript fixtures still pass through the backward-read adapter.
- [ ] Desktop tests, GUI component tests, production build, and DU-18 critical journey remain green.
- [ ] Bundle output does not contain the removed path or any prohibited framework dependency.

#### Blocked by

- DU-18

## Parallel execution lanes

### Lane A: shared runtime and chat protocol

`DU-01 -> DU-03 -> DU-05 -> DU-06 -> DU-08 -> DU-09`

Keep this lane sequential. These tickets own the event contract and the shared `server.py`, `turn.py`, `store.py`, chat client, and runtime-store boundaries.

### Lane B: shell and durable destinations

`DU-02 -> {DU-15, DU-17}` and later `DU-16`

DU-15 and DU-17 can run in parallel after DU-02. DU-16 waits for the event and approval contracts. DU-15 also touches connector endpoints in `server.py`, so merge it before DU-09 begins or isolate and coordinate that server edit.

### Lane C: conversation presentation

`DU-04 -> DU-07 -> DU-10 -> {DU-11, DU-14}`

DU-05 can run alongside DU-07 only if each agent stays inside its assigned backend versus presentation boundary. DU-11 and DU-14 are parallel after DU-10.

### Lane D: consequential artifacts

`DU-08 -> {DU-12, DU-13}`

DU-12 and DU-13 should modify only their domain renderer and fixture tests. DU-07 must establish their registry slots first so neither agent redesigns the shared registry.

### Final lane

`DU-18 -> DU-19`

No cleanup begins before the visual/accessibility gate. Deleting the legacy path earlier removes the parity oracle and makes regression diagnosis harder.

## Recommended launch order

1. Launch DU-01 and DU-02 in parallel.
2. Confirm the DU-01 go/no-go result; merge DU-01 and DU-02.
3. Run DU-03, then DU-04.
4. Launch the safe parallel set: DU-05, DU-07, DU-15, and DU-17, with the shared-file ownership cautions above.
5. Merge DU-05 and DU-07; run DU-06, DU-08, and DU-10 in dependency order.
6. Launch DU-11-DU-14 in parallel; run DU-09 after DU-15 and DU-08; run DU-16 after DU-03 and DU-08.
7. Run DU-18 with human visual review, then DU-19.

## Explicitly not in scope

- Gmail sending or approve-to-send.
- New connectors or LinkedIn/Apify v2.
- assistant-ui Cloud, Vercel AI SDK, shadcn/ui, Tailwind, or React 19.
- Voice, attachments, message branching, edit/regenerate, or feedback reactions.
- New multi-agent product features.
- Skill editing or a skill marketplace.
- Calendar deletion.
- Automatic scheduled-run resume after Inbox approval.
- Rewriting legacy conversation files during migration.

## Approval questions

Before these become individual issue-tracker tickets:

1. Is 19 tickets the right granularity, or should any named pair be merged?
2. Are the dependency relationships and parallel lanes correct for how you want to dispatch subagents?
3. Should DU-01 and DU-18 remain HITL while every other ticket is AFK?
4. After approval, should these be published to GitHub Issues, emitted as individual local ticket files, or both?
