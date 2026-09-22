"""S3: discover inbound Gmail replies and file each on exactly one person.

Two properties are worth more than the happy path here.

The first is that a refusal is a result. A reply that cannot be tied to one
person through exact thread and recipient evidence stays unassigned and files a
knowledge gap. Every negative test below proves the refresh really ran and
really read the message before it proves nothing was filed, because a refresh
that returned early would pass a weaker test forever.

The second is that nothing in the background path can enrich, draft, or send.
That is asserted structurally — the reader surface, the module source, and a
tripwire Gmail whose ``send`` and ``create_draft`` raise — rather than by the
absence of a call.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp, HttpError
from coworker.connectors.google_oauth import READ_SCOPE, load_google, save_google
from coworker.gmail import (
    FakeGmail,
    GmailApi,
    GmailError,
    GmailHistoryExpired,
    MissingGmail,
    body_digest,
)
from coworker.people import PersonStore
from coworker.reply_filing import (
    InboundReader,
    address_of,
    build_index,
    classify,
    refresh_replies,
)
from coworker.run_evidence import Evidence
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-reply-filing"
HEADERS = {TOKEN_HEADER: TOKEN}
ACCOUNT = "director@sourcecado.test"
SUBJECT = "Thursday?"
BODY = "Hi Ada,\n\nWould Thursday work for a short call?\n\nFisher"


# --- fixtures ------------------------------------------------------------


def _gmail() -> FakeGmail:
    gmail = FakeGmail()
    gmail.account_email = ACCOUNT
    return gmail


def _person(
    people: PersonStore,
    *,
    first: str = "Ada",
    last: str = "Analytic",
    email: str = "ada@analytic.example",
    apollo_id: str = "apollo-ada",
) -> dict:
    person = people.keep_from_apollo(
        apollo_id=apollo_id,
        first_name=first,
        last_name_obfuscated=last,
        title="Head of Data",
        company="Analytic",
    )
    people.apply_enrichment(person["person_id"], email=email)
    loaded = people.get(person["person_id"])
    assert loaded is not None
    return loaded


def _sent(people: PersonStore, gmail: FakeGmail, person: dict, *, subject: str = SUBJECT) -> dict:
    """Send approved outreach the way the send path does, and file its receipt."""
    draft = gmail.create_draft(to=person["email"], subject=subject, body=BODY)
    result = gmail.send(draft_id=draft["id"])
    people.record_approved_send(
        person["person_id"],
        message_id=result["id"],
        thread_id=result["threadId"],
        draft_id=draft["id"],
        to=person["email"],
        subject=subject,
        body_digest=body_digest(BODY),
        account=ACCOUNT,
        approval_id=f"appr_{result['id']}",
    )
    return result


def _reader(gmail: FakeGmail) -> InboundReader:
    return InboundReader(gmail)


def _baseline(people: PersonStore, gmail: FakeGmail) -> None:
    """Run the first pass, which has no cursor, and forget what it touched.

    A fresh install has no incremental boundary, so its first refresh reads the
    tracked threads and then stores one. Tests about the steady state start
    from there; the counters are cleared so what follows is only theirs.
    """
    assert refresh_replies(people, _reader(gmail))["status"] == "ok"
    assert people.reply_cursor()
    gmail.reads.clear()
    gmail.thread_reads.clear()
    gmail.history_calls.clear()


def _inbound(people: PersonStore, person_id: str) -> list[dict]:
    return [
        event
        for event in people.timeline(person_id)
        if isinstance(event.get("payload"), dict)
        and event["payload"].get("direction") == "inbound"
    ]


def _transitions(people: PersonStore, person_id: str, state: str) -> list[dict]:
    return [
        event
        for event in people.timeline(person_id)
        if event.get("kind") == "state"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("state") == state
    ]


def _gaps(people: PersonStore, person_id: str) -> list[dict]:
    person = people.get(person_id, expand_sources=True)
    assert person is not None
    return [
        gap
        for gap in person["knowledge_gaps"]
        if gap["fields"].get("kind") == "unassigned_reply"
    ]


# --- criterion 1: the send already stores the identity a reply needs -----


def test_the_approved_send_receipt_already_carries_thread_and_message_identity(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people, email="ada@analytic.example")
    sent = _sent(people, gmail, person)

    send_events = [e for e in people.timeline(person["person_id"]) if e["kind"] == "send"]
    assert len(send_events) == 1
    payload = send_events[0]["payload"]
    assert payload["message_id"] == sent["id"]
    assert payload["thread_id"] == sent["threadId"]
    assert payload["to"] == "ada@analytic.example"

    index = build_index(people)
    assert sent["threadId"] in index.threads
    assert index.threads[sent["threadId"]][0].person_id == person["person_id"]
    assert index.threads[sent["threadId"]][0].recipient == "ada@analytic.example"


# --- criterion 3 and 5: the normal reply --------------------------------


def test_a_reply_on_the_sent_thread_files_on_that_person_and_opens_the_conversation(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    assert people.get(person["person_id"])["sequence_state"] == "open"
    _baseline(people, gmail)

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="Ada Analytic <ada@analytic.example>",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works. Send an invite.",
    )

    result = refresh_replies(people, _reader(gmail))

    assert result["status"] == "ok"
    assert result["scanned"] >= 1
    assert result["filed"] == 1
    assert result["unassigned"] == 0
    assert gmail.reads == ["in_ada_1"]

    replies = _inbound(people, person["person_id"])
    assert len(replies) == 1
    payload = replies[0]["payload"]
    assert payload["message_id"] == "in_ada_1"
    assert payload["thread_id"] == sent["threadId"]
    assert payload["from"] == "ada@analytic.example"
    assert payload["snippet"] == "Thursday works. Send an invite."
    assert payload["received_at"]
    assert payload["source_ref"]["provider"] == "Gmail"
    assert payload["source_ref"]["message_id"] == "in_ada_1"

    assert people.get(person["person_id"])["sequence_state"] == "in_conversation"
    moves = _transitions(people, person["person_id"], "in_conversation")
    assert len(moves) == 1
    assert moves[0]["payload"]["source_ref"]["message_id"] == "in_ada_1"
    assert moves[0]["payload"]["source_ref"]["thread_id"] == sent["threadId"]
    assert _gaps(people, person["person_id"]) == []


def test_operator_person_file_never_receives_another_persons_reply_attention(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    operator = _person(
        people,
        first="Fisher",
        last="Zhang",
        email=ACCOUNT,
        apollo_id="apollo-operator",
    )
    bob = _person(
        people,
        first="Bob",
        last="Builder",
        email="bob@analytic.example",
        apollo_id="apollo-bob",
    )
    sent = _sent(people, gmail, bob)
    _baseline(people, gmail)

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_bob_operator_person_1",
        sender="bob@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    result = refresh_replies(people, _reader(gmail))

    assert result["scanned"] == 1
    assert result["filed"] == 1
    assert result["unassigned"] == 0
    board = people.list_board()
    operator_row = next(
        row for row in board["backlog"] if row["person_id"] == operator["person_id"]
    )
    bob_row = next(
        row for row in board["in_conversation"] if row["person_id"] == bob["person_id"]
    )
    assert operator_row["replied"] is False
    assert operator_row["follow_up"] == {"needed": False, "reason": None}
    assert bob_row["replied"] is True
    assert bob_row["follow_up"] == {"needed": True, "reason": "reply_unanswered"}


def test_a_reply_leaves_a_person_the_director_already_moved_where_they_are(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    people.set_sequence(
        person["person_id"],
        "done",
        actor="director",
        rationale_summary="Closed before the reply arrived.",
    )

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Sorry for the slow reply.",
    )
    result = refresh_replies(people, _reader(gmail))

    assert result["filed"] == 1
    assert len(_inbound(people, person["person_id"])) == 1
    assert people.get(person["person_id"])["sequence_state"] == "done"
    assert _transitions(people, person["person_id"], "in_conversation") == []


# --- criterion 10: no reply ---------------------------------------------


def test_unrelated_inbox_mail_is_read_and_files_nothing(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    _sent(people, gmail, person)
    _baseline(people, gmail)

    gmail.deliver(
        thread_id="thread_newsletter",
        message_id="in_news_1",
        sender="digest@news.example",
        to=ACCOUNT,
        subject="Your weekly digest",
        snippet="Five stories this week.",
    )
    gmail.deliver(
        thread_id="thread_recruiting",
        message_id="in_news_2",
        sender="noreply@jobs.example",
        to=ACCOUNT,
        subject="A candidate applied",
        snippet="New application.",
    )

    result = refresh_replies(people, _reader(gmail))

    # The refresh really reached both messages before it filed nothing.
    assert result["status"] == "ok"
    assert result["scanned"] == 2
    assert sorted(gmail.reads) == ["in_news_1", "in_news_2"]
    assert result["filed"] == 0
    assert result["unassigned"] == 0
    assert _inbound(people, person["person_id"]) == []
    assert _gaps(people, person["person_id"]) == []
    assert people.get(person["person_id"])["sequence_state"] == "open"


def test_our_own_message_on_a_tracked_thread_is_never_filed_as_a_reply(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)

    # A second outbound nudge on the same thread, as Gmail stores it.
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="out_ada_2",
        sender=ACCOUNT,
        to="ada@analytic.example",
        subject="Re: Thursday?",
        snippet="Bumping this.",
        label_ids=("SENT",),
    )
    people.set_reply_cursor(None)
    result = refresh_replies(people, _reader(gmail))

    assert result["status"] == "ok"
    assert result["scanned"] >= 2  # the resync read the whole thread
    assert result["filed"] == 0
    assert _inbound(people, person["person_id"]) == []
    assert people.get(person["person_id"])["sequence_state"] == "open"


def test_our_own_message_is_skipped_even_when_the_receipt_has_no_account(tmp_path):
    """``SendAuthority.account`` can be None, so the label has to carry it."""
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    people.record_approved_send(
        person["person_id"],
        message_id="out_ada_1",
        thread_id="thread_ada",
        draft_id="draft_ada",
        to="ada@analytic.example",
        subject=SUBJECT,
        body_digest=body_digest(BODY),
        account=None,
        approval_id="appr_ada",
    )
    gmail.deliver(
        thread_id="thread_ada",
        message_id="out_ada_1",
        sender=ACCOUNT,
        to="ada@analytic.example",
        subject=SUBJECT,
        snippet="Would Thursday work?",
        label_ids=("SENT",),
    )

    result = refresh_replies(people, _reader(gmail))

    assert result["mode"] == "resync"
    assert result["scanned"] == 1  # it really read our own message
    assert result["filed"] == 0
    assert result["unassigned"] == 0
    assert _inbound(people, person["person_id"]) == []
    assert people.get(person["person_id"])["sequence_state"] == "open"


# --- criterion 4: the cases we refuse to guess on -----------------------


def test_a_plus_addressed_variant_stays_unassigned_and_files_a_gap(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people, email="ada@analytic.example")
    sent = _sent(people, gmail, person)
    _baseline(people, gmail)

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_alias_1",
        sender="Ada Analytic <ada+sourcing@analytic.example>",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    result = refresh_replies(people, _reader(gmail))

    assert result["scanned"] == 1
    assert gmail.reads == ["in_alias_1"]
    assert result["filed"] == 0
    assert result["unassigned"] == 1
    assert _inbound(people, person["person_id"]) == []
    assert people.get(person["person_id"])["sequence_state"] == "open"

    gaps = _gaps(people, person["person_id"])
    assert len(gaps) == 1
    assert gaps[0]["fields"]["reason"] == "plus_addressed_variant"
    assert gaps[0]["fields"]["evidence"] == Evidence.AMBIGUOUS.value
    assert gaps[0]["fields"]["message_id"] == "in_alias_1"
    assert gaps[0]["fields"]["thread_id"] == sent["threadId"]
    # The gap never carries the reply text: it may belong to someone else.
    assert "Thursday works." not in str(gaps[0]["fields"])


def test_a_reply_from_a_colleague_on_the_thread_stays_unassigned(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    _baseline(people, gmail)

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_colleague_1",
        sender="Chief of Staff <cos@analytic.example>",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Ada asked me to reply.",
    )
    result = refresh_replies(people, _reader(gmail))

    assert result["scanned"] == 1
    assert result["unassigned"] == 1
    assert _inbound(people, person["person_id"]) == []
    gaps = _gaps(people, person["person_id"])
    assert [gap["fields"]["reason"] for gap in gaps] == ["sender_is_not_the_recipient"]


def test_a_thread_carrying_outreach_to_two_tracked_people_stays_unassigned(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    ada = _person(people, email="ada@analytic.example")
    bob = _person(
        people,
        first="Bob",
        last="Builder",
        email="bob@analytic.example",
        apollo_id="apollo-bob",
    )
    sent = _sent(people, gmail, ada)
    # A second approved send landed on the same Gmail thread.
    people.record_approved_send(
        bob["person_id"],
        message_id="out_bob_1",
        thread_id=sent["threadId"],
        draft_id="draft_bob",
        to="bob@analytic.example",
        subject=SUBJECT,
        body_digest=body_digest(BODY),
        account=ACCOUNT,
        approval_id="appr_bob",
    )
    _baseline(people, gmail)

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_shared_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    result = refresh_replies(people, _reader(gmail))

    assert result["scanned"] == 1
    assert result["filed"] == 0
    assert result["unassigned"] == 1
    assert _inbound(people, ada["person_id"]) == []
    assert _inbound(people, bob["person_id"]) == []
    for person_id in (ada["person_id"], bob["person_id"]):
        gaps = _gaps(people, person_id)
        assert [gap["fields"]["reason"] for gap in gaps] == [
            "thread_serves_several_people"
        ]
        assert gaps[0]["fields"]["candidate_count"] == 2


def test_a_shared_alias_two_people_answer_to_stays_unassigned(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    ada = _person(people, email="team@analytic.example")
    _person(
        people,
        first="Bob",
        last="Builder",
        email="team@analytic.example",
        apollo_id="apollo-bob",
    )
    sent = _sent(people, gmail, ada)
    _baseline(people, gmail)

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_alias_2",
        sender="team@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    result = refresh_replies(people, _reader(gmail))

    assert result["scanned"] == 1
    assert result["unassigned"] == 1
    assert _inbound(people, ada["person_id"]) == []
    assert [gap["fields"]["reason"] for gap in _gaps(people, ada["person_id"])] == [
        "shared_address"
    ]


def test_a_second_tracked_person_copied_on_the_thread_stays_unassigned(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    ada = _person(people, email="ada@analytic.example")
    bob = _person(
        people,
        first="Bob",
        last="Builder",
        email="bob@analytic.example",
        apollo_id="apollo-bob",
    )
    sent = _sent(people, gmail, ada)
    _baseline(people, gmail)

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_cc_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        cc="Bob Builder <bob@analytic.example>",
        subject="Re: Thursday?",
        snippet="Looping in Bob.",
    )
    result = refresh_replies(people, _reader(gmail))

    assert result["scanned"] == 1
    assert result["filed"] == 0
    assert result["unassigned"] == 1
    assert _inbound(people, ada["person_id"]) == []
    assert _inbound(people, bob["person_id"]) == []
    assert [gap["fields"]["reason"] for gap in _gaps(people, ada["person_id"])] == [
        "several_tracked_people_on_thread"
    ]
    assert [gap["fields"]["reason"] for gap in _gaps(people, bob["person_id"])] == [
        "several_tracked_people_on_thread"
    ]


def test_a_forwarded_thread_stays_unassigned(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    _baseline(people, gmail)

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_fwd_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Fwd: Thursday?",
        snippet="Forwarding for context.",
    )
    result = refresh_replies(people, _reader(gmail))

    assert result["scanned"] == 1
    assert result["unassigned"] == 1
    assert _inbound(people, person["person_id"]) == []
    assert [gap["fields"]["reason"] for gap in _gaps(people, person["person_id"])] == [
        "forwarded_thread"
    ]


def test_an_unreadable_sender_stays_unassigned(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    _baseline(people, gmail)

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_broken_1",
        sender="Ada Analytic",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    result = refresh_replies(people, _reader(gmail))

    assert result["scanned"] == 1
    assert result["unassigned"] == 1
    assert [gap["fields"]["reason"] for gap in _gaps(people, person["person_id"])] == [
        "sender_unreadable"
    ]


def test_address_comparison_never_folds_a_plus_suffix_or_a_subdomain():
    assert address_of("Ada <ADA@Analytic.Example>") == "ada@analytic.example"
    assert address_of("no address here") is None
    assert address_of("ada+sourcing@analytic.example") == "ada+sourcing@analytic.example"
    assert address_of("ada@mail.analytic.example") != address_of("ada@analytic.example")


# --- criterion 8: duplicates, restart, repeated sync --------------------


def test_a_repeated_refresh_files_the_reply_once_and_transitions_once(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )

    first = refresh_replies(people, _reader(gmail))
    second = refresh_replies(people, _reader(gmail))
    third = refresh_replies(people, _reader(gmail))

    assert first["filed"] == 1
    # The later passes really ran; they had nothing new inside the cursor.
    assert second["status"] == "ok" and third["status"] == "ok"
    assert second["scanned"] == 0
    assert len(_inbound(people, person["person_id"])) == 1
    assert len(_transitions(people, person["person_id"], "in_conversation")) == 1


def test_a_cursor_reset_replays_the_same_reply_without_duplicating_it(tmp_path):
    """The durable identity is the Gmail message id, not the cursor position."""
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    assert refresh_replies(people, _reader(gmail))["filed"] == 1

    # The director puts the person back on Open, then the cursor is lost.
    people.set_sequence(
        person["person_id"],
        "open",
        actor="director",
        rationale_summary="Not a real conversation yet.",
    )
    people.set_reply_cursor(None)

    replay = refresh_replies(people, _reader(gmail))

    assert replay["status"] == "ok"
    assert replay["mode"] == "resync"
    # It really re-read the message before it declined to re-file it.
    assert replay["scanned"] >= 1
    assert "in_ada_1" in [message["id"] for message in _read_thread(gmail, sent["threadId"])]
    assert len(_inbound(people, person["person_id"])) == 1
    assert len(_transitions(people, person["person_id"], "in_conversation")) == 1
    assert people.get(person["person_id"])["sequence_state"] == "open"


def _read_thread(gmail: FakeGmail, thread_id: str) -> list[dict]:
    return gmail.thread(thread_id=thread_id)["messages"]


def test_a_restart_reuses_the_stored_cursor_and_files_nothing_twice(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    refresh_replies(people, _reader(gmail))
    cursor = people.reply_cursor()
    assert cursor

    restarted = PersonStore(tmp_path)
    assert restarted.reply_cursor() == cursor

    after = refresh_replies(restarted, _reader(gmail))
    assert after["status"] == "ok"
    assert after["scanned"] == 0
    assert len(_inbound(restarted, person["person_id"])) == 1
    assert len(_transitions(restarted, person["person_id"], "in_conversation")) == 1

    # A second reply after the restart still lands.
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_2",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="One more thing.",
    )
    later = refresh_replies(restarted, _reader(gmail))
    assert later["filed"] == 1
    assert len(_inbound(restarted, person["person_id"])) == 2
    assert len(_transitions(restarted, person["person_id"], "in_conversation")) == 1


def test_a_repeated_ambiguous_reply_files_one_gap_per_person(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_colleague_1",
        sender="cos@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Ada asked me to reply.",
    )
    refresh_replies(people, _reader(gmail))
    people.set_reply_cursor(None)
    again = refresh_replies(people, _reader(gmail))

    # The replay really re-read and re-judged the message; it recorded nothing
    # new, so it reports nothing new.
    assert again["scanned"] >= 1
    assert again["unassigned"] == 0
    assert len(_gaps(people, person["person_id"])) == 1


# --- criterion 2: the incremental boundary ------------------------------


def test_the_refresh_reads_only_what_arrived_after_the_cursor(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    _baseline(people, gmail)
    for index in range(3):
        gmail.deliver(
            thread_id="thread_noise",
            message_id=f"in_noise_{index}",
            sender="digest@news.example",
            to=ACCOUNT,
            subject="Digest",
            snippet="News.",
        )
    first = refresh_replies(people, _reader(gmail))
    assert first["scanned"] == 3

    gmail.reads.clear()
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    second = refresh_replies(people, _reader(gmail))

    assert second["scanned"] == 1
    assert gmail.reads == ["in_ada_1"]
    assert second["filed"] == 1


def test_the_first_pass_has_no_cursor_and_reads_the_tracked_threads(tmp_path):
    """A reply that beat the first sync still lands, without a mailbox scan."""
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    gmail.deliver(
        thread_id="thread_newsletter",
        message_id="in_news_1",
        sender="digest@news.example",
        to=ACCOUNT,
        subject="Your weekly digest",
        snippet="Five stories.",
    )
    assert people.reply_cursor() is None

    result = refresh_replies(people, _reader(gmail))

    assert result["mode"] == "resync"
    assert gmail.thread_reads == [sent["threadId"]]
    assert gmail.history_calls == []
    # Only the tracked thread was read, so the newsletter was never touched.
    assert result["filed"] == 1
    assert result["scanned"] == 2  # our own send plus the reply
    assert people.reply_cursor() == str(gmail.history_id)
    assert len(_inbound(people, person["person_id"])) == 1


def test_history_pagination_reaches_every_page(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    gmail.history_page_size = 2
    person = _person(people)
    sent = _sent(people, gmail, person)
    _baseline(people, gmail)
    for index in range(4):
        gmail.deliver(
            thread_id="thread_noise",
            message_id=f"in_noise_{index}",
            sender="digest@news.example",
            to=ACCOUNT,
            subject="Digest",
            snippet="News.",
        )
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )

    result = refresh_replies(people, _reader(gmail))

    assert len(gmail.history_calls) == 3  # five messages, two per page
    assert result["scanned"] == 5
    assert result["filed"] == 1
    assert len(_inbound(people, person["person_id"])) == 1


def test_an_expired_cursor_falls_back_to_the_tracked_threads(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    _baseline(people, gmail)

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    # Gmail no longer keeps history that far back.
    gmail.history_floor = gmail.history_id + 1

    result = refresh_replies(people, _reader(gmail))

    assert result["mode"] == "cursor_reset"
    assert gmail.thread_reads == [sent["threadId"]]
    assert result["filed"] == 1
    assert people.reply_cursor() == str(gmail.history_id)
    assert len(_inbound(people, person["person_id"])) == 1


def test_a_connector_failure_leaves_the_cursor_alone_and_files_nothing(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    refresh_replies(people, _reader(gmail))
    cursor = people.reply_cursor()

    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )

    class Offline(FakeGmail):
        def history(self, *, start_history_id, page_token=None):
            raise GmailError("Gmail is unreachable.")

    offline = Offline()
    offline.inbox = gmail.inbox
    offline.history_id = gmail.history_id

    failed = refresh_replies(people, InboundReader(offline))

    assert failed["status"] == "failed"
    assert failed["filed"] == 0
    assert people.reply_cursor() == cursor
    assert _inbound(people, person["person_id"]) == []

    recovered = refresh_replies(people, _reader(gmail))
    assert recovered["status"] == "ok"
    assert recovered["filed"] == 1
    assert people.reply_cursor() != cursor


def test_a_message_read_failure_does_not_advance_the_cursor(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    refresh_replies(people, _reader(gmail))
    cursor = people.reply_cursor()
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )

    class Flaky(FakeGmail):
        def inbound_message(self, *, message_id):
            raise GmailError("Gmail dropped the read.")

    flaky = Flaky()
    flaky.inbox = gmail.inbox
    flaky.history_id = gmail.history_id

    failed = refresh_replies(people, InboundReader(flaky))
    assert failed["status"] == "failed"
    assert people.reply_cursor() == cursor

    recovered = refresh_replies(people, _reader(gmail))
    assert recovered["filed"] == 1


def test_a_failure_part_way_through_filing_leaves_the_cursor_where_it_was(
    tmp_path, monkeypatch
):
    """The cursor is the record of a *completed* pass, not of a started one."""
    people = PersonStore(tmp_path)
    gmail = _gmail()
    first = _person(people, email="ada@analytic.example")
    second = _person(
        people,
        first="Bob",
        last="Builder",
        email="bob@analytic.example",
        apollo_id="apollo-bob",
    )
    sent_first = _sent(people, gmail, first)
    sent_second = _sent(people, gmail, second)
    _baseline(people, gmail)
    cursor = people.reply_cursor()

    for thread_id, message_id, sender in (
        (sent_first["threadId"], "in_ada_1", "ada@analytic.example"),
        (sent_second["threadId"], "in_bob_1", "bob@analytic.example"),
    ):
        gmail.deliver(
            thread_id=thread_id,
            message_id=message_id,
            sender=sender,
            to=ACCOUNT,
            subject="Re: Thursday?",
            snippet="Thursday works.",
        )

    original = PersonStore.file_inbound_reply
    seen: list[str] = []

    def fail_on_the_second(self, person_id, *, message_id, **kwargs):
        seen.append(message_id)
        if len(seen) > 1:
            raise ValueError("the person store rejected the write")
        return original(self, person_id, message_id=message_id, **kwargs)

    monkeypatch.setattr(PersonStore, "file_inbound_reply", fail_on_the_second)
    failed = refresh_replies(people, _reader(gmail))

    # It really got part of the way: one reply landed before the write failed.
    assert failed["status"] == "failed"
    assert failed["filed"] == 1
    assert len(seen) == 2
    assert people.reply_cursor() == cursor

    monkeypatch.undo()
    recovered = refresh_replies(people, _reader(gmail))

    assert recovered["status"] == "ok"
    assert recovered["scanned"] == 2  # the whole pass replayed
    assert recovered["filed"] == 1  # only the one that never landed
    assert len(_inbound(people, first["person_id"])) == 1
    assert len(_inbound(people, second["person_id"])) == 1
    assert people.reply_cursor() != cursor


def test_a_disconnected_gmail_reports_failure_rather_than_raising(tmp_path):
    people = PersonStore(tmp_path)
    _person(people)
    result = refresh_replies(people, InboundReader(MissingGmail()))
    assert result["status"] == "failed"
    assert result["filed"] == 0


# --- criterion 10: token refresh on the live connector ------------------


def test_the_live_history_read_refreshes_an_expired_token_and_retries(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(
        secrets,
        {
            "refresh_token": "rt",
            "access_token": "stale",
            "email": ACCOUNT,
            "scopes": [READ_SCOPE],
        },
    )
    history_url = "https://gmail.googleapis.com/gmail/v1/users/me/history"

    class Once401(FakeHttp):
        def get(self, url, **kwargs):
            self.calls.append({"method": "GET", "url": url, **kwargs})
            if url == history_url and not self.refreshed:
                raise HttpError(401, url)
            return {
                "history": [
                    {"id": "9", "messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}]}
                ],
                "historyId": "9",
            }

        def post(self, url, **kwargs):
            self.calls.append({"method": "POST", "url": url, **kwargs})
            if url == "https://oauth2.googleapis.com/token":
                self.refreshed = True
                return {"access_token": "fresh"}
            raise RuntimeError(url)

    http = Once401()
    http.refreshed = False
    api = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")

    page = api.history(start_history_id="5")

    assert page["message_ids"] == ["m1"]
    assert load_google(secrets)["access_token"] == "fresh"
    assert [call["url"] for call in http.calls].count(history_url) == 2
    assert not any("drafts" in call["url"] for call in http.calls)


def test_the_live_history_read_names_an_expired_cursor(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [READ_SCOPE]})
    history_url = "https://gmail.googleapis.com/gmail/v1/users/me/history"
    http = FakeHttp({history_url: HttpError(404, history_url)})
    api = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")

    with pytest.raises(GmailHistoryExpired):
        api.history(start_history_id="1")


def test_the_live_reads_never_ask_gmail_for_a_message_body(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [READ_SCOPE]})
    messages_url = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    http = FakeHttp(
        {
            f"{messages_url}/m1": {
                "id": "m1",
                "threadId": "t1",
                "labelIds": ["INBOX"],
                "snippet": "Thursday works.",
                "internalDate": "1756000000000",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Ada <ada@analytic.example>"},
                        {"name": "Subject", "value": "Re: Thursday?"},
                    ]
                },
            }
        }
    )
    api = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")

    message = api.inbound_message(message_id="m1")

    assert message["from"] == "Ada <ada@analytic.example>"
    assert message["snippet"] == "Thursday works."
    assert message["received_at"].startswith("2025-")
    assert http.calls[-1]["params"]["format"] == "metadata"
    assert "body" not in message


# --- criterion 9: the background path cannot enrich, draft, or send -----

FORBIDDEN_NAMES = frozenset(
    {
        "send",
        "create_draft",
        "get_draft",
        "send_reviewed_draft",
        "authority_for_draft",
        "verify_send_authority",
        "draft_snapshot",
        "record_apollo_enrichment",
        "apply_enrichment",
        "enrich",
        "enrich_contact",
        "park",
    }
)


def _referenced_names(source: str) -> set[str]:
    """Every name the source could reach a callable through.

    Attributes and bare names cover the direct calls. String constants are
    only collected inside ``getattr``, which is the one way a write could hide
    behind a literal. Plain string literals are excluded on purpose: reading
    the ``send`` receipts a reply matches against requires the word.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
        ):
            found |= {
                argument.value
                for argument in node.args
                if isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            }
    return found


