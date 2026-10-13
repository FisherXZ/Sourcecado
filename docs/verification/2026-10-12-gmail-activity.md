# Gmail activity verification — 2026-10-12

Status: execution record for #207 on `codex/gmail-activity`. This records checks
performed on Windows, Python 3.14.7 and Node 24.21.0. It is not a native macOS
release qualification. The attached September 28 engineering spec supplied
requirements; its example commands and code were treated as illustrations.

## Behavior proved

- Exact account, thread and recipient matching; plus variants, shared addresses,
  forwards and multiple recipients remain uncertain without body reads.
- Incoming and sent-in-Gmail activity, including archived incoming messages.
- One durable message/event per replay, including after restart, and unchanged
  analysis revisions when the evidence has not changed.
- Separate incoming/outgoing progress, bounded pages/threads/body reads, tracked
  resync after expired history and the original resync boundary across restart.
- Failed passes preserve prior checkpoints, known messages and last success.
- Missing body evidence is visible and a successful retry updates its revision.
- Responses clear only the relevant thread reminder; an explicit reply to an
  older message leaves a newer incoming message unanswered. Import order and
  unknown outgoing times cannot erase a reminder.
- Manual tasks, Done state and existing approved-send authority are preserved.
- The same message's earlier ambiguity resolves with durable audit history.
- Schema 3 → 4 preserves populated person/task data; migration failure rolls back.
- HTTP authentication, saved failure/freshness after restart and bounded API
  previews. Bodies remain in local evidence storage rather than timeline payloads.
- Read-only reader surface, source inspection and connector tripwires prevent
  collection from drafting, sending or enriching.
- UI All/Incoming/Outgoing filters, source links, escaped previews, incomplete
  evidence, collection failure and separate pending analysis.

## Results

The affected backend suites passed **228 tests**, including mail collection,
migrations, API, legacy reply filing, approved send/enrichment, person storage,
tasks and living-brief behavior. Focused UI suites passed **35 tests**. The final
`npm --prefix frontend run build` passed TypeScript checking and the Vite build;
it retains existing large-chunk and missing marketing-image warnings.

The final full backend run reported **1,759 passed, 256 failed, 2 errors and
3 skipped**. A separate original-code baseline reported **1,738 passed,
258 failed, 2 errors and 3 skipped**. Comparing JUnit failure identities found
**no new failures** in the final implementation. Two baseline failures were
absent in the final run; this does not establish a fix for those untouched paths.
The inherited failures include Windows filesystem permission, symlink and
directory-fsync assumptions. No tests were disabled or thresholds weakened.

The full frontend run reported **591 passed and 2 failed**. One production-spine
source assertion compares Windows CRLF against LF; it reproduces with unchanged
baseline source. One scheduled-page selection test failed in the broad run but
passes in both baseline and current isolated runs. Neither source was modified
for #207. The offline sourcing eval fails with the same child-process
`PermissionError` on baseline and current code.

The real-browser example check showed one incoming message, one reply sent in
Gmail and the original approved receipt, ordered by source time. Incoming and
Outgoing filters displayed only their respective directions. A saved manual
task remained available. This check used FakeGmail and isolated state; it is not
evidence of a real Gmail send.

## Limits and follow-through

Real Gmail OAuth and the user-reviewed email test are being configured in a
separate external development state directory. No real send was claimed by the
automated or example-browser checks above. Gmail credentials and runtime tokens
are not committed. Native Tauri/macOS behavior remains unverified on this host.

#208 owns automatic refresh timing, request coalescing and later analysis
execution. The reusable collection entry point and persisted boundaries are
described in [the engineering guide](../engineering/gmail-activity.md).
