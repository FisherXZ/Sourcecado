# PR #35 full user-trail QA — 2026-08-26

## Scope

Environment:

- Branch: `feat/club-desktop` / PR #35
- Sidecar: `http://127.0.0.1:8765`
- UI: `http://127.0.0.1:5180`
- Browser: Fisher's existing Chrome tab
- Desktop viewport: 1550px wide
- Narrow viewport: 390 × 844
- Persona restored after testing: Sourcecado Sourcing Agent

Chrome trails completed:

- existing conversation restore, including a 141-second partial Drive run;
- new session, rename, pin, reload, and missing-thread recovery;
- per-thread draft isolation;
- command search and destination navigation;
- Settings and reversible persona switching;
- Connections catalog and Google Drive detail;
- Board empty state;
- Scheduled list, creation form, keyboard validation, and historical receipts;
- Skills catalog;
- Evidence set, failed tool rows, recovery receipts, and Inspector;
- desktop and narrow-screen navigation, Escape handling, and focus return;
- root-URL last-destination restore;
- repeated console inspection (no warnings or errors observed).

Not yet exercised because it requires explicit sensitive-data transmission confirmation:

- live multi-turn model conversation;
- running/queued/stopped chat states against the configured provider;
- new live Drive evidence passed to the provider;
- Gmail draft, Calendar write, Apollo enrichment, and approval resolution.

## Critical mitigations already made locally before report-only direction

These changes are uncommitted and should still become reviewed tickets/commits:

1. Added `drive_list_folder` with pagination and truthful folder-as-file validation. This removes the original false “repair Google Drive” diagnosis for folder traversal.
2. Added Drive credential-value redaction before content reaches model messages, persisted events, or Inspector. The real SOP produced two redactions and no credential-shaped value remained.
3. Added a prompt guardrail: legal templates are evidence only until every party, date, term, and approval status is verified.
4. Deleted the temporary raw extraction file after producing the redacted semester index.

The original Apollo credential still needs to be rotated and removed from Google Drive. The stale NDA still needs replacement at the source.

## Suggested tickets

### T1 — P0 — Rotate and remove the plaintext Apollo credential

Evidence: `Codeology Sourcing SOP` contains a credential value in plaintext. App-side redaction now prevents the value from entering chat, but the original Drive source and revision history remain exposed.

Acceptance criteria:

- the exposed credential is revoked/rotated;
- no current Drive document or accessible revision contains the live value;
- setup docs point to an approved secret store/environment variable;
- a source-ingestion test proves credential-shaped values are redacted before model/event/UI boundaries;
- logs and existing local event files are scanned for the revoked value without printing it.

### T2 — P0 — Quarantine and replace the stale NDA template

Evidence: `Codeology NDA Template.docx` names De Beers and Berkeley Consulting in the agreement body.

Acceptance criteria:

- the stale template is clearly archived/quarantined;
- the canonical template names Berkeley Codeology and uses explicit party placeholders;
- an authorized officer or counsel approves the replacement;
- Sourcecado never labels a legal source “ready to sign” without validating parties, dates, terms, and approval status.

### T3 — P1 — Make Drive reads MIME-aware and truthful

Evidence: the original Chrome trail treated folder download 403s as auth failures; DOCX content was returned as binary/unreadable. Current `DriveApi.read` still lacks extraction for PDF, DOCX, PPTX, and Google Forms.

Acceptance criteria:

- folders route to `drive_list_folder` and never to media download;
- Google Docs/Sheets/Slides export to useful text;
- PDF, DOCX, and PPTX either produce extracted text or an explicit supported-format error;
- Google Forms return metadata-only with a pointer to the response Sheet when available;
- unsupported MIME types never become auth-repair failures or binary-looking model content;
- per-file read status is persisted: `read`, `metadata_only`, `unsupported`, `failed`, or `truncated`.

### T4 — P1 — Add a resumable folder-ingestion/index job

Evidence: “read every file under Fall 2026” consumed 17 tool calls over 141 seconds, hit the chat step budget, and ended partial/interrupted. The folder contains 35 files across seven folders.

Acceptance criteria:

- a folder index runs outside the eight-step conversational loop;
- traversal is recursive, paginated, resumable, and idempotent;
- progress shows folders/files discovered, read, skipped, failed, and remaining;
- cancellation checkpoints after the current file and can resume later;
- every source records stable Drive id, parent/path, MIME type, modified time, sensitivity, and extraction status;
- the resulting index is queryable by later chats without rereading every file.

### T5 — P1 — Enforce source scope during Drive research

Evidence: the original assistant could not enumerate the folder and compensated with global searches. It mixed unrelated `Top10States_LinkedIn_EPC_Leads` sheets into the Fall 2026 sourcing index.

Acceptance criteria:

- once the user selects `Sourcing/Fall 2026`, all subsequent discovery is constrained to that folder tree;
- duplicate folder names are resolved with parent path and source id before traversal;
- global search results outside the selected tree are marked out-of-scope and excluded by default;
- the final receipt reports exact coverage and never claims completeness from global search alone.

### T6 — P1 — Map legacy scheduled-run status `ok` to `success`

Evidence: database runs 1 and 2 have status `ok` and complete answer bodies. `normalizeScheduleRun` treats unknown statuses as `failed`, so Chrome renders both as `Failed` with complete-looking content.

Root cause: frontend status normalization accepts `success` but not the legacy `ok` value.

Acceptance criteria:

- legacy `ok` runs render as Completed, or a migration rewrites them to `success`;
- genuinely unknown statuses render as “Needs review,” not “Failed” without evidence;
- migration/normalization is covered with restored production-shaped fixtures;
- receipt status agrees with the scheduled transcript terminal event.

