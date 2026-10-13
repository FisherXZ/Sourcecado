"""Tracked Gmail activity: exact ownership, durable replay, and honest freshness."""

import pytest

from coworker.crm_mail import classify_outbound
from coworker.crm_mail_repository import MailRepository
from coworker.people import PersonStore
from coworker.reply_filing import build_index
from coworker.run_evidence import Evidence

ACCOUNT = "director@sourcecado.test"
ADDRESS = "ada@example.com"


def tracked_person(people, *, apollo_id="ada", address=ADDRESS, thread="thread-ada"):
    person = people.keep_from_apollo(apollo_id=apollo_id, first_name=apollo_id,
        last_name_obfuscated=None, title="Founder", company="Example")
    people.apply_enrichment(person["person_id"], email=address)
    people.record_approved_send(person["person_id"], message_id=f"original-{apollo_id}",
        thread_id=thread, draft_id=f"draft-{apollo_id}", to=address, subject="Hello",
        body_digest="reviewed", account=ACCOUNT, approval_id=f"approval-{apollo_id}")
    return person["person_id"]


def outgoing(**changes):
    return {"id": "gmail-response", "thread_id": "thread-ada", "from": ACCOUNT,
        "to": ADDRESS, "subject": "Re: Hello", "label_ids": ["SENT"], **changes}


def test_outgoing_requires_exact_account_thread_and_recipient(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    verdict = classify_outbound(outgoing(), build_index(people), account=ACCOUNT)
    assert verdict.evidence is Evidence.PRESENT
    assert verdict.person_id == pid


@pytest.mark.parametrize("changes,reason", [
    ({"from": "someone-else@example.com"}, "sender_is_not_connected_account"),
    ({"to": "ada+events@example.com"}, "plus_addressed_variant"),
    ({"to": "other@example.com"}, "recipient_is_not_the_person"),
    ({"cc": "colleague@example.com"}, "thread_has_several_recipients"),
    ({"subject": "Re: Fwd: Hello"}, "forwarded_thread"),
])
def test_uncertain_outgoing_is_a_review_reason(tmp_path, changes, reason):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    verdict = classify_outbound(outgoing(**changes), build_index(people), account=ACCOUNT)
    assert verdict.evidence is Evidence.AMBIGUOUS
    assert verdict.person_id is None
    assert verdict.reason == reason
    assert verdict.candidates == (pid,)


def test_shared_address_and_multi_person_thread_are_never_guessed(tmp_path):
    people = PersonStore(tmp_path)
    tracked_person(people)
    second = tracked_person(people, apollo_id="second", thread="second-thread")
    verdict = classify_outbound(outgoing(), build_index(people), account=ACCOUNT)
    assert verdict.reason == "shared_address"
    people.record_approved_send(second,
        message_id="second-on-thread", thread_id="thread-ada", draft_id="d", to=ADDRESS,
        subject="Hello", body_digest="reviewed", account=ACCOUNT, approval_id="a")
    assert classify_outbound(outgoing(), build_index(people), account=ACCOUNT).reason == "thread_serves_several_people"


@pytest.mark.parametrize("changes", [
    {"thread_id": "untracked"}, {"label_ids": ["DRAFT"]}, {"label_ids": ["SENT", "TRASH"]},
])
def test_untracked_mail_drafts_and_trash_are_skipped(tmp_path, changes):
    people = PersonStore(tmp_path)
    tracked_person(people)
    assert classify_outbound(outgoing(**changes), build_index(people), account=ACCOUNT).evidence is Evidence.ABSENT


def test_evidence_and_pending_analysis_survive_restart_without_duplicates(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    mail = MailRepository(people)
    message = outgoing(received_at="2026-10-12T12:00:00+00:00", snippet="Sent in Gmail")
    assert mail.save_message(pid, message, direction="outbound", account=ACCOUNT,
        content={"body": "A real response", "body_state": "present"}) is True
    assert mail.save_message(pid, message, direction="outbound", account=ACCOUNT,
        content={"body": "A real response", "body_state": "present"}) is False
    people._conn.close()
    restarted = MailRepository(PersonStore(tmp_path))
    assert restarted.message(ACCOUNT, "gmail-response")["body"] == "A real response"
    assert restarted.analysis_state(pid) == {"latest_evidence_revision": 1, "analyzed_revision": 0, "status": "pending"}


def test_failed_check_preserves_success_and_separate_checkpoints(tmp_path):
    people = PersonStore(tmp_path)
    mail = MailRepository(people)
    people.set_reply_cursor("100")
    mail.finish_check(status="ok", account=ACCOUNT, outbound_after=None, checked_at="2026-10-12T12:00:00+00:00")
    mail.finish_check(status="partial", account=ACCOUNT, outbound_after="thread-a", checked_at="2026-10-12T12:01:00+00:00")
    mail.finish_check(status="failed", account=ACCOUNT, warning="gmail_unavailable", checked_at="2026-10-12T12:02:00+00:00")
    people._conn.close()
    restarted = MailRepository(PersonStore(tmp_path))
    state = restarted.sync_state()
    assert state["last_success_at"] == "2026-10-12T12:00:00+00:00"
    assert state["status"] == "failed"
    assert state["warning"] == "gmail_unavailable"
    assert state["outbound_after"] == "thread-a"
    assert restarted.people.reply_cursor() == "100"