def test_the_source_check_itself_catches_an_injected_send():
    direct = "def refresh(reader):\n    return reader.send(draft_id='d1')\n"
    assert _referenced_names(direct) & FORBIDDEN_NAMES == {"send"}
    hidden = "def refresh(client):\n    return getattr(client, 'send')(draft_id='d1')\n"
    assert _referenced_names(hidden) & FORBIDDEN_NAMES == {"send"}
    # A plain literal is not a call target and must not trip the check.
    reading = "def index(event):\n    return event['kind'] == 'send'\n"
    assert _referenced_names(reading) & FORBIDDEN_NAMES == set()


def test_the_reply_filing_module_cannot_name_an_enrich_draft_or_send_call():
    source = Path(inspect.getfile(refresh_replies)).read_text(encoding="utf-8")
    assert _referenced_names(source) & FORBIDDEN_NAMES == set()


def test_the_inbound_reader_exposes_four_read_calls_and_nothing_else():
    surface = {
        name
        for name in dir(InboundReader)
        if not name.startswith("_") and callable(getattr(InboundReader, name))
    }
    assert surface == {"profile_history_id", "history", "thread", "inbound_message"}
    for name in ("send", "create_draft", "get_draft", "drafts", "sends"):
        assert not hasattr(InboundReader, name)


