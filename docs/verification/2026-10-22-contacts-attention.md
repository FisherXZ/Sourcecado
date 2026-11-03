# Contacts attention verification — October 22, 2026

Implementation target: local Python/FastAPI and React/Vite/Tauri application.
Environment exercised: Windows, Python 3.14, existing repository virtualenv and npm dependencies.
Starting commit: `50fa1a1`; feature branch: `codex/contacts-attention`.

## Executed checks

- Baseline: 79 focused backend tests and 42 frontend tests passed before implementation.
- Final focused backend set: **189 passed**. Covers tasks, attention, analysis, chat tools,
  local drafts, descriptions, Gmail collection, reply filing, migration/rollback,
  approved sending/enrichment and the changed tool-catalog/migration contracts.
- Complete frontend suite: **630 passed across 69 files**.
- Frontend production build: **passed**, including TypeScript. Vite reports an
  unresolved existing marketing-hero reference and a large bundle warning.
- Existing tests were updated where the specification deliberately changes behavior:
  last contact moves to the preview; empty tables retain headers; global Tasks
  includes saved people before sequences; restored description versions increase;
  schema 7 extends the migration chain. Tool diagnostics now reject a `description`
  key rather than the substring in the legitimate tool name `crm_update_description`.

The focused backend command runs these files with `python -m pytest -q`:
`test_crm_tasks`, `test_crm_attention`, `test_crm_analysis`, `test_crm_tools`,
`test_crm_drafts`, `test_person_descriptions`, `test_crm_mail`, `test_crm_mail_api`,
`test_crm_mail_migrations`, `test_approved_send`, `test_approved_enrichment`, and
`test_reply_filing`. It also runs the two diagnostics shape tests and the two
populated-database migration-chain tests in `test_effective_tools`,
`test_effective_tools_api` and `test_migrations`.

## What the checks establish

- Global/person task identity, edits, lifecycle, pagination and retry receipts agree.
- Attention uses saved state and local dates; status never completes another task.
- UI and chat use shared rules, with separate execution actor and human protection.
- Evidence ownership, full bodies and direct unquoted context gate automatic writes.
- Partial, quoted, vague and foreign evidence cannot fabricate committed tasks.
- Human descriptions, including blanks, survive passive analysis. Suggestions can
  be reviewed without silently releasing protection.
- Acceptance and its target mutation roll back together on simulated receipt failure.
- Scoped undo preserves unrelated work and suppresses unchanged rejected inference.
- Analysis resumes from durable revisions, shares overlapping requests, caps failures
  and remains pending after cancellation. New evidence resets its retry budget.
- Draft preparation is durable and retry-safe without an email address, does not
  complete tasks, and does not create Gmail drafts or send mail.
- Schema failure rolls back; old task operation fingerprints remain replayable.

## Browser demonstration

Used fictional people and an isolated external state directory with FakeProvider
and FakeGmail. No real account, normal installation state, enrichment or sends
were used. The backend ran on `127.0.0.1:8766`; Vite used the already permitted
development origin `127.0.0.1:5180`.

Verified Contacts attention and next actions, person preview, unsaved-description
warning and deliberate discard, focus return, and locally prepared follow-up
text with an explicit “nothing sent” notice. Saved tasks, reviewed suggestions
and prepared artifacts remained visible after backend restart. Checked an
800px viewport: preview occupies the main pane; Back retains the prior search.
Restored the default viewport afterward. No browser console errors were present
during the inspected interactions.

Screenshots and full test logs are in the task's external visualization directory,
not the repository or operator's runtime state.

## Full backend suite and limits

The broad backend run was **not green**: 1,803 passed, 261 failed, 3 skipped and
2 errors during integration. Reproduced POSIX permission, directory-fsync,
process-liveness and file-lock failures on unchanged starting code under Windows.
A backend-source export of `50fa1a1` was used for comparison. Four additional
changed-contract failures were identified and all four now pass in the final
focused set. The export omitted root/frontend/documentation fixtures, producing
six baseline-only failures; the comparison is diagnostic, not release certification.
No tests or quality thresholds were disabled to hide these failures.

