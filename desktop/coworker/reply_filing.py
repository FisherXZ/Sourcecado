"""Find inbound Gmail replies and put each one on exactly one person file.

Filing a reply on the wrong person is worse than filing nothing. The person
file is the evidence a director reads before deciding what to do next, so a
reply attached to the wrong name becomes a wrong decision, and nothing in the
record says it was a guess. Every match here therefore rests on two facts that
can be pointed at: the Gmail thread the outreach went out on, and the address
the outreach went out to. When those two stop short of naming one person, the
reply stays unassigned and the refusal is filed as a knowledge gap that says
why. A named gap is a correct answer. A confident wrong association is not.

The refusals are deliberate and they are listed in ``_QUESTIONS`` below. The
tempting shortcut - one thread, one candidate, file it - is exactly what this
module does not do.

Nothing here can enrich, draft, or send. The refresh holds an ``InboundReader``,
which exposes four read calls and no way to reach a write.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import getaddresses, parseaddr
from typing import Any

from coworker.gmail import GmailError, GmailHistoryExpired
from coworker.people import PersonStore
from coworker.run_evidence import Evidence


class InboundReader:
    """The whole Gmail surface a background refresh is allowed to hold.

    Narrowing is the enforcement, not a convention: the refresh never has a
    reference to a client that can draft or send, so a regression has to add a
    method here in plain sight rather than slip through one that already
    exists.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def profile_history_id(self) -> str:
        return self._client.profile_history_id()

    def history(
        self, *, start_history_id: str, page_token: str | None = None
    ) -> dict[str, Any]:
        return self._client.history(
            start_history_id=start_history_id, page_token=page_token
        )

    def thread(self, *, thread_id: str) -> dict[str, Any]:
        return self._client.thread(thread_id=thread_id)

    def inbound_message(self, *, message_id: str) -> dict[str, Any]:
        return self._client.inbound_message(message_id=message_id)


# --- addresses -----------------------------------------------------------


def address_of(header: str | None) -> str | None:
    """The bare address in one header value, case-folded.

    Case folding is safe: mailbox names are compared case-insensitively in
    practice and Gmail itself does. Nothing else is normalized. A ``+`` suffix
    and a subdomain both change which mailbox is meant as far as this module
    can prove, so folding either one away would be the guess this module
    exists to refuse.
    """
    _name, address = parseaddr(header or "")
    address = address.strip().casefold()
    return address if "@" in address else None


def _addresses_in(*headers: str | None) -> set[str]:
    joined = ", ".join(header for header in headers if header)
    found = set()
    for _name, address in getaddresses([joined]):
        cleaned = address.strip().casefold()
        if "@" in cleaned:
            found.add(cleaned)
    return found


def _same_mailbox_family(left: str, right: str) -> bool:
    """Whether two addresses differ only by a plus suffix on the same domain."""
    left_local, _, left_domain = left.partition("@")
    right_local, _, right_domain = right.partition("@")
    if left_domain != right_domain:
        return False
    return left_local.split("+", 1)[0] == right_local.split("+", 1)[0]


_FORWARD_MARKERS = ("fwd:", "fw:")


def _is_forwarded(subject: str | None) -> bool:
    text = (subject or "").strip().casefold()
    while text.startswith("re:"):
        text = text[3:].lstrip()
    return text.startswith(_FORWARD_MARKERS)


# --- what we know we sent ------------------------------------------------


@dataclass(frozen=True)
class SentOutreach:
    person_id: str
    recipient: str


@dataclass(frozen=True)
class OutboundIndex:
    """Every thread Sourcecado sent outreach on, and who it was sent to."""

    threads: dict[str, tuple[SentOutreach, ...]]
    people_by_address: dict[str, frozenset[str]]
    accounts: frozenset[str]