class TripwireGmail(FakeGmail):
    """Gmail whose write calls fail the test instead of doing anything."""

    def send(self, *, draft_id: str):
        raise AssertionError("a background refresh reached Gmail send")

    def create_draft(self, *, to: str, subject: str, body: str):
        raise AssertionError("a background refresh reached Gmail draft")

    def get_draft(self, *, draft_id: str):
        raise AssertionError("a background refresh reached a Gmail draft read")


def test_the_refresh_route_files_a_reply_without_touching_send_draft_or_apollo(tmp_path):
    gmail = TripwireGmail()
    gmail.account_email = ACCOUNT

    class TripwireHttp(FakeHttp):
        def post(self, url, **kwargs):
            raise AssertionError(f"a background refresh reached {url}")

    app = create_app(
        token=TOKEN,
        provider=None,
        state=tmp_path,
        gmail=gmail,
        http=TripwireHttp(),
        apollo_key="unused",
    )
    people = app.state.people
    person = _person(people)
    # The send receipt is filed directly: the send path itself is not under test.
    people.record_approved_send(
        person["person_id"],
        message_id="out_ada_1",
        thread_id="thread_ada",
        draft_id="draft_ada",
        to="ada@analytic.example",
        subject=SUBJECT,
        body_digest=body_digest(BODY),
        account=ACCOUNT,
        approval_id="appr_ada",
    )
    gmail.deliver(
        thread_id="thread_ada",
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )

    client = TestClient(app)
    res = client.post("/v1/replies/refresh", headers=HEADERS)

    assert res.status_code == 200
    body = res.json()
    # Non-vacuous: the refresh really processed and filed a reply.
    assert body["refresh"]["status"] == "ok"
    assert body["refresh"]["scanned"] == 1
    assert body["refresh"]["filed"] == 1
    assert gmail.sends == []
    assert gmail.send_attempts == []
    assert gmail.drafts == []
    assert [row["person_id"] for row in body["board"]["in_conversation"]] == [
        person["person_id"]
    ]


