# Gmail activity PR-readiness review — 2026-10-19

Scope: [#207](https://github.com/FisherXZ/Sourcecado/issues/207), on
`codex/gmail-activity`. Reviewed against the ticket and the September 28 Contacts
engineering spec; example code in the attached spec was illustrative. Integrated
upstream `4809f6f` before the final checks. Windows, Python 3.14.7, Node 24.21.0,
npm 11.19.0. This supersedes the earlier verification record for readiness.

## Review and corrections

Independent Standards and Spec reviews identified three collection defects and
one wording defect. Regression tests reproduced each before its correction:

- Expired inbound history during a partial outgoing pass could skip an earlier
  tracked thread. Recovery now starts at the first tracked thread and retains
  its original history boundary across batches/restart.
- A changed ambiguity reason, candidate count, or resolved warning could collide
  with immutable attachment upserts and stall collection. Message-specific gaps
  now update/reopen transactionally with audit receipts and version snapshots.
  Unchanged replay writes nothing; generic attachments remain immutable.
- A successful match resolved only the winning person's warning. It now resolves
  that message's warnings on every earlier live candidate, while content is
  filed only on the supported owner.
- Incoming mail now says “Reply to review,” without asserting that the sender
  expects a response.

An additional security regression proved that a body whose Bcc differs from the
attributed metadata was accepted. Bcc is now checked with the other identity
headers before accepting the body. Tests first failed, then passed.

Main's current task lifecycle and description implementation replaces the older,
unmerged task implementation inherited by this branch. Duplicate task modules,
UI, routes and stale execution notes were removed. The registered mail migration
is now **5 → 6**, after main's migrations. Populated person, protected description,
task, task-change and operation records survive; failure restores the backup.

Both reviewers inspected the resulting worktree again and found no remaining
actionable #207 blockers. These are AI-assisted code reviews, not Peer Approval.

## Automated verification

All tests used temporary/synthetic state, not the operator's real mailbox.

| Check | Result |
| --- | --- |
| Gmail collection/API/migration, existing reply filing, Gmail connector, approved sending, people, tasks, descriptions and living brief | **216 passed** |
| Complete frontend suite, after clean locked install and tool update | **624 passed** |
| CRM/person/task/production-spine suites after final UI polish | **63 passed** |
| Brief, mail API and task checks after obsolete projection cleanup | **53 passed** |
| TypeScript and production Vite build | **Passed** |
| Clean `npm ci`, dependency scripts disabled by `.npmrc` | **Passed** |
| Tauri CLI load (`npm run tauri -- --version`) | **Passed**, 2.11.4; not a native build |
| Frontend `npm audit` against final lockfile | **0 vulnerabilities** |
| `git diff --check` | **Passed** |

Focused backend invocation covered `test_crm_mail`, `test_crm_mail_api`,
`test_crm_mail_migrations`, `test_crm_tasks`, `test_person_descriptions`,
`test_reply_filing`, `test_gmail`, `test_approved_send`, `test_people` and
`test_living_brief`. Full checks used `python -m pytest backend/tests`,
`npm test`, `npm run build` and `python -m coworker.evals --suite sourcing`.
JUnit, JSON reports, raw logs, eval artifacts and fictional browser fixtures
remain outside the repository.

### Existing platform failures

The full backend run reported **1,782 passed, 256 failed, 2 errors, 3 skipped**.
A separate unchanged copy of upstream `4809f6f`, using the same Python environment,
reported **1,747 passed, 257 failed, 2 errors, 3 skipped**. Comparing JUnit failure
identities found **no new failures**. One untouched restart test failed only in
the baseline; this does not establish a fix. Many failures involve Windows
directory-fsync, permissions, symlink or path assumptions. The older migration
suite has the same 14 platform failures; its mail/task schema assertions pass.

The sourcing evaluation failed with `PermissionError: eval child failed` on both
main and this branch. No test was skipped, removed or suppressed to obtain a pass.
One existing frontend test raced against its loading state; it now waits for the
partial-brief banner it tests. Another normalizes CRLF before comparing source,
retaining the same strict AppShell-only assertion on either platform.

Linux CI and the repository's macOS preview/build job must still pass before
merge. Existing large-bundle and missing marketing-image build warnings remain.

## CRM browser demonstration

Used the real local React/FastAPI application with **FakeGmail**, no AI provider,
and a separate state directory. Fictional Ada and Maya had current descriptions,
manual tasks, tracked outreach and In conversation/Done states.

Observed Contacts search/status controls and person links; opened a person file;
used section shortcuts and verified focus moved to the target; filtered incoming
and outgoing mail; checked local timestamps, Sourcecado/Gmail send provenance,
Gmail-only source URLs and collapsed source details. Refresh kept an unsaved
handoff draft. The uncertain message's private-text canary was absent from the
page. The existing task controls remained available; no task or sequence action
was triggered by collection.

Viewport checks at **320, 768, 1024 and 1440px** showed no document-wide horizontal
overflow. Narrow-screen shortcuts clear the fixed menu button. Direction controls
retain pressed state, keyboard focus rings and theme colors. Browser console
inspection returned no errors. Screenshot evidence contains fictional data only
and remains outside Git. Saved failure/freshness, offline reads and restart are
also covered by the API and UI tests.

## Security and credentials

The collector remains read-only: no drafting, sending, enrichment or model calls.
Tripwire tests execute actual attributed collection and body reads, then assert
no forbidden effect was reached. Existing sending still requires Sourcecado's
explicit reviewed approval. API token checks, parameterized SQL, conservative
person attribution, bounded stored bodies/previews, React text escaping and
Gmail-only external links were checked. Error responses omit raw provider errors.

Compared the real development credential/token values with feature Git blobs and
all tracked/nonignored working files without printing those values. **No actual
credential matched** and no credential/runtime file is tracked. Generic scanning
also reported existing fake-key security fixtures and example address references
identical to main; these are not a new Gmail credential leak. No scanner proves
the absence of every possible unknown secret. Added ignores for downloaded OAuth
client JSON, `secrets.json` and token files.

Real Gmail OAuth configuration and runtime state remain outside Git and were not
deleted, disconnected or sent anywhere. The earlier operator-approved real send
is historical evidence; this review sent no real email. Real incoming and
sent-directly-in-Gmail paths remain covered by deterministic tests, rather than
a new live recipient exchange.

The earlier unshipped #207 development database used a different schema-4 meaning
than upstream. Its state remains preserved. Do not open it under the new registry
without a deliberate backed-up development-state conversion; use isolated fresh
state for this review. This does not affect upstream schema-5 upgrades.

Frontend development/test advisories were removed with the contained supported
tool upgrade described in [ADR 0006](../engineering/adr/0006-patched-frontend-tooling.md).
Backend `pip-audit` reports one existing moderate `mcp==1.29.1` advisory:
[GHSA-84m7-p3x7-pcfv](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-84m7-p3x7-pcfv).
It affects stateful Streamable HTTP **servers**. Sourcecado imports the SDK's
client session/transport; it does not instantiate that server path. Deferred from
#207 as unexercised server code; reassess by **2026-11-02**, or before adding any
MCP server. The audit is not described as globally clean.

## Scope and readiness

#207 is ready for pull-request review, subject to CI and human Peer Approval.
The tested behavior includes tracked incoming/outgoing mail, correct attribution,
durable replay, source links, failure/freshness and thread-specific reminders.
Manual tasks, human descriptions, Done state and send permissions remain intact.

[#208](https://github.com/FisherXZ/Sourcecado/issues/208) retains automatic timing,
trigger coalescing and analysis execution. It can call the existing serialized
collection entry point and consume pending evidence revisions. This PR adds no
competing background loop and does not claim to implement the whole Contacts epic.
