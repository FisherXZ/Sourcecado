# Inbound reply filing

Status: active-stack engineering reference. Covers how Sourcecado finds inbound
Gmail replies, decides who each one belongs to, and what it does when it cannot
decide. It extends [approved-send.md](approved-send.md), which produced the
outbound identity this feature reads.

## Domain words

- **Person file** — the durable record for one candidate.
- **Send receipt** — the timeline event the approved-send path files after a
  message leaves. It carries the Gmail message id, thread id, recipient,
  subject, and sending account.
- **Tracked thread** — a Gmail thread that has at least one send receipt on it.
- **Cursor** — the Gmail history id the last completed sync reached.
- **Unassigned reply** — an inbound message on a tracked thread that the
  evidence does not tie to one person. It is filed as a knowledge gap, not as a
  reply.

## What this feature does not do

It reads Gmail and writes person files. It never drafts, never sends, and never
enriches. That is a product invariant, not a preference.

## How a reply is matched

Two facts have to line up, and both come from records that already exist:

1. The inbound message's **thread id** equals the thread id on a send receipt.
2. The inbound message's **sender address** equals, exactly, the recipient
   address on that send receipt.

Addresses are compared after case folding and nothing else. A `+` suffix and a
subdomain each change which mailbox is meant as far as the record can prove, so
neither is folded away.

When both facts hold and no other tracked person is involved, the reply is
filed on that person.

## What we refuse to guess on

Each case below leaves the reply unassigned and writes a knowledge gap on every
tracked person whose thread it landed on. The gap carries the thread id, the
message id, the arrival time, the reason, and the question a human has to
answer. It never carries the reply text, because the message may belong to
someone else.

| Reason | What happened |
|---|---|
| `thread_serves_several_people` | The thread carries approved outreach to more than one tracked person. |
| `thread_has_several_recipients` | Outreach on the thread went to more than one address. |
| `sender_is_not_the_recipient` | Someone other than the person we wrote to answered on their thread. |
| `plus_addressed_variant` | The sender is a `+` variant of the address we wrote to. |
| `shared_address` | The address we wrote to belongs to more than one tracked person. |
| `several_tracked_people_on_thread` | Another tracked person is on the thread's To, Cc, or Delivered-To. |
| `forwarded_thread` | The subject carries a forward marker, so the sender may be relaying someone else. |
| `sender_unreadable` | Gmail returned no readable sender address. |

A message on a thread with no send receipt is not a gap. It is ordinary mail
and nothing is written.

The evidence values come from `run_evidence.Evidence`: `present` for a match,
`absent` for mail that is positively not ours, `ambiguous` for every row above.
There is no second vocabulary for the same distinction.

## The incremental boundary

Steady state uses `users.history.list` with `startHistoryId` set to the stored
cursor, `historyTypes=messageAdded`, and `labelId=INBOX`. Pages are followed to
the end. The cursor moves only after every message in the pass has been filed,
so a failure part-way through repeats work instead of losing it.

There is no full mailbox scan, in any path.

**When the cursor is missing or invalid**, the sync re-reads the tracked threads
directly with `users.threads.get`, which is bounded by our own outreach rather
than by the size of the mailbox, and then stores a fresh cursor from
`users.getProfile`. This happens on the very first run, after the state
directory is cleared, and when Gmail answers 404 because the stored cursor has
aged out of its history window. The new boundary is read *before* the threads,
so a message that arrives during the re-read falls after the cursor and the next
pass picks it up.

Every read uses `format=metadata`, so no message body ever reaches Sourcecado.
Only Gmail's own snippet is stored.

## Idempotency

The durable identity is the Gmail message id. Nothing depends on local ordering,
on a timestamp, or on the cursor.

- **The reply event** is written through `upsert_external_event` with the key
  `gmail:message:{message_id}`. A repeated sync updates one row.
- **The knowledge gap** is written through `upsert_attachment` with the key
  `gmail:reply:{message_id}`. A repeated sync returns the existing row.
- **The Open to In conversation move** writes a state receipt carrying the
  message id that caused it. The next pass finds that receipt and does not move
  the person again — even after the cursor has been lost and the same message
  is read a second time, and even if the director moved the person back to Open
  in between.

Because all three are keyed on the message, a crash anywhere in a pass is safe:
the pass repeats and converges.

## What a filed reply records

`PersonStore.file_inbound_reply` writes one timeline event with
`direction: "inbound"`, the sender, subject, snippet, arrival time, and a
`source_ref` naming the Gmail message, thread, and a link to the thread. It then
moves the person from Open to In conversation, with a state receipt carrying the
same `source_ref`. A person the director already put in In conversation or Done
is left where they are.

The transition is not a special case: `PersonStore.set_sequence` already refuses
an assistant move to In conversation without sent outreach, an inbound reply, or
a director Allow. The reply event is what satisfies it.

## The operating picture

`PersonStore.mail_state` derives, from the ledger alone:

- `last_contact_at` and `last_contact_direction`
- `replied` and `replied_at`
- `follow_up`, which is `reply_unanswered` when the newest mail event is
  inbound, `reply_needs_review` when an unassigned reply names this person, and
  otherwise nothing.

Both `list_board` and `get(expand_sources=True)` carry these, so the Board and
the person file read the same derivation. There is no deal, no stage, no
forecast, and no timer: every value points at a message that exists.

## Enforcing no enrich, draft, or send

The refresh is handed an `InboundReader`, which wraps the Gmail client and
exposes exactly four calls: `profile_history_id`, `history`, `thread`, and
`inbound_message`. The refresh never holds a reference to anything that can
write to Gmail, so a regression has to add a method there in plain sight.

`tests/test_reply_filing.py` proves it three ways: the reader's public surface
is asserted to be those four names, the `reply_filing` module source is parsed
and checked for any attribute, name, or `getattr` literal that could reach a
write, and the HTTP route is exercised against a Gmail whose `send`,
`create_draft`, and `get_draft` raise and an Apollo HTTP that raises on any
POST. That last test asserts a reply was actually filed before it asserts
nothing was sent.

## Testing

Every negative test proves the refresh reached the message before it proves
nothing was filed — `scanned`, `FakeGmail.reads`, and `FakeGmail.thread_reads`
are asserted first. A refresh that returned early would otherwise pass those
tests forever.

Four `test_mutation_*` tests break one guard each from the outside and require
the behaviour to change: replacing `classify` with a best-match version files a
colleague's reply on the wrong person, stubbing the transition guard moves a
person twice, and stripping the external key duplicates the reply event.

The guards were also mutated in the source itself, one at a time, and each
required the owning test to go red. Fifteen mutations were run: the five refusal
branches, plus-suffix folding, the own-message label check, the reply event key,
the gap key, the transition receipt check, cursor advance on failure, cursor
advance before filing, history pagination, the expired-cursor fallback, the
narrowed reader, the state receipt's `source_ref`, and the board's follow-up
decoration. All fifteen went red. Two of them were green on the first run and
found real holes in the suite, both now covered:
`test_our_own_message_is_skipped_even_when_the_receipt_has_no_account` and
`test_a_failure_part_way_through_filing_leaves_the_cursor_where_it_was`.

## Known gap

The reply cursor lives in a `reply_sync` table inside `people.db` rather than in
its own store. That keeps it inside the migration registry's backup and rollback
coverage, which a new database file would not have had without an entry in
`migrations.REGISTRY`. Adding the table did not change `PEOPLE_DB_VERSION`,
which follows how the other tables in that module are created but leaves the
schema change unversioned. Both are worth revisiting when the registry is next
touched.