def test_a_filed_reply_still_leaves_the_diagnostic_preview_current_main_serves(
    tmp_path,
):
    """Stacked #109 cannot merge onto current main; checking it out drops #110.

    Public seam: POST /v1/replies/refresh, GET /v1/board, and
    POST /v1/diagnostics/bundle/preview. Issue #67's unique work files a reply
    through the first two. Current main also serves the diagnostic preview.
    The published stacked send commit conflicts on gmail.py, people.py, and
    PersonFile.tsx, so GitHub will not merge, and that tree does not carry
    the preview route.
    """
    app = create_app(token=TOKEN, provider=None, state=tmp_path, gmail=_gmail())
    people = app.state.people
    gmail = app.state.gmail
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    client = TestClient(app)

    refreshed = client.post("/v1/replies/refresh", headers=HEADERS)
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh"]["filed"] == 1

    board = client.get("/v1/board", headers=HEADERS)
    assert board.status_code == 200
    talking = board.json()["in_conversation"]
    assert [row["person_id"] for row in talking] == [person["person_id"]]
    assert talking[0]["follow_up"] == {"needed": True, "reason": "reply_unanswered"}

    preview = client.post(
        "/v1/diagnostics/bundle/preview",
        json={"check": "permissions", "store_id": "state_root"},
        headers=HEADERS,
    )
    assert preview.status_code == 200
    body = preview.json()["preview"]
    assert body["bundle_version"] == 1
    assert body["subject"] == {
        "kind": "doctor_finding",
        "check": "permissions",
        "store_id": "state_root",
    }
    assert body["run"] is None
    diagnostics = tmp_path / "diagnostics"
    assert not diagnostics.exists()


