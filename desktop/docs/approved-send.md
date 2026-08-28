# Reviewed enrichment, draft, and approved Gmail send

Status: active-stack engineering reference. Covers the person-bound outreach
routes, the send authority, and the at-most-once guarantee. It does not cover
process-crash ambiguity — a send interrupted by a crash is recorded as
`interrupted`, outcome unknown, and hardening that is separate later work.

## Domain words

- **Person file** — the durable record for one candidate. Holds the email
  address outreach is sent to.
- **Sourcing chat** — a chat session bound to exactly one person file.
- **Approval** — a row in the `inbox` table. It has a decision (allow, deny,
  none) and an execution status, and they are not the same thing.
- **Send authority** — the identity the director approved: one Gmail account,
  one draft, one recipient, one subject, one body version.
- **Body digest** — a SHA-256 of the message body after line endings and
  trailing whitespace are normalized. It identifies a body version. It is not
  the body, and it is safe to store and to display.

## The chain

1. `POST /v1/people/{id}/outreach/draft` creates a Gmail draft. The recipient
   comes from the person file. The request cannot supply one. The route refuses
   unless `session_id` names a sourcing chat bound to this person.
2. `GET /v1/people/{id}/outreach/draft/{draft_id}` re-reads the live draft. The
   draft is edited in Gmail; this is how an edit becomes visible, and how the
   director reviews the version that exists right now.
3. `POST /v1/people/{id}/outreach/send-approval` parks one approval. The caller
   sends `reviewed_body_digest` — the version they read. The route re-reads the
   draft and refuses if it has changed since, so no approval is ever created for
   a body nobody reviewed. It also refuses a draft addressed to anyone other
   than this person. Nothing is sent here.
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
   and compares the account, recipient, subject, and body digest before it calls
   Gmail. Any mismatch raises and nothing is sent.
4. **Gmail itself.** `drafts.send` deletes the draft. A second send of the same
   draft id is a 404 there, so two approvals for one draft cannot both deliver.
   `FakeGmail` models this, which is why the duplicate case is loud in tests.

Deny sets `execution_status = 'not_run'` without ever claiming. Cancel and
expiry move the row out of `pending`, so `decide_and_claim` returns nothing and
the route answers 409. A provider retry cannot replay a send because
`gmail_send` is not in `permissions.RETRY_SAFE`, from which the turn retry
allowlist is derived.

## What a send records

A successful send calls `PersonStore.record_approved_send`, which:

- files one timeline event keyed on `gmail:message:{message_id}`, carrying the
  Gmail message id, thread id, draft id, recipient, subject, body digest,
  account, and approval id, correlated to the session and run;
- advances the person to `open` only when they have no sequence state yet. A
  person already in conversation or done is left where the director put them.

A refused or failed send files nothing and advances nobody.

## Testing

`tests/test_approved_send.py` and `tests/test_approved_enrichment.py` count real
calls into the fake service — `FakeGmail.send_attempts` for every submission
including the ones that raise, `FakeGmail.sends` for the messages that left, and
`FakeHttp.calls` for Apollo credits. No test decides whether an external effect
happened by reading a status string.

Each negative case asserts how far the flow got before proving nothing was sent.
`execution_status == "failed"` means the claim was granted and the executor ran,
so a zero send count is a guard working rather than a path nobody reached.