def build_index(people: PersonStore) -> OutboundIndex:
    """Read the send receipts the approved-send path already files.

    Nothing new is stored for this. ``PersonStore.record_approved_send``
    already keeps the Gmail message id, thread id, recipient, and sending
    account for every message that left.
    """
    threads: dict[str, list[SentOutreach]] = {}
    addresses: dict[str, set[str]] = {}
    accounts: set[str] = set()
    for person in people.list_people():
        person_id = str(person["person_id"])
        email = address_of(person.get("email"))
        if email:
            addresses.setdefault(email, set()).add(person_id)
        for event in people.timeline(person_id):
            payload = event.get("payload")
            if event.get("kind") != "send" or not isinstance(payload, dict):
                continue
            thread_id = str(payload.get("thread_id") or "")
            recipient = address_of(payload.get("to"))
            if not thread_id or not recipient:
                continue
            addresses.setdefault(recipient, set()).add(person_id)
            account = address_of(payload.get("account"))
            if account:
                accounts.add(account)
            sent = SentOutreach(person_id=person_id, recipient=recipient)
            if sent not in threads.setdefault(thread_id, []):
                threads[thread_id].append(sent)
    return OutboundIndex(
        threads={key: tuple(value) for key, value in threads.items()},
        people_by_address={key: frozenset(value) for key, value in addresses.items()},
        accounts=frozenset(accounts),
    )