# --- criteria 6 and 7: the operating picture ----------------------------


def test_the_board_shows_last_contact_replied_and_the_follow_up_reason(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    waiting = _person(people, email="ada@analytic.example")
    replied = _person(
        people,
        first="Bob",
        last="Builder",
        email="bob@analytic.example",
        apollo_id="apollo-bob",
    )
    _sent(people, gmail, waiting)
    sent_bob = _sent(people, gmail, replied)
    gmail.deliver(
        thread_id=sent_bob["threadId"],
        message_id="in_bob_1",
        sender="bob@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    assert refresh_replies(people, _reader(gmail))["filed"] == 1

    board = people.list_board()
    open_row = next(
        row for row in board["open"] if row["person_id"] == waiting["person_id"]
    )
    assert open_row["replied"] is False
    assert open_row["last_contact_at"]
    assert open_row["last_contact_direction"] == "outbound"
    assert open_row["follow_up"] == {"needed": False, "reason": None}

    talking_row = next(row for row in board["in_conversation"])
    assert talking_row["person_id"] == replied["person_id"]
    assert talking_row["replied"] is True
    assert talking_row["last_contact_direction"] == "inbound"
    assert talking_row["follow_up"] == {"needed": True, "reason": "reply_unanswered"}


def test_an_unassigned_reply_puts_the_person_on_the_board_as_needing_review(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_colleague_1",
        sender="cos@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Ada asked me to reply.",
    )
    assert refresh_replies(people, _reader(gmail))["unassigned"] == 1

    row = people.list_board()["open"][0]
    assert row["replied"] is False
    assert row["follow_up"] == {"needed": True, "reason": "reply_needs_review"}


def test_the_person_file_shows_the_reply_summary_source_and_follow_up_state(tmp_path):
    app = create_app(token=TOKEN, provider=None, state=tmp_path, gmail=_gmail())
    people = app.state.people
    gmail = app.state.gmail
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="Ada Analytic <ada@analytic.example>",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works. Send an invite.",
    )
    client = TestClient(app)
    refreshed = client.post("/v1/replies/refresh", headers=HEADERS).json()
    assert refreshed["refresh"]["filed"] == 1

    res = client.get(f"/v1/people/{person['person_id']}", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()

    reply = next(
        event
        for event in body["timeline"]
        if event["payload"].get("direction") == "inbound"
    )
    assert reply["payload"]["snippet"] == "Thursday works. Send an invite."
    assert reply["payload"]["received_at"]
    assert reply["payload"]["source_ref"]["thread_id"] == sent["threadId"]
    assert body["person"]["sequence_state"] == "in_conversation"
    assert body["person"]["replied"] is True
    assert body["person"]["follow_up"]["reason"] == "reply_unanswered"


def test_the_person_file_shows_an_unassigned_reply_as_a_knowledge_gap(tmp_path):
    app = create_app(token=TOKEN, provider=None, state=tmp_path, gmail=_gmail())
    people = app.state.people
    gmail = app.state.gmail
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_colleague_1",
        sender="cos@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Ada asked me to reply.",
    )
    client = TestClient(app)
    assert client.post("/v1/replies/refresh", headers=HEADERS).json()["refresh"][
        "unassigned"
    ] == 1

    body = client.get(f"/v1/people/{person['person_id']}", headers=HEADERS).json()
    gaps = [
        gap
        for gap in body["person"]["knowledge_gaps"]
        if gap["fields"].get("kind") == "unassigned_reply"
    ]
    assert len(gaps) == 1
    assert gaps[0]["fields"]["reason"] == "sender_is_not_the_recipient"
    assert gaps[0]["fields"]["question"]
    assert body["person"]["follow_up"]["reason"] == "reply_needs_review"


# --- mutation harness: every guard above is load-bearing ----------------


def _ambiguous_case(tmp_path):
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    _baseline(people, gmail)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_colleague_1",
        sender="cos@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Ada asked me to reply.",
    )
    return people, gmail, person


