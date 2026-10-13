"""Collect tracked conversation evidence through a read-only Gmail boundary.

Gmail's SENT label proves delivery, but it does not prove which person owns
the conversation. Ownership also needs the connected account, the original
outreach thread, and one exact recipient. Ambiguity never carries message text.
"""

from typing import Any

from coworker.reply_filing import (
    OutboundIndex, Verdict, _addresses_in, _is_forwarded, _same_mailbox_family, address_of,
)
from coworker.run_evidence import Evidence


def classify_outbound(message: dict[str, Any], index: OutboundIndex, *, account: str) -> Verdict:
    sent = index.threads.get(str(message.get("thread_id") or ""))
    if not sent:
        return Verdict(Evidence.ABSENT, None, "untracked_thread", ())
    labels = set(message.get("label_ids") or ())
    if "SENT" not in labels or labels & {"DRAFT", "TRASH"}:
        return Verdict(Evidence.ABSENT, None, "not_sent", ())
    candidates = tuple(sorted({item.person_id for item in sent}))

    def uncertain(reason: str) -> Verdict:
        return Verdict(Evidence.AMBIGUOUS, None, reason, candidates)

    if address_of(message.get("from")) != account:
        return uncertain("sender_is_not_connected_account")
    if len(candidates) != 1:
        return uncertain("thread_serves_several_people")
    recipients = {item.recipient for item in sent}
    if len(recipients) != 1:
        return uncertain("thread_has_several_recipients")
    recipient = next(iter(recipients))
    if len(index.people_by_address.get(recipient, ())) != 1:
        return uncertain("shared_address")
    addresses = _addresses_in(message.get("to"), message.get("cc"), message.get("bcc")) - {account}
    if len(addresses) > 1:
        return uncertain("thread_has_several_recipients")
    if addresses != {recipient}:
        reason = "recipient_is_not_the_person"
        if len(addresses) == 1 and _same_mailbox_family(next(iter(addresses)), recipient):
            reason = "plus_addressed_variant"
        return uncertain(reason)
    if _is_forwarded(message.get("subject")):
        return uncertain("forwarded_thread")
    return Verdict(Evidence.PRESENT, candidates[0], "exact_thread_account_and_recipient", candidates)