Native macOS/Tauri behavior and packaging were not exercised from this Windows
environment. Full release validation on the supported native environment remains
necessary. Synthetic email fixtures do not establish production interpretation
precision or recall; consented, redacted sourcing examples remain required.

Reply-thread-preserving draft creation is not supported by the existing connector
path. The implementation deliberately uses the specified Sourcecado-local text
fallback for replies, with a visible limitation. New-message prepared text can
enter the existing outreach review panel; sending retains its explicit approval.

## Pull request review — October 25, 2026

Two independent read-only reviews checked project standards and the supplied
Contacts specification, with current AGENTS.md taking precedence on global Tasks
membership. Regression tests first reproduced, then verified corrections for:

- Dismissed reply reviews incorrectly reappearing in Contacts filters/chips.
- A contact's promise or director's request becoming the director's obligation,
  including mixed request/promise sentences.
- Undo suppressing other obligations from the same email, or recreating the same
  obligation under a reworded title or removed deadline.
- Follow-up preparation choosing a newer unrelated conversation instead of the
  task's owned source thread; ambiguous context stays local with a review warning.
- Provider preparation failures escaping as raw server exceptions.
- Manual Refresh collecting twice and bypassing background request coalescing.
  Concurrent API requests now share collection; a blocked model does not delay
  the legacy collection response.

Final review reports: **Standards: zero remaining actionable blockers; Spec: zero
remaining actionable blockers in the corrections.** Reviews do not constitute
independent human Peer Approval.

Final focused backend set: **201 passed**. Complete frontend suite: **631 passed**.
After the refresh adjustment, the 26 affected reply/Contacts/outreach tests and
production TypeScript/Vite build also passed. The isolated browser demo again
verified Refresh, preview, saved tasks and local draft preparation, with no
observed console errors. It uses only fictional contacts and fake provider/Gmail
boundaries.

The sourcing evaluation command was rerun and still stops with the documented
Windows `PermissionError: eval child failed`. The earlier full-backend Windows
comparison and native/real-email verification limits above still apply. Required
Linux CI and macOS checks must be assessed on the submitted PR; no check was
disabled or weakened.

### CI follow-up and patched dependencies

The first PR run passed the complete GUI job and macOS Python/GUI tests, Rust
checks, native Tauri build and packaged-backend smoke test. Linux reported one
chat-queue test failure after 2,082 passes: it asserted provider call count after
an 80ms sleep while not consuming websocket frames. The same exact call-count,
queue and terminal-event assertions now run after observing both completed
turns, avoiding the timing assumption. The focused check passes locally.

The inherited dependency audit also required security patches. Updated only
MCP 1.29.1 → 1.30.0, certifi 2026.7.22 → 2026.10.19, and pypdf 6.20.1 → 6.21.2.
The generated lock now uses `--universal`, preserving Linux/macOS dependencies
and adding marked Windows dependencies rather than replacing the native set.
Unrelated package versions, including uvloop, remain pinned.

Primary references: [MCP advisory](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-84m7-p3x7-pcfv),
[certifi advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2),
and [pypdf release](https://github.com/py-pdf/pypdf/releases/tag/6.21.2).
MCP's changed redirect policy remains inside the existing client boundary; no
authentication, CORS or approval policy was relaxed.

Hash-verified installation and the full `pip-audit -r backend/requirements.lock`
pass with **no known vulnerabilities found**. The expanded compatibility run
has 254 passes and one unchanged Windows POSIX-mode assertion failure in Drive
ingestion; the earlier focused MCP/meeting set has 15 passes. Linux/macOS CI
must confirm the final dependency state.

The final frontend audit also reported
[GHSA-mrhx-c7xc-m3w5](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5), updated
October 23. Updated only transitive Zod 4.4.3 → 4.6.7 within assistant-ui's
existing `^4.4.3` range, with installation scripts disabled. The frontend audit
then reports **zero vulnerabilities**; package.json and unrelated dependencies
are unchanged.