### T7 — P2 — Render scheduled summaries as bounded rich receipts

Evidence: historical scheduled summaries render raw Markdown as one very long paragraph, including headings, lists, blockquotes, and drafts. Duration shows `0ms` for legacy runs.

Acceptance criteria:

- Markdown renders semantically or is summarized into a bounded preview;
- long receipts have an accessible expand/collapse control;
- legacy zero-duration runs show “Legacy run” or omit duration instead of `0ms`;
- contact details and private memory are not repeated in the schedule overview when the scheduled thread already contains them.

### T8 — P1 — Stop stale shell cache from overwriting the latest destination

Evidence: navigating to `#/skills`, waiting, then booting at `/` restored `#/scheduled` twice. The backend setting also ended as `#/scheduled`.

Root cause: on root boot, cached `last_destination` is applied before the fresh session listing. That cached route then triggers `setLastDestination`, overwriting the newer durable backend value.

Acceptance criteria:

- root boot never persists a cached route before the fresh listing resolves;
- cached navigation may paint a stale shell but cannot overwrite durable state;
- a browser-level test proves `Skills → / → Skills` and `Settings → / → Settings`;
- a stale cache vs. fresh backend conflict test proves the backend wins.

### T9 — P2 — Update the active chat header immediately after rename

Evidence: renaming the current empty thread updated the rail, but the main heading remained “New sourcing conversation” until reload.

Acceptance criteria:

- renaming the active thread updates rail, document route state, and main heading in the same render cycle;
- reload preserves the same title;
- cancelling rename changes nothing and returns focus to the rename trigger.

### T10 — P2 — Keep connection capability copy in sync with tools

Evidence: Google Drive detail advertises only “Search files” and “Read files” after `drive_list_folder` was added.

Acceptance criteria:

- Drive advertises Search files, List folders, and Read files;
- connector capability labels are derived from one server-owned contract or covered by a contract test;
- UI copy updates automatically when a readonly tool is added or removed.

### T11 — P1 — Make the closed mobile rail inert to assistive technology

Evidence: at 390px, the rail is visually closed, but the DOM/accessibility snapshot still exposes the full `Sourcecado` navigation and the off-screen “Close navigation” control alongside “Open navigation.”

Acceptance criteria:

- closed rail is removed from the accessibility tree (`inert`, conditional render, or equivalent);
- only “Open navigation” is keyboard/screen-reader reachable while closed;
- open rail traps focus, Escape closes it, and focus returns to Open navigation;
- overlay click closes without exposing background controls to focus.

### T12 — P1 — Prevent capability overclaims in assistant follow-ups

Evidence: the restored answer offered to “persist this index as a Drive doc,” but Sourcecado has no Drive-write tool.

Acceptance criteria:

- offered next actions are validated against the active tool/capability registry;
- unsupported actions are phrased as manual handoffs, not actions the agent can perform;
- no assistant response claims it can create/update Drive content without a matching allowed tool result;
- a conversation eval covers Drive write, email send, calendar delete, and other unavailable actions.

### T13 — P2 — Contextualize historical connector failures after recovery

Evidence: the restored partial run still says “Google Drive needs to be repaired” even though Connections reports Drive Ready. The recovery receipt correctly says the run continued without it, but the alert reads like current status.

Acceptance criteria:

- historical failures are labeled with run time and historical state;
- current connector status is shown separately;
- completed “continue without source” recovery does not keep a current-tense repair callout;
- audit history remains immutable.

### T14 — P1 — Materialize sourced opportunities into the Board/index layer

Evidence: the Fall 2026 folder contains qualified opportunities and the local structured index, while Board still shows “No one in motion.” The app has no durable company/opportunity/touchpoint/index projection from folder research.

Acceptance criteria:

- folder ingestion can propose structured Company, Contact, Opportunity, Touchpoint, Action, KnowledgeGap, Artifact, and SourceRef records;
- a human review step controls materialization;
- Board stage requires evidence and never advances from deck presence alone;
- source refs and sensitivity survive every projection;
- general Board views exclude restricted resume details.

### T15 — P2 — Add routine lifecycle controls

Evidence: Scheduled supports create and run-now but exposes no pause, edit, or delete controls. A mistakenly created routine cannot be cleaned up through the UI.

Acceptance criteria:

- routines can be paused/resumed and edited;
- delete requires explicit confirmation and clearly states whether receipts/transcript remain;
- next-run state updates immediately and survives restart;
- accidental duplicate routine creation is detectable.

## Confirmed good behavior

- No browser console warnings or errors during the tested trails.
- New session creation and route assignment work.
- Rename persists after reload; pinning separates Pinned and Recent groups.
- Per-thread drafts remain isolated across conversation switches.
- Missing-thread recovery links to the most recent valid conversation.
- Command search filters destinations and conversations.
- Persona switching persists and was restored to Sourcing Agent.
- Connections and Settings expose safe status without credential values.
- Empty automation validation works with real keyboard input and emits accessible alerts.
- Mobile rail Escape handling returns focus to Open navigation.
- Inspector keeps raw tool detail behind an explicit action.
- Partial-run evidence remains visible after a source failure.

## Harness note

One false positive occurred: the Chrome automation `fill("")` changed visible form values without triggering React state, so Save used the original defaults and created a routine. Reproduction with real Cmd+A/Backspace correctly blocked submission. The one QA-created routine was removed directly by exact id; the original weekly routine and its receipts were not changed.