# --- the judgement -------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """What the record supports about one inbound message.

    ``evidence`` reuses the run-record vocabulary rather than coining a second
    one: ``present`` is a match backed by exact thread and sender, ``absent``
    is a message positively not on any thread we sent on, and ``ambiguous`` is
    the run's own "it does not know" - which is exactly what a reply we refuse
    to attribute is.
    """

    evidence: Evidence
    person_id: str | None
    reason: str
    candidates: tuple[str, ...]


_QUESTIONS = {
    "thread_serves_several_people": (
        "Which person does this reply belong to? This Gmail thread carries "
        "approved outreach to more than one tracked person."
    ),
    "thread_has_several_recipients": (
        "Which address answered? Outreach on this Gmail thread went to more "
        "than one address for this person."
    ),
    "sender_is_not_the_recipient": (
        "Who sent this reply? It arrived on this person's Gmail thread from an "
        "address we did not write to."
    ),
    "plus_addressed_variant": (
        "Is this the same mailbox? The reply came from a plus-addressed "
        "variant of the address we wrote to, which we cannot prove is the "
        "same person."
    ),
    "shared_address": (
        "Which person replied? The address we wrote to is shared by more than "
        "one tracked person."
    ),
    "several_tracked_people_on_thread": (
        "Which person does this reply belong to? More than one tracked person "
        "is on this Gmail thread."
    ),
    "forwarded_thread": (
        "Who is answering? The reply arrived on a forwarded Gmail thread, so "
        "the sender may be relaying someone else's words."
    ),
    "sender_unreadable": (
        "Who sent this reply? Gmail returned no readable sender address for it."
    ),
}


def classify(message: dict[str, Any], index: OutboundIndex) -> Verdict:
    """Decide who one inbound message belongs to, or refuse to decide.

    Reads in order of how cheaply a case can be ruled out, and every branch
    that stops short of one person returns ``ambiguous`` with the reason. There
    is deliberately no fallback that picks a best candidate.
    """
    thread_id = str(message.get("thread_id") or "")
    sent = index.threads.get(thread_id)
    if not sent:
        return Verdict(Evidence.ABSENT, None, "untracked_thread", ())
    candidates = tuple(sorted({item.person_id for item in sent}))

    labels = set(message.get("label_ids") or ())
    if labels & {"SENT", "DRAFT", "TRASH"}:
        return Verdict(Evidence.ABSENT, None, "own_message", ())
    sender = address_of(message.get("from"))
    if sender is None:
        return Verdict(Evidence.AMBIGUOUS, None, "sender_unreadable", candidates)
    if sender in index.accounts:
        return Verdict(Evidence.ABSENT, None, "own_message", ())

    if len(candidates) > 1:
        return Verdict(
            Evidence.AMBIGUOUS, None, "thread_serves_several_people", candidates
        )
    person_id = candidates[0]
    recipients = {item.recipient for item in sent}
    if len(recipients) > 1:
        return Verdict(
            Evidence.AMBIGUOUS, None, "thread_has_several_recipients", candidates
        )
    recipient = next(iter(recipients))

    if sender != recipient:
        reason = (
            "plus_addressed_variant"
            if _same_mailbox_family(sender, recipient)
            else "sender_is_not_the_recipient"
        )
        return Verdict(Evidence.AMBIGUOUS, None, reason, candidates)
    if len(index.people_by_address.get(recipient, frozenset())) > 1:
        return Verdict(Evidence.AMBIGUOUS, None, "shared_address", candidates)

    others: set[str] = set()
    for address in _addresses_in(
        message.get("to"), message.get("cc"), message.get("delivered_to")
    ):
        others |= set(index.people_by_address.get(address, frozenset()))
    others.discard(person_id)
    if others:
        return Verdict(
            Evidence.AMBIGUOUS,
            None,
            "several_tracked_people_on_thread",
            tuple(sorted({person_id, *others})),
        )
    if _is_forwarded(message.get("subject")):
        return Verdict(Evidence.AMBIGUOUS, None, "forwarded_thread", candidates)

    return Verdict(Evidence.PRESENT, person_id, "exact_thread_and_sender", candidates)


# --- the incremental refresh --------------------------------------------


def refresh_replies(people: PersonStore, reader: InboundReader) -> dict[str, Any]:
    """Read what arrived since the cursor and file it. Never writes to Gmail.

    The cursor only moves after every message in the pass has been filed, so a
    failure part-way through repeats work instead of losing it. Repeating is
    free: every write below is keyed on the Gmail message id.
    """
    index = build_index(people)
    cursor = people.reply_cursor()
    mode = "incremental" if cursor else "resync"
    scanned = filed = unassigned = 0
    try:
        if cursor is None:
            messages, next_cursor = _resync(reader, index)
        else:
            try:
                messages, next_cursor = _incremental(reader, cursor)
            except GmailHistoryExpired:
                mode = "cursor_reset"
                messages, next_cursor = _resync(reader, index)
        for message in messages:
            scanned += 1
            outcome = _apply(people, message, classify(message, index))
            filed += int(outcome == "filed")
            unassigned += int(outcome == "unassigned")
    except (GmailError, ValueError) as exc:
        return {
            "status": "failed",
            "mode": mode,
            "error": str(exc),
            "cursor": cursor,
            "scanned": scanned,
            "filed": filed,
            "unassigned": unassigned,
        }
    people.set_reply_cursor(next_cursor)
    return {
        "status": "ok",
        "mode": mode,
        "cursor": next_cursor,
        "scanned": scanned,
        "filed": filed,
        "unassigned": unassigned,
    }


def _incremental(
    reader: InboundReader, cursor: str
) -> tuple[list[dict[str, Any]], str]:
    message_ids: list[str] = []
    page_token: str | None = None
    next_cursor = cursor
    while True:
        page = reader.history(start_history_id=cursor, page_token=page_token)
        for identifier in page.get("message_ids") or []:
            if identifier not in message_ids:
                message_ids.append(identifier)
        next_cursor = page.get("history_id") or next_cursor
        page_token = page.get("next_page_token")
        if not page_token:
            break
    return [
        reader.inbound_message(message_id=identifier) for identifier in message_ids
    ], next_cursor


def _resync(
    reader: InboundReader, index: OutboundIndex
) -> tuple[list[dict[str, Any]], str]:
    """Recover a lost boundary by re-reading only the threads we sent on.

    Bounded by our own outreach, never by the size of the mailbox. The new
    boundary is read first: a message that arrives while the threads are being
    read then falls after the cursor and the next pass picks it up, instead of
    landing in the gap between the two reads.
    """
    next_cursor = reader.profile_history_id()
    messages: list[dict[str, Any]] = []
    for thread_id in sorted(index.threads):
        messages.extend(reader.thread(thread_id=thread_id).get("messages") or [])
    return messages, next_cursor


def _apply(
    people: PersonStore, message: dict[str, Any], verdict: Verdict
) -> str:
    if verdict.evidence is Evidence.ABSENT:
        return "skipped"
    common = {
        "message_id": str(message.get("id") or ""),
        "thread_id": message.get("thread_id"),
        "received_at": message.get("received_at"),
    }
    if verdict.evidence is Evidence.PRESENT and verdict.person_id:
        outcome = people.file_inbound_reply(
            verdict.person_id,
            sender=address_of(message.get("from")) or "",
            subject=message.get("subject"),
            snippet=message.get("snippet") or "",
            **common,
        )
        # A replayed message writes nothing new. Counting it again would tell
        # the operator a reply arrived when none did.
        recorded = not outcome["already_filed"] or outcome[
            "advanced_to_in_conversation"
        ]
        return "filed" if recorded else "skipped"
    recorded = False
    for person_id in verdict.candidates:
        outcome = people.record_reply_gap(
            person_id,
            reason=verdict.reason,
            question=_QUESTIONS.get(verdict.reason, "Which person does this reply belong to?"),
            candidate_count=len(verdict.candidates),
            **common,
        )
        recorded = recorded or not outcome["already_recorded"]
    return "unassigned" if recorded else "skipped"