def test_mutation_dropping_the_recipient_check_files_on_the_wrong_person(
    tmp_path, monkeypatch
):
    """Criterion 3 and 4: 'best match' is exactly the wrong implementation."""
    import coworker.reply_filing as reply_filing

    people, gmail, person = _ambiguous_case(tmp_path)

    def best_match(message, index):
        thread = index.threads.get(message.get("thread_id") or "")
        if not thread:
            return reply_filing.Verdict(Evidence.ABSENT, None, "untracked_thread", ())
        return reply_filing.Verdict(
            Evidence.PRESENT, thread[0].person_id, "best_match", ()
        )

    monkeypatch.setattr(reply_filing, "classify", best_match)
    result = refresh_replies(people, _reader(gmail))

    # The mutant files the colleague's reply on Ada. The real guard does not.
    assert result["filed"] == 1
    assert len(_inbound(people, person["person_id"])) == 1


def test_mutation_the_unmutated_run_refuses_the_same_message(tmp_path):
    people, gmail, person = _ambiguous_case(tmp_path)
    result = refresh_replies(people, _reader(gmail))
    assert result["filed"] == 0
    assert result["unassigned"] == 1
    assert _inbound(people, person["person_id"]) == []


def test_mutation_dropping_the_transition_guard_moves_the_person_twice(
    tmp_path, monkeypatch
):
    """Criterion 8: the state receipt is what stops a second transition."""
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    refresh_replies(people, _reader(gmail))
    people.set_sequence(
        person["person_id"],
        "open",
        actor="director",
        rationale_summary="Back to open.",
    )
    people.set_reply_cursor(None)

    monkeypatch.setattr(
        PersonStore,
        "_already_transitioned_for",
        lambda self, person_id, message_id: False,
    )
    refresh_replies(people, _reader(gmail))

    assert len(_transitions(people, person["person_id"], "in_conversation")) == 2


def test_mutation_dropping_the_external_key_duplicates_the_reply(tmp_path, monkeypatch):
    """Criterion 8: the Gmail message id is what stops a second reply event."""
    people = PersonStore(tmp_path)
    gmail = _gmail()
    person = _person(people)
    sent = _sent(people, gmail, person)
    gmail.deliver(
        thread_id=sent["threadId"],
        message_id="in_ada_1",
        sender="ada@analytic.example",
        to=ACCOUNT,
        subject="Re: Thursday?",
        snippet="Thursday works.",
    )
    refresh_replies(people, _reader(gmail))
    people.set_reply_cursor(None)

    original = PersonStore.upsert_external_event
    counter = {"n": 0}

    def unkeyed(self, person_id, *, external_key, **kwargs):
        counter["n"] += 1
        return original(self, person_id, external_key=f"{external_key}:{counter['n']}", **kwargs)

    monkeypatch.setattr(PersonStore, "upsert_external_event", unkeyed)
    refresh_replies(people, _reader(gmail))

    assert len(_inbound(people, person["person_id"])) == 2
