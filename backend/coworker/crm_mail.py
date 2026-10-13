"""Collect tracked conversation evidence through a read-only Gmail boundary.

Gmail's SENT label proves delivery, but it does not prove which person owns
the conversation. Ownership also needs the connected account, the original
outreach thread, and one exact recipient. Ambiguity never carries message text.
"""

from typing import Any

from coworker.reply_filing import (
    InboundReader, OutboundIndex, Verdict, _addresses_in, _apply, _is_forwarded,
    _same_mailbox_family, address_of, build_index, classify,
)
from coworker.gmail import GmailError, GmailHistoryExpired
from coworker.people import PersonStore
from coworker.crm_mail_repository import MailRepository
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
    if any(item.account and item.account != account for item in sent):
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


class TrackedMailReader(InboundReader):
    """Metadata, account identity, and attributed bodies; no Gmail writes."""

    def account(self) -> str | None:
        return self._client.account()

    def read(self, *, message_id: str) -> dict[str, Any]:
        return self._client.read(message_id=message_id)


def _content(reader: TrackedMailReader, message: dict[str, Any]) -> dict[str, Any]:
    try:
        full = reader.read(message_id=str(message["id"]))
        # A body is only evidence for the metadata we actually attributed.
        if full.get("id") != message["id"] or full.get("thread_id") != message["thread_id"] or any(
            full.get(key) != message.get(key) for key in ("from", "to", "cc", "subject")):
            return {"body": "", "body_state": "missing"}
        full_body = str(full.get("body") or "")
        body = full_body[:8000]
        return {"body": body, "body_state": "partial" if full.get("truncated") or len(full_body) > 8000 else "present" if body else "missing"}
    except GmailError:
        return {"body": "", "body_state": "missing"}


def refresh_mail(people: PersonStore, reader: TrackedMailReader, *, max_threads: int = 50,
    max_history_pages: int = 5, max_body_reads: int = 100) -> dict[str, Any]:
    """One bounded collection pass. #208 owns scheduling and request sharing.

    Cursor/progress commit only after the pass. Earlier message commits survive
    a failed pass and replay by identity, so interruption loses no known work.
    """
    if not (1 <= max_threads <= 50 and 1 <= max_history_pages <= 5 and 0 <= max_body_reads <= 100):
        raise ValueError("Collection limits are outside the permitted bounds")
    repository = MailRepository(people)
    prior = repository.sync_state()
    cursor = people.reply_cursor()
    result = {"status": "ok", "mode": "incremental" if cursor else "resync",
        "scanned": 0, "filed": 0, "outgoing": 0, "unassigned": 0, "content_partial": 0}
    bodies = 0
    seen: set[str] = set()
    account = None

    def collect(message: dict[str, Any]) -> None:
        nonlocal bodies
        mid = str(message.get("id") or "")
        if not mid:
            raise GmailError("Message metadata has no identity")
        if mid in seen:
            return
        seen.add(mid)
        result["scanned"] += 1
        outbound = "SENT" in (message.get("label_ids") or [])
        verdict = classify_outbound(message, index, account=account) if outbound else classify(message, index)
        if verdict.evidence is Evidence.ABSENT:
            return
        if verdict.evidence is Evidence.AMBIGUOUS:
            result["unassigned"] += int(_apply(people, message, verdict) == "unassigned")
            return
        pid = verdict.person_id
        stored = repository.message(account, mid)
        existing = people._has_external_event(pid, f"gmail:message:{mid}")
        if not outbound and not stored:
            result["filed"] += int(_apply(people, message, verdict) == "filed")
        if stored and stored["body_state"] != "missing":
            content = {"body": stored["body"], "body_state": stored["body_state"]}
        elif bodies < max_body_reads:
            content = _content(reader, message)
            bodies += 1
        else:
            content = {"body": "", "body_state": "missing"}
        repository.save_message(pid, message, direction="outbound" if outbound else "inbound", account=account, content=content)
        if outbound and not existing:
            result["outgoing"] += 1
        result["content_partial"] += int(content["body_state"] != "present")
        people.resolve_reply_gap(pid, message_id=mid)

    try:
        account = address_of(reader.account())
        if not account:
            raise GmailError("Connected account unavailable")
        index = build_index(people, account=account)
        if (prior["account"] and prior["account"] != account) or prior.get("resync_history_id"):
            cursor = None
        resync_cursor = prior.get("resync_history_id") if prior["account"] == account else None
        next_cursor = cursor
        page_token = prior["inbound_page_token"] if cursor else None
        target_cursor = prior["inbound_target_history_id"] if cursor else None
        if cursor:
            try:
                for _ in range(max_history_pages):
                    page = reader.history(start_history_id=cursor, page_token=page_token)
                    target_cursor = target_cursor or page.get("history_id") or cursor
                    for mid in page.get("message_ids") or []:
                        collect(reader.inbound_message(message_id=mid))
                    page_token = page.get("next_page_token")
                    if not page_token:
                        next_cursor = target_cursor
                        target_cursor = None
                        break
            except GmailHistoryExpired:
                result["mode"] = "cursor_reset"
                cursor = None
                page_token = target_cursor = None
        if not cursor:
            # Read the boundary first, so mail arriving during rereads is replayed.
            resync_cursor = resync_cursor or reader.profile_history_id()
            next_cursor = resync_cursor
        after = prior["outbound_after"] if prior["account"] == account else None
        threads = [thread for thread in sorted(index.threads) if not after or thread > after]
        batch = threads[:max_threads]
        for thread in batch:
            for message in reader.thread(thread_id=thread).get("messages") or []:
                collect(message)
        remaining = len(threads) - len(batch)
        # Resync must cover every tracked thread before advancing its boundary.
        if not cursor and remaining:
            next_cursor = people.reply_cursor()
        result["status"] = "partial" if remaining or page_token else "ok"
        result["content_partial"] = repository.incomplete_body_count(account)
        repository.finish_check(status=result["status"], account=account,
            outbound_after=batch[-1] if remaining else None, inbound_page_token=page_token,
            inbound_target_history_id=target_cursor,
            resync_history_id=resync_cursor if remaining else None,
            advance_inbound=True, inbound_cursor=next_cursor,
            warning="collection_pending" if result["status"] == "partial" else "body_unavailable" if result["content_partial"] else None)
        result["remaining_threads"] = remaining
    except (GmailError, ValueError) as exc:
        result["status"] = "failed"
        repository.finish_check(status="failed", account=account,
            warning="gmail_unavailable" if isinstance(exc, GmailError) else "collection_conflict")
    result["cursor"] = people.reply_cursor()
    result["mail_sync"] = repository.public_sync_state()
    return result
