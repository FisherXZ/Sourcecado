# Reviewed enrichment, draft, and approved Gmail send

Status: active-stack engineering reference. Covers the person-bound outreach
routes, the send authority, and the at-most-once guarantee. Production approval
dispatch also uses the [external-effect fence](agent-runs.md): an external call
whose outcome is unknown is quarantined for human review, not replayed.

The generic chat-tool paths remain separate. They require approval and use the
effect fence, but generic `gmail_send` does not enforce the person-bound
reviewed-content authority described here, and generic enrichment does not
produce the same person-bound credit/provenance receipt. Unifying these paths is
[#165](https://github.com/FisherXZ/Sourcecado/issues/165); see the
[architecture review](../audit/2026-09-21-onboarding-architecture-review.md).

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

## The chain

1. `POST /v1/people/{id}/outreach/draft` creates a Gmail draft. The recipient
   comes from the person file. The request cannot supply one. The route refuses
   unless `session_id` names a sourcing chat bound to this person. With
   `thread_id` the draft is a reply in that conversation (see
   [Follow-up replies](#follow-up-replies)).
2. `GET /v1/people/{id}/outreach/draft/{draft_id}` re-reads the live draft, so
   an edit made in Gmail becomes visible and the director reviews the version
   that exists right now. `PUT` on the same path saves an edit made inside
   Sourcecado, checked against the body digest the edit started from.
3. `POST /v1/people/{id}/outreach/send-approval` parks one approval. The caller
   sends `reviewed_body_digest` — the version they read — and, for a reply,
   `expected_thread_id`. The route re-reads the draft and refuses if it has
   changed since or sits in another conversation, so no approval is ever
   created for a body or thread nobody reviewed. It also refuses a draft
   addressed to anyone other than this person. Nothing is sent here.
4. `POST /v1/inbox/{approval_id}` with `allow` decides it. This is the existing
   approval route; the send is not a special case of it.

Apollo enrichment follows the same shape:
`POST /v1/people/{id}/enrich-approval` parks an approval whose resource names
the exact person file, the match key, and the credit cost, then the same inbox
route decides it.

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
