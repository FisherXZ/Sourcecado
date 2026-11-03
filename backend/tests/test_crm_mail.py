"""Tracked Gmail activity: exact ownership, durable replay, and honest freshness."""

import pytest

from coworker.crm_mail import TrackedMailReader, classify_outbound, refresh_mail
from coworker.crm_mail_repository import MailRepository
from coworker.gmail import FakeGmail, GmailError
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
    analysis = restarted.analysis_state(pid)
    assert {key: analysis[key] for key in ("latest_evidence_revision", "analyzed_revision", "status")} == {"latest_evidence_revision": 1, "analyzed_revision": 0, "status": "pending"}
    assert analysis["attempts"] == 0
    assert analysis["next_attempt_at"] is None


def test_gmail_preview_entities_are_readable_untrusted_text(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    gmail = mailbox()
    gmail.deliver(thread_id="thread-ada", message_id="encoded-preview", sender=ADDRESS,
        to=ACCOUNT, subject="Re: Hello", snippet="Sourcecado&#39;s &amp; &lt;script&gt;text&lt;/script&gt;", body="Plain body")
    result = refresh_mail(people, TrackedMailReader(gmail))
    assert result["filed"] == 1
    event = next(event for event in people.timeline(pid) if event["payload"].get("message_id") == "encoded-preview")
    assert event["payload"]["snippet"] == "Sourcecado's & <script>text</script>"
    assert event["payload"]["untrusted"] is True


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


class ReadOnlyGmail(FakeGmail):
    def create_draft(self, **kwargs):
        raise AssertionError("collection reached drafting")

    def send(self, **kwargs):
        raise AssertionError("collection reached sending")

    def get_draft(self, **kwargs):
        raise AssertionError("collection reached a draft")


def mailbox():
    gmail = ReadOnlyGmail()
    gmail.account_email = ACCOUNT
    return gmail


def deliver(gmail, *, mid="incoming", sender=ADDRESS, to=ACCOUNT, labels=("INBOX",),
    thread="thread-ada", body="Please send project examples."):
    return gmail.deliver(thread_id=thread, message_id=mid, sender=sender, to=to,
        subject="Re: Hello", snippet=body[:20], body=body, label_ids=labels)


def test_collects_gmail_reply_once_and_preserves_done_tasks_and_approval(tmp_path):
    from coworker.crm_tasks import TaskService
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    gmail = mailbox()
    deliver(gmail)
    first = refresh_mail(people, TrackedMailReader(gmail))
    assert first["filed"] == 1
    people.set_sequence(pid, "done", actor="director")
    tasks = TaskService(people)
    task = tasks.create(pid, title="Unrelated promise", operation_id="manual-task",
        timezone="America/Los_Angeles", actor="director")
    deliver(gmail, mid="external-reply", sender=ACCOUNT, to=ADDRESS, labels=("SENT",))
    result = refresh_mail(people, TrackedMailReader(gmail))
    assert result["outgoing"] == 1
    assert gmail.thread_reads == ["thread-ada", "thread-ada"]
    assert MailRepository(people).message(ACCOUNT, "external-reply")["body_state"] == "present"
    assert people.get(pid)["sequence_state"] == "done"
    assert people.mail_state(pid)["follow_up"] == {"needed": False, "reason": None}
    assert next(e for e in people.timeline(pid) if e["kind"] == "send")["payload"]["approval_id"] == "approval-ada"
    assert people._conn.execute("SELECT state FROM person_tasks WHERE task_id = ?", (task["task"]["task_id"],)).fetchone()[0] == "open"
    people._conn.close()
    restarted = PersonStore(tmp_path)
    replay = refresh_mail(restarted, TrackedMailReader(gmail))
    assert replay["filed"] == replay["outgoing"] == 0
    assert len([e for e in restarted.timeline(pid) if e["kind"] == "mail"]) == 2


def test_ambiguity_reads_no_body_and_matching_later_resolves_only_that_gap(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    gmail = mailbox()
    uncertain = deliver(gmail, sender="colleague@example.com", body="Private colleague text")
    refresh_mail(people, TrackedMailReader(gmail))
    assert gmail.body_reads == []
    assert not MailRepository(people).message(ACCOUNT, "incoming")
    assert "Private colleague" not in str(people.get(pid, expand_sources=True))
    people.record_reply_gap(pid, message_id="other-gap", thread_id="thread-ada",
        reason="sender_unreadable", question="Who sent it?", candidate_count=1, received_at=None)
    uncertain["payload"]["headers"][0]["value"] = ADDRESS
    result = refresh_mail(people, TrackedMailReader(gmail))
    assert result["filed"] == 1
    gaps = people.get(pid, expand_sources=True)["knowledge_gaps"]
    assert next(g for g in gaps if g["fields"].get("message_id") == "incoming")["fields"]["resolved"] is True
    assert not next(g for g in gaps if g["fields"].get("message_id") == "other-gap")["fields"].get("resolved")


def test_outgoing_on_one_thread_does_not_clear_another_thread_reminder(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    people.record_approved_send(pid, message_id="second-send", thread_id="thread-two",
        draft_id="d2", to=ADDRESS, subject="Hello", body_digest="reviewed", account=ACCOUNT, approval_id="a2")
    gmail = mailbox()
    deliver(gmail, mid="reply-one")
    deliver(gmail, mid="reply-two", thread="thread-two")
    deliver(gmail, mid="answer-one", sender=ACCOUNT, to=ADDRESS, labels=("SENT",))
    refresh_mail(people, TrackedMailReader(gmail))
    assert people.mail_state(pid)["last_contact_direction"] == "outbound"
    assert people.mail_state(pid)["follow_up"]["reason"] == "reply_unanswered"


def test_batched_resync_keeps_the_original_boundary_across_restart(tmp_path):
    people = PersonStore(tmp_path)
    tracked_person(people)
    tracked_person(people, apollo_id="second", address="second@example.com", thread="thread-z")
    gmail = mailbox()
    boundary = gmail.profile_history_id()
    first = refresh_mail(people, TrackedMailReader(gmail), max_threads=1)
    assert first["status"] == "partial"
    assert people.reply_cursor() is None
    deliver(gmail, mid="arrived-between-batches")
    people._conn.close()
    restarted = PersonStore(tmp_path)
    resumed = refresh_mail(restarted, TrackedMailReader(gmail), max_threads=1)
    assert resumed["status"] == "ok"
    assert restarted.reply_cursor() == boundary
    assert refresh_mail(restarted, TrackedMailReader(gmail))["filed"] == 1


def test_failed_pass_keeps_both_boundaries_and_known_records(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    gmail = mailbox()
    refresh_mail(people, TrackedMailReader(gmail))
    prior = MailRepository(people).sync_state()
    cursor = people.reply_cursor()
    deliver(gmail)
    original = gmail.thread
    gmail.thread = lambda **kwargs: (_ for _ in ()).throw(GmailError("private token-like provider detail"))
    failed = refresh_mail(people, TrackedMailReader(gmail))
    assert failed["status"] == "failed"
    assert people.reply_cursor() == cursor
    assert MailRepository(people).sync_state()["last_success_at"] == prior["last_success_at"]
    assert len([e for e in people.timeline(pid) if e["kind"] == "mail"]) == 1
    assert "private token-like" not in str(failed)
    gmail.thread = original
    assert refresh_mail(people, TrackedMailReader(gmail))["filed"] == 0


def test_expired_history_rereads_only_tracked_threads(tmp_path):
    people = PersonStore(tmp_path)
    tracked_person(people)
    gmail = mailbox()
    refresh_mail(people, TrackedMailReader(gmail))
    gmail.history_floor = gmail.history_id + 1
    deliver(gmail, labels=())  # An archived reply is still conversation evidence.
    deliver(gmail, mid="unrelated", thread="untracked")
    result = refresh_mail(people, TrackedMailReader(gmail))
    assert result["mode"] == "cursor_reset"
    assert result["filed"] == 1
    assert set(gmail.thread_reads) == {"thread-ada"}
    assert gmail.body_reads == ["incoming"]


def test_explicit_reply_to_older_message_leaves_newer_message_unanswered(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    gmail = mailbox()
    older = deliver(gmail, mid="older")
    older["payload"]["headers"].append({"name": "Message-ID", "value": "<older@example.com>"})
    newer = deliver(gmail, mid="newer")
    newer["payload"]["headers"].append({"name": "Message-ID", "value": "<newer@example.com>"})
    answer = deliver(gmail, mid="answer", sender=ACCOUNT, to=ADDRESS, labels=("SENT",))
    answer["payload"]["headers"].append({"name": "In-Reply-To", "value": "<older@example.com>"})
    refresh_mail(people, TrackedMailReader(gmail))
    assert people.mail_state(pid)["unanswered_message_ids"] == ["newer"]
    refresh_mail(people, TrackedMailReader(gmail))
    assert people.mail_state(pid)["unanswered_message_ids"] == ["newer"]


def test_message_time_wins_over_filing_order(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    mail = MailRepository(people)
    mail.save_message(pid, outgoing(id="new-send", received_at="2027-01-02T12:00:00Z"), direction="outbound",
        account=ACCOUNT, content={"body": "Yes", "body_state": "present"})
    mail.save_message(pid, outgoing(id="old-reply", received_at="2027-01-01T12:00:00Z", sender=ADDRESS), direction="inbound",
        account=ACCOUNT, content={"body": "Question", "body_state": "present"})
    assert people.mail_state(pid)["last_contact_direction"] == "outbound"
    assert people.mail_state(pid)["follow_up"]["needed"] is False


def test_missing_outgoing_time_does_not_clear_known_incoming_reminder(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    people._conn.execute("UPDATE events SET created_at = '2025-01-01 12:00:00' WHERE kind = 'send'")
    people._conn.commit()
    mail = MailRepository(people)
    mail.save_message(pid, outgoing(id="known-reply", received_at="2026-01-01T12:00:00Z"), direction="inbound",
        account=ACCOUNT, content={"body": "Question", "body_state": "present"})
    mail.save_message(pid, outgoing(id="unknown-time"), direction="outbound",
        account=ACCOUNT, content={"body": "Answer", "body_state": "present"})
    assert people.mail_state(pid)["follow_up"]["needed"] is True


def test_missing_body_is_visible_and_recovery_changes_the_evidence_revision(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    gmail = mailbox()
    deliver(gmail)
    original = gmail.read
    gmail.read = lambda **kwargs: (_ for _ in ()).throw(GmailError("body unavailable"))
    result = refresh_mail(people, TrackedMailReader(gmail))
    assert result["status"] == "ok"  # Metadata collected; analysis evidence is incomplete.
    assert result["mail_sync"]["warning"] == "body_unavailable"
    assert MailRepository(people).message(ACCOUNT, "incoming")["body_state"] == "missing"
    revision = MailRepository(people).analysis_state(pid)["latest_evidence_revision"]
    gmail.read = original
    refresh_mail(people, TrackedMailReader(gmail))
    assert MailRepository(people).analysis_state(pid)["latest_evidence_revision"] == revision + 1
    assert MailRepository(people).message(ACCOUNT, "incoming")["body_state"] == "present"


def test_body_budget_and_history_pages_are_bounded_and_replay_safe(tmp_path):
    people = PersonStore(tmp_path)
    tracked_person(people)
    gmail = mailbox()
    refresh_mail(people, TrackedMailReader(gmail))
    for i in range(5):
        deliver(gmail, mid=f"incoming-{i}")
    gmail.history_page_size = 1
    result = refresh_mail(people, TrackedMailReader(gmail), max_history_pages=1, max_body_reads=1)
    assert result["status"] == "partial"
    assert len(gmail.history_calls) == len(gmail.body_reads) == 1
    assert MailRepository(people).sync_state()["inbound_page_token"]
    for _ in range(4):
        refresh_mail(people, TrackedMailReader(gmail), max_history_pages=1)
    assert MailRepository(people).sync_state()["status"] == "ok"
    assert people._conn.execute("SELECT count(*) FROM gmail_messages").fetchone()[0] == 5


def test_read_only_surface_and_source_cannot_reach_connector_writes():
    import inspect
    from coworker import crm_mail
    from tests.test_reply_filing import FORBIDDEN_NAMES, _referenced_names
    surface = {name for name in dir(TrackedMailReader) if not name.startswith("_") and callable(getattr(TrackedMailReader, name))}
    assert surface == {"account", "read", "thread", "inbound_message", "history", "profile_history_id"}
    assert not _referenced_names(inspect.getsource(crm_mail)) & FORBIDDEN_NAMES


def test_later_approved_receipt_preserves_collected_mail_and_attaches_authority(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    mail = MailRepository(people)
    mail.save_message(pid, outgoing(id="settled-send", received_at="2026-10-12T12:00:00Z"),
        direction="outbound", account=ACCOUNT, content={"body": "Reviewed response", "body_state": "present"})
    people.record_approved_send(pid, message_id="settled-send", thread_id="thread-ada", draft_id="reviewed-draft",
        to=ADDRESS, subject="Re: Hello", body_digest="reviewed", account=ACCOUNT, approval_id="settled-approval")
    events = [event for event in people.timeline(pid) if event["payload"].get("message_id") == "settled-send"]
    assert len(events) == 1
    assert events[0]["kind"] == "send"
    assert events[0]["payload"]["approval_id"] == "settled-approval"
    assert events[0]["payload"]["source_ref"]["message_id"] == "settled-send"


def test_expired_history_restarts_partial_outgoing_pass_and_keeps_recovery_boundary(tmp_path):
    people = PersonStore(tmp_path)
    tracked_person(people)
    tracked_person(people, apollo_id="second", address="second@example.com", thread="thread-z")
    gmail = mailbox()
    refresh_mail(people, TrackedMailReader(gmail))
    assert refresh_mail(people, TrackedMailReader(gmail), max_threads=1)["status"] == "partial"
    assert MailRepository(people).sync_state()["outbound_after"] == "thread-ada"
    gmail.history_floor = gmail.history_id + 1
    deliver(gmail, mid="archived-before-expiry", labels=())
    boundary = gmail.profile_history_id()
    recovering = refresh_mail(people, TrackedMailReader(gmail), max_threads=1)
    assert recovering["mode"] == "cursor_reset"
    assert recovering["status"] == "partial"
    assert MailRepository(people).message(ACCOUNT, "archived-before-expiry")
    deliver(gmail, mid="between-recovery-batches")
    people._conn.close()
    restarted = PersonStore(tmp_path)
    assert refresh_mail(restarted, TrackedMailReader(gmail), max_threads=1)["status"] == "ok"
    assert restarted.reply_cursor() == boundary
    assert refresh_mail(restarted, TrackedMailReader(gmail))["filed"] == 1


def test_changed_ambiguity_updates_one_warning_without_stalling_later_mail(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    gmail = mailbox()
    deliver(gmail, sender="colleague@example.com")
    refresh_mail(people, TrackedMailReader(gmail))
    tracked_person(people, apollo_id="second", address="second@example.com", thread="thread-ada")
    refresh = refresh_mail(people, TrackedMailReader(gmail))
    assert refresh["status"] == "ok"
    gaps = people.get(pid, expand_sources=True)["knowledge_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["fields"]["reason"] == "thread_serves_several_people"
    assert gaps[0]["fields"]["candidate_count"] == 2
    version = people.get(pid)["version"]
    assert refresh_mail(people, TrackedMailReader(gmail))["status"] == "ok"
    assert people.get(pid)["version"] == version


def test_matching_resolves_every_candidate_and_later_ambiguity_reopens_same_gap(tmp_path):
    people = PersonStore(tmp_path)
    winner = tracked_person(people)
    other = tracked_person(people, apollo_id="second", address="second@example.com", thread="thread-z")
    gmail = mailbox()
    message = deliver(gmail, to=f"{ACCOUNT}, second@example.com")
    assert refresh_mail(people, TrackedMailReader(gmail))["status"] == "ok"
    assert all(people.mail_state(pid)["follow_up"]["reason"] == "reply_needs_review" for pid in (winner, other))
    next(h for h in message["payload"]["headers"] if h["name"] == "To")["value"] = ACCOUNT
    assert refresh_mail(people, TrackedMailReader(gmail))["filed"] == 1
    assert not people.mail_state(other)["follow_up"]["needed"]
    for pid in (winner, other):
        gap = people.get(pid, expand_sources=True)["knowledge_gaps"][0]
        assert gap["fields"]["resolved"] is True
    next(h for h in message["payload"]["headers"] if h["name"] == "To")["value"] = f"{ACCOUNT}, second@example.com"
    assert refresh_mail(people, TrackedMailReader(gmail))["status"] == "ok"
    for pid in (winner, other):
        gaps = people.get(pid, expand_sources=True)["knowledge_gaps"]
        assert len(gaps) == 1
        assert not gaps[0]["fields"].get("resolved")


def test_body_with_changed_bcc_is_not_saved_under_attributed_metadata(tmp_path):
    people = PersonStore(tmp_path)
    tracked_person(people)
    gmail = mailbox()
    deliver(gmail, sender=ACCOUNT, to=ADDRESS, labels=("SENT",), body="Sensitive body")
    original = gmail.read
    gmail.read = lambda **kwargs: {**original(**kwargs), "bcc": "other@example.com"}
    assert refresh_mail(people, TrackedMailReader(gmail))["status"] == "ok"
    saved = MailRepository(people).message(ACCOUNT, "incoming")
    assert saved["body"] == ""
    assert saved["body_state"] == "missing"
