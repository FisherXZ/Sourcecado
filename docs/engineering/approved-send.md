# Reviewed enrichment, draft, and approved Gmail send

Status: active-stack engineering reference. Covers the person-bound outreach
routes, the send authority, and the at-most-once guarantee. Production approval
dispatch also uses the [external-effect fence](agent-runs.md): an external call
whose outcome is unknown is quarantined for human review, not replayed.

`ConsequentialActions` is the shared boundary for person-file HTTP actions and
chat tools. Inbox owns the durable one-action decision/claim; Agent Runs owns
dispatch and unknown-outcome quarantine. HTTP handlers and `tools.execute` cannot
send or enrich by calling a connector around that boundary. Unsupported opaque
MCP actions fail closed; this does not expose a Sourcecado MCP server.

## Domain words

- **Person file** — the durable record for one candidate. Holds the email
  address outreach is sent to.
- **Sourcing chat** — a chat session bound to exactly one person file.
- **Approval** — a row in the `inbox` table. It has a decision (allow, deny,
  none) and an execution status, and they are not the same thing.
- **Send authority** — the identity the director approved: one Gmail account,
  one draft, one recipient, one subject, one body version and, for a reply,
  one Gmail conversation (thread id).
- **Body digest** — a SHA-256 of the message body after line endings and
  trailing whitespace are normalized. It identifies a body version. It is not
  the body, and it is safe to store and to display.

## Person identity revision

Schema 9 adds a monotonic `identity_revision`, initially zero. Effective changes
to the saved first/last name, company, email or Apollo id increment it; email
comparison ignores case and outer whitespace. Repeating the same provider facts
does not increment it, and unrelated CRM edits leave it alone. Restoring identity
facts increments the revision instead of restoring an old token, so A→B→A cannot
revive an earlier approval.

Identity writers call `PersonStore._bump_identity_revision(person_id, before)`
under the people lock after their SQL update and before commit/snapshot, using
the fresh prior row. The helper returns the current revision and does not bump
twice for the same prior revision. Manual correction/protection uses the next
registered migration; it must share this helper rather than broad person versions.

## The chain

1. A person-bound draft is written/read through the existing outreach routes.
   Read and display its full plain-text body before requesting approval.
2. `ConsequentialActions.prepare_send` snapshots the account, saved person,
   recipient, subject, reviewed digest and actual thread, plus identity revision.
   `prepare_enrichment` snapshots `safe_enrichment_match`: prefer a saved exact
   Apollo id; otherwise reject shared local email/name matches or insufficient
   identity. The approved match is immutable and never recomputed as a substitute.
3. Both park an Inbox resource with `authority_version=2`. Stable `operation_id`
   retries return that exact approval, including after success. A changed request
   under the same id conflicts. Existing v1 authorities need a fresh review;
   historical completed outcomes remain historical.
4. Allow must use `scope=once`. The common store rejects Always for sends and
   paid enrichment before it records the decision. A boolean passed to a tool
   cannot supply approval authority. Runtime dispatch must supply the stored
   approval id and actual execution claimant.
5. The existing effect fence records dispatch, then the service checks the live
   person/session/revision and live Gmail content or exact enrichment match.
   Only the owner of that execution claim can call the connector. An identity
   correction, A→B→A or Undo requires a new review; unrelated CRM edits do not.
6. The service records the domain result/source under the approved person.
   The turn loop leaves that receipt alone instead of duplicating it as a generic
   tool event. Tool calls and failures still appear in the run/transcript ledger.

After the last live Gmail read, the final pre-send callback checks current person
identity again and checks the verified snapshot’s headers/thread. This catches a
correction that commits while Gmail is being read, without keeping a SQLite
transaction open across network I/O. Provider read errors use fixed safe text.

The bounded send editor refuses Cc/Bcc, a different From identity, attachments and
HTML parts it cannot show for exact review. Adding them externally after review
also refuses the send. Gmail read failures do not authorize a send.

Enrichment pricing is unknown unless supplied explicitly. Both the estimate and
actual cost are nullable. The Apollo adapter preserves a finite non-negative
numeric `credits_used` response metadata field as `credits_actual`; absent or
invalid metadata remains null. This internal normalization contract does not
promise that Apollo always reports usage. Success, returned fields or a historic
one-credit assumption cannot establish an actual charge.

An exact Apollo-id lookup must return that same provider id before any facts
are applied. A different id, or nonempty facts without a returned id, produces a
completed `no_match` outcome with `review_required`, the reported actual cost and
no person/source mutation. No automatic retry follows this result. An entirely
empty response remains a normal no-match.

An enrichment request with no observed response propagates uncertainty to the
existing effect fence. Repeated approval submissions cannot call it again. A new
lookup is blocked by default; a director may explicitly acknowledge a previous
unknown approval id, review a new cost-unknown lookup and approve the new action
once. That new authority preserves the acknowledgement/history and never replays
the old paid call. An executing request cannot be acknowledged as a retry.

## At most once

The guarantee is that one allow produces at most one Gmail message. Four
independent mechanisms hold it, and none of them is new:

1. **The claim.** `ConversationStore.decide_and_claim_inbox_execution` records
   the decision and claims execution in one statement:
   `UPDATE inbox SET execution_status = 'executing' WHERE id = ? AND
   COALESCE(execution_status, 'pending') = 'pending'`. Exactly one caller sees
   `rowcount == 1`. Every other caller — a duplicate submission, a reconnected
   window, a second socket — gets `claimed = False` and waits for the outcome
   instead of producing one.
