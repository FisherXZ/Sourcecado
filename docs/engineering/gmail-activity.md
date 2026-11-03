# Tracked Gmail activity

Status: active-stack engineering reference for [#207](https://github.com/FisherXZ/Sourcecado/issues/207).
Collection extends [inbound reply filing](reply-filing.md) and reads the identity
established by [approved sending](approved-send.md). It never creates drafts,
sends mail, enriches people, completes tasks, or reopens Done sequences.

## Attribution before content

A tracked thread has an approved send receipt. Incoming attribution retains the
exact thread and sender rules of reply filing. Outgoing attribution additionally
requires Gmail's SENT label, the connected sending account, one tracked person,
and exactly the original recipient across To, Cc and Bcc, excluding the account.
Addresses are case folded; plus variants are not collapsed. Shared addresses,
multiple people or recipients, forwards and conflicting senders stay uncertain.
Drafts, trash and untracked threads are skipped.

Uncertain messages create deduplicated knowledge gaps without message text.
Only a match can fetch a body. The body response must match the attributed
message identity and headers. Bodies are bounded to 8,000 characters and stored
as untrusted local evidence with `present`, `partial` or `missing` completeness.
The UI receives a bounded preview and source link, not the stored body. A body
failure leaves the matched activity visible and reports incomplete evidence.

## Persistence and replay

Schema 6 adds three tables after main's task/description migrations through the existing backup/rollback path:

- `gmail_messages`: account/message identity, person ownership and bounded body.
- `gmail_collection_state`: collection status, timestamps and independent inbound
  history pagination and outgoing tracked-thread progress.
- `crm_analysis_state`: latest evidence revision and analyzed revision per person.

`MailRepository.save_message` commits message evidence, its timeline projection
and its analysis revision together. Replaying identical evidence changes none
of them. Existing approved-send events retain their approval/run authority; a
later sanctioned send receipt can promote an already collected outgoing event
without adding a second event. See [the storage decision](adr/0005-tracked-mail-evidence.md).

The collector uses a read-only `TrackedMailReader`, exposing the legacy reader's
four metadata calls plus account identity and body reads. Each pass reads at
most five history pages, fifty tracked threads and one hundred bodies. Gmail
history requests ask for at most one hundred history records per page. Stored bodies are reused;
missing bodies are retried on subsequent tracked-thread reads.

Inbound history progress and outgoing thread progress are independent. Tracked
thread rereads discover sent-in-Gmail replies and archived incoming messages.
There is no mailbox-wide search. First collection and expired history reread only
tracked threads, taking a profile boundary before the reread. A partial resync
persists that original boundary across restart until all tracked threads finish.
Failed passes retain both prior checkpoints and the previous successful check;
already committed messages replay safely. Partial passes report unfinished work
without claiming a new successful check. Missing bodies have a separate warning.

When uncertain evidence becomes attributable, that message's warnings on all
earlier candidate people are resolved with audit receipts. Changing or reopening
ambiguity updates the same warning; identical replay changes nothing. Version
snapshots retain the earlier explanation. Resolved gaps no longer appear as open
attention or brief gaps. Generic attachment upserts remain strict.

## Reminders and relationship state

`PersonStore.mail_state` compares source timestamps rather than import order.
Each thread retains its latest incoming reminder. A later outgoing message clears
only its thread's reminder, and an explicit In-Reply-To must name the latest
incoming RFC Message-ID. Unknown outgoing times cannot clear a known reminder.
Other threads, manually saved tasks and Done state remain unchanged. Incoming
filing retains the existing once-per-message Open-to-In-conversation transition.

## API and UI

- `POST /v1/replies/refresh` collects both directions and returns counts, saved
  `mail_sync`, and the current board. The existing local token is required.
- `GET /v1/board?include_mail_state=true` includes saved `mail_sync`; the default
  response retains the previous four-list shape for existing callers.
- `GET /v1/people/{id}` includes collection freshness and separate `mail_analysis`.

The person file offers section shortcuts, a local Refresh Gmail button,
All/Incoming/Outgoing filters, local timestamps and Gmail thread links. Raw source
identifiers sit in expandable details. Refresh preserves unsaved handoff text.
Collection failure, unfinished work, incomplete body evidence and pending
analysis are distinct states. Last successful collection survives navigation
and restart. No model analysis is performed by this collector.

## Automatic refresh coordination

[#208](https://github.com/FisherXZ/Sourcecado/issues/208) owns timing, overlapping
request coalescing, startup resume and bounded analysis retries. The app exposes
`app.state.collect_tracked_mail`, the same synchronous function used by manual
refresh, protected by a collection lock. An async scheduler should call it with
`asyncio.to_thread` and coalesce concurrent triggers around it. The lock serializes
calls; it does not itself share one result between callers. Analysis revisions
provide the later worker's boundary without treating collection as analysis.

See [current verification and limitations](../verification/2026-10-19-gmail-pr-readiness.md).