2. **The terminal guard.** `complete_inbox_execution` refuses to overwrite a
   row already in `succeeded`, `failed`, `not_run`, `cancelled`, `expired`, or
   `interrupted`. A retry after a failed submission reads that result. It does
   not re-run the work.
3. **The authority check.** `gmail.send_reviewed_draft` re-reads the live draft
   and compares the account, recipient, subject, body digest and, when bound,
   thread id before it calls Gmail. Any mismatch raises and nothing is sent.
4. **Gmail itself.** `drafts.send` deletes the draft. A second send of the same
   draft id is a 404 there, so two approvals for one draft cannot both deliver.
   `FakeGmail` models this, which is why the duplicate case is loud in tests.

Deny sets `execution_status = 'not_run'` without ever claiming. Cancel and
expiry move the row out of `pending`, so `decide_and_claim` returns nothing and
the route answers 409. A provider retry cannot replay a send because
`gmail_send` is not in `permissions.RETRY_SAFE`, from which the turn retry
allowlist is derived.

## Follow-up replies

Each open task has a **Prepare draft** link (#211). It opens a follow-up
editor in the person file. Nothing in this flow completes the task.

- `GET /v1/people/{id}/tasks/{task_id}/follow-up` is read-only. It returns the
  task, the person, the Gmail account, the person's email (or a
  `no_recipient` blocker), their Gmail threads, a starting subject and
  message, talking points, sources, and a prompt for the person's chat. It
  creates no draft and never calls Apollo.
- A person's threads come from `PersonStore.mail_threads`: threads with an
  approved send to them or a reply verified as theirs. Replies we could not
  match to one person are left out.
- The director can write without an email address. Unsent text is saved in
  localStorage per person and task (`followUpDrafts.ts`). Sending waits until
  an address is added.
- A reply draft has the thread id, `In-Reply-To`/`References` taken from the
  newest message's `Message-ID`, and the thread's `Re:` subject. Gmail needs
  all three to keep the reply in the thread. `gmail.reply_target` reads only
  headers, not bodies.
- After creating or editing a draft, the route reads it back. If Gmail put it
  in a different thread, the route returns `thread_not_preserved` instead of
  quietly starting a new thread. The UI keeps the text, and the approval
  refuses that draft. If the thread can't be read (`thread_unavailable`), no
  draft is made.
- Editing a draft denies any pending approval for it. The new text needs a
  new approval anyway, because its digest changed.
- The send record stores the `task_id`. The task stays open.

The background reply check uses `InboundReader`, which has no draft, edit,
reply or send methods. `test_reply_filing.py` checks this.

## What a send records

A successful send calls `PersonStore.record_approved_send`, which:

- files one timeline event keyed on `gmail:message:{message_id}`, carrying the
  Gmail message id, thread id, draft id, recipient, subject, body digest,
  account, approval id and, for a follow-up, task id, correlated to the session
  and run;
- advances the person to `open` only when they have no sequence state yet. A
  person already in conversation or done is left where the director put them.

A refused or failed send files nothing and advances nobody.

Sending and person-file recording are separate operations. If Gmail succeeds but
`record_approved_send` fails, the response preserves the successful send and
returns `receipt_error` with `person_event_id: null`. Do not send again to repair
that missing receipt. Startup does not currently repair it; projection repair,
safe run continuation, and delivery of recorded final answers remain in
[#128](https://github.com/FisherXZ/Sourcecado/issues/128).

## Optional reminder after a send

The first-email and task follow-up panels offer **Set follow-up** only after
a successful send result contains `sent: true` and a Gmail message ID. The
director chooses a calendar date in the saved timezone, edits the task text,
and saves or skips. There is no default date and no task created by mail
refresh, an incoming reply, denial, failure or an uncertain send outcome.

`GET /v1/people/{id}/sent-messages/{message_id}/reminder` returns the saved
timezone and a stable message-specific operation ID. Both this read and the
corresponding `POST` require the person's saved send event and its succeeded,
person-bound send approval to name that exact message. A missing receipt does
not authorize a reminder; never resend to repair it. The panel keeps a failed
save's date and text and retries only the task. If the saved timezone changes,
refresh the follow-up options to review the date in that timezone.

`crm_reminders.py` has no connector access. It creates one ordinary task through
the existing transactional task writer, atomically with a person-owned Gmail
source pointer, task history and operation receipt. Repeated or concurrent
saves, including after restart, return the same task. Changed arguments for an
already saved operation conflict; use the normal task editor to change it.
Replies do not complete or remove this human task, and creating it does not
change the sequence. Existing task controls provide editing, completion,
cancellation and edit undo.

Component screenshots and visual limitations are recorded in
[after-send reminder evidence](../evidence/2026-11-02-after-send-reminders/README.md).

## Testing

`backend/tests/test_approved_send.py`, `backend/tests/test_follow_up_reply.py`
and `backend/tests/test_approved_enrichment.py` count real
calls into the fake service — `FakeGmail.send_attempts` for every submission
including the ones that raise, `FakeGmail.sends` for the messages that left, and
`FakeHttp.calls` for Apollo credits. No test decides whether an external effect
happened by reading a status string.

Each negative case asserts how far the flow got before proving nothing was sent.
`execution_status == "failed"` means the claim was granted and the executor ran,
so a zero send count is a guard working rather than a path nobody reached.
