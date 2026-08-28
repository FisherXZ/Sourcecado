"""S3: reviewed enrich, draft, and approved Gmail send for one bound person.

Every test here counts real calls into the fake Gmail service. ``send_attempts``
records every invocation of ``send``, including the ones that raise;
``sends`` records only the messages that actually left. A test that asserted a
status string would keep passing after the guard broke, so nothing below does
that.

Each negative case proves it bites before it proves nothing was sent. The
strongest signal is ``execution_status``: ``failed`` means the approval claim
was granted and the executor really ran, so a zero send count is a guard doing
its job rather than a code path nobody reached. ``not_run`` means the decision
was recorded as a denial. ``cancelled`` and ``expired`` mean the item reached a
terminal state before any claim was possible.
"""

import threading
import time

from fastapi.testclient import TestClient

from coworker.gmail import (
    FakeGmail,
    GmailError,
    SendAuthority,
    body_digest,
    send_reviewed_draft,
)
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-approved-send"
HEADERS = {TOKEN_HEADER: TOKEN}

BODY = "Hi Ada,\n\nWould Thursday work for a short call?\n\nFisher"
SUBJECT = "Thursday?"
ADA_EMAIL = "ada@analytic.example"
ACCOUNT = "director@sourcecado.test"


class FailingSendGmail(FakeGmail):
    """Gmail that accepts the submission and then fails it.

    The attempt is still recorded, so a test can tell "we never reached Gmail"
    apart from "Gmail refused us".
    """

    def __init__(self, failures: int = 1) -> None:
        super().__init__()
        self.failures = failures

    def send(self, *, draft_id: str):
        if self.failures > 0:
            self.failures -= 1
            self.send_attempts.append(draft_id)
            raise GmailError("Gmail rejected the submission.")
        return super().send(draft_id=draft_id)


class SlowSendGmail(FakeGmail):
    """Gmail whose send is slow enough to overlap two approval submissions."""

    def __init__(self, delay: float = 0.25) -> None:
        super().__init__()
        self.delay = delay
        self.entered = threading.Event()

    def send(self, *, draft_id: str):
        self.entered.set()
        time.sleep(self.delay)
        return super().send(draft_id=draft_id)


def _gmail(cls=FakeGmail, **kwargs) -> FakeGmail:
    gmail = cls(**kwargs)
    gmail.account_email = ACCOUNT
    return gmail


def _app(tmp_path, gmail, **kwargs):
    return create_app(
        token=TOKEN, provider=None, state=tmp_path, gmail=gmail, **kwargs
    )


def _bound_person(app, *, email: str | None = ADA_EMAIL, apollo_id="ada"):
    """One person file with a sourcing chat bound to it."""
    people = app.state.people
    person = people.keep_from_apollo(
        apollo_id=apollo_id,
        first_name="Ada",
        last_name_obfuscated="L.",
        title="Head of Data",
        company="Analytic",
    )
    if email:
        people.apply_enrichment(person["person_id"], name="Ada Lovelace", email=email)
    person = people.get(person["person_id"])
    session_id = app.state.store.create_session()["session_id"]
    people.bind_session(
        session_id, person["person_id"], expected_person_version=int(person["version"])
    )
    return person["person_id"], session_id


def _draft(client, person_id, session_id, *, subject=SUBJECT, body=BODY):
    res = client.post(
        f"/v1/people/{person_id}/outreach/draft",
        headers=HEADERS,
        json={"session_id": session_id, "subject": subject, "body": body},
    )
    assert res.status_code == 201, res.text
    return res.json()["draft"]


def _approval(client, person_id, session_id, draft, **overrides):
    payload = {
        "session_id": session_id,
        "draft_id": draft["id"],
        "reviewed_body_digest": draft["body_digest"],
    }
    payload.update(overrides)
    return client.post(
        f"/v1/people/{person_id}/outreach/send-approval",
        headers=HEADERS,
        json=payload,
    )


def _park(client, person_id, session_id, draft, **overrides):
    res = _approval(client, person_id, session_id, draft, **overrides)
    assert res.status_code == 201, res.text
    return res.json()["item"]["id"]


def _decide(client, item_id, decision="allow"):
    return client.post(
        f"/v1/inbox/{item_id}",
        headers=HEADERS,
        json={"decision": decision, "actor": "Fisher", "scope": "once"},
    )


def _live_draft(gmail: FakeGmail, draft_id: str) -> dict:
    return next(item for item in gmail.drafts if item["id"] == draft_id)


def _reached_the_send_gate(app, item_id: str) -> dict:
    """Non-vacuity guard: the approval really is a bound send, still decidable."""
    item = app.state.inbox.get(item_id)
    assert item is not None, "the approval never existed"
    assert item["name"] == "gmail_send"
    assert item["resource"]["kind"] == "gmail_send_authority"
    return item


def _nothing_was_filed(app, person_id: str) -> bool:
    """No send receipt, and the person was not advanced off the shelf."""
    timeline = app.state.people.timeline(person_id)
    return (
        [row for row in timeline if row["kind"] == "send"] == []
        and app.state.people.get(person_id)["sequence_state"] is None
    )


def _sends_when_nothing_is_wrong(tmp_path, subdir: str) -> int:
    """Positive control: the same fixture, untampered, really does send.

    Every negative test calls this. Without it, a zero send count could mean the
    guard worked or could mean the flow never got off the ground.
    """
    gmail = _gmail()
    app = _app(tmp_path / subdir, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)
    res = _decide(client, item_id)
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert len(gmail.send_attempts) == 1
    return len(gmail.sends)


# --------------------------------------------------------------------------
# Criterion 1 — the flow starts from a person-bound chat and never guesses
# --------------------------------------------------------------------------


def test_the_draft_takes_its_recipient_from_the_person_file(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)

    draft = _draft(client, person_id, session_id)

    assert draft["to"] == ADA_EMAIL
    assert gmail.drafts[0]["to"] == ADA_EMAIL
    assert gmail.send_attempts == []


def test_an_unbound_chat_cannot_draft_or_park_a_send(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    other_id, other_session = _bound_person(app, apollo_id="grace", email="g@x.test")

    missing = client.post(
        f"/v1/people/{person_id}/outreach/draft",
        headers=HEADERS,
        json={"subject": SUBJECT, "body": BODY},
    )
    wrong = client.post(
        f"/v1/people/{person_id}/outreach/draft",
        headers=HEADERS,
        json={"session_id": other_session, "subject": SUBJECT, "body": BODY},
    )

    assert missing.status_code == 400
    assert missing.json()["code"] == "unbound_session"
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "unbound_session"
    assert gmail.drafts == []
    assert gmail.send_attempts == []
    # The control: the person's own chat does work.
    assert _draft(client, person_id, session_id)["to"] == ADA_EMAIL
    assert other_id != person_id


def test_a_person_with_no_email_cannot_be_drafted_to(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app, email=None)

    res = client.post(
        f"/v1/people/{person_id}/outreach/draft",
        headers=HEADERS,
        json={"session_id": session_id, "subject": SUBJECT, "body": BODY},
    )

    assert res.status_code == 409
    assert res.json()["code"] == "no_recipient"
    assert gmail.drafts == []
    assert gmail.send_attempts == []


def test_a_draft_addressed_elsewhere_cannot_be_approved_under_this_person(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    # Someone redirects the draft in Gmail after it was written.
    _live_draft(gmail, draft["id"])["to"] = "someone.else@example.com"

    res = _approval(client, person_id, session_id, draft)

    assert res.status_code == 409
    assert res.json()["code"] == "recipient_not_bound"
    assert app.state.inbox.pending() == []
    assert gmail.send_attempts == []


# --------------------------------------------------------------------------
# Criterion 4 — the draft is editable and visibly not sent
# --------------------------------------------------------------------------


def test_creating_a_draft_sends_nothing_and_reports_not_sent(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)

    draft = _draft(client, person_id, session_id)

    assert draft["sent"] is False
    assert gmail.send_attempts == []
    assert gmail.sends == []


def test_an_edit_to_the_draft_is_visible_and_changes_the_reviewed_version(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)

    _live_draft(gmail, draft["id"])["body"] = BODY + "\n\nPS: bringing Charles."
    res = client.get(
        f"/v1/people/{person_id}/outreach/draft/{draft['id']}", headers=HEADERS
    )

    reread = res.json()["draft"]
    assert res.status_code == 200
    assert "PS: bringing Charles." in reread["body"]
    assert reread["body_digest"] != draft["body_digest"]
    assert reread["sent"] is False
    assert gmail.send_attempts == []


# --------------------------------------------------------------------------
# Criteria 5 and 8 — the binding, and what a successful send records
# --------------------------------------------------------------------------


def test_the_send_approval_binds_account_draft_recipient_subject_and_body_version(
    tmp_path,
):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)

    item_id = _park(client, person_id, session_id, draft)

    item = app.state.inbox.get(item_id)
    resource = item["resource"]
    assert resource == {
        "kind": "gmail_send_authority",
        "person_id": person_id,
        "draft_id": draft["id"],
        "account": ACCOUNT,
        "to": ADA_EMAIL,
        "subject": SUBJECT,
        "body_digest": body_digest(BODY),
        "sent": False,
    }
    assert item["session_id"] == session_id
    # The binding names the body version. It never carries the body itself.
    assert "Thursday work for a short call" not in str(item)
    listing = client.get("/v1/inbox", headers=HEADERS).json()
    assert "Thursday work for a short call" not in str(listing)
    assert gmail.send_attempts == []


def test_an_approved_send_delivers_once_and_files_an_inspectable_receipt(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft, run_id="run-ada-1")

    res = _decide(client, item_id)

    body = res.json()
    assert res.status_code == 200
    assert body["ok"] is True
    assert gmail.send_attempts == [draft["id"]]
    assert gmail.sends == [{"draft_id": draft["id"]}]
    assert body["result"]["message_id"] == "msg_1"
    assert body["result"]["thread_id"] == "thread_1"
    assert body["item"]["execution_status"] == "succeeded"

    timeline = app.state.people.timeline(person_id)
    sends = [row for row in timeline if row["kind"] == "send"]
    assert len(sends) == 1
    payload = sends[0]["payload"]
    assert payload["message_id"] == "msg_1"
    assert payload["thread_id"] == "thread_1"
    assert payload["draft_id"] == draft["id"]
    assert payload["to"] == ADA_EMAIL
    assert payload["subject"] == SUBJECT
    assert payload["body_digest"] == draft["body_digest"]
    assert payload["approval_id"] == item_id
    assert payload["sent"] is True
    assert sends[0]["run_id"] == "run-ada-1"
    assert sends[0]["session_id"] == session_id
    assert app.state.people.get(person_id)["sequence_state"] == "open"
    assert body["result"]["advanced_to_open"] is True


def test_a_send_leaves_an_already_placed_person_where_the_director_put_them(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    app.state.people.set_sequence(person_id, "in_conversation", actor="director")
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)

    res = _decide(client, item_id)

    assert res.json()["ok"] is True
    assert gmail.sends == [{"draft_id": draft["id"]}]
    assert res.json()["result"]["advanced_to_open"] is False
    assert app.state.people.get(person_id)["sequence_state"] == "in_conversation"


# --------------------------------------------------------------------------
# Criterion 6 — deny, cancel, expiry, stale draft, failed submission
# --------------------------------------------------------------------------


def test_deny_sends_nothing(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)
    _reached_the_send_gate(app, item_id)

    res = _decide(client, item_id, "deny")

    assert res.status_code == 200
    # The denial was really recorded: this is not a route that 404'd.
    assert res.json()["item"]["decision"] == "deny"
    assert res.json()["item"]["execution_status"] == "not_run"
    assert gmail.send_attempts == []
    assert gmail.sends == []
    assert _nothing_was_filed(app, person_id)
    assert _sends_when_nothing_is_wrong(tmp_path, "control") == 1


def test_cancel_before_the_decision_sends_nothing(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)
    _reached_the_send_gate(app, item_id)

    cancelled = app.state.inbox.cancel(item_id)
    res = _decide(client, item_id)

    assert cancelled["execution_status"] == "cancelled"
    assert res.status_code == 409
    assert app.state.inbox.get(item_id)["execution_status"] == "cancelled"
    assert gmail.send_attempts == []
    assert gmail.sends == []
    assert _nothing_was_filed(app, person_id)
    assert _sends_when_nothing_is_wrong(tmp_path, "control") == 1


def test_an_expired_approval_sends_nothing(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    app.state.store.approval_ttl_seconds = 0.05
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)
    _reached_the_send_gate(app, item_id)
    time.sleep(0.12)

    res = _decide(client, item_id)

    assert res.status_code == 409
    expired = app.state.inbox.get(item_id)
    # Expiry is not a denial: nobody decided, and the record says so.
    assert expired["state"] == "expired"
    assert expired["execution_status"] == "expired"
    assert expired["decision"] is None
    assert gmail.send_attempts == []
    assert gmail.sends == []
    assert _nothing_was_filed(app, person_id)
    assert _sends_when_nothing_is_wrong(tmp_path, "control") == 1


def test_a_body_edited_after_review_sends_nothing(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)
    _reached_the_send_gate(app, item_id)
    _live_draft(gmail, draft["id"])["body"] = BODY + "\n\nPS: wire me $500 first."

    res = _decide(client, item_id)

    body = res.json()
    assert res.status_code == 200
    # execution_status 'failed' proves the claim was granted and the executor
    # ran: the guard stopped this, not an unreached code path.
    assert body["item"]["execution_status"] == "failed"
    assert body["result"]["code"] == "stale_draft"
    assert body["ok"] is False
    assert gmail.send_attempts == []
    assert gmail.sends == []
    assert _nothing_was_filed(app, person_id)
    assert _sends_when_nothing_is_wrong(tmp_path, "control") == 1


def test_a_recipient_changed_after_review_sends_nothing(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)
    _live_draft(gmail, draft["id"])["to"] = "attacker@example.com"

    res = _decide(client, item_id)

    assert res.json()["item"]["execution_status"] == "failed"
    assert res.json()["result"]["code"] == "recipient_mismatch"
    assert gmail.send_attempts == []
    assert gmail.sends == []
    assert _nothing_was_filed(app, person_id)
    assert _sends_when_nothing_is_wrong(tmp_path, "control") == 1


def test_a_subject_changed_after_review_sends_nothing(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)
    _live_draft(gmail, draft["id"])["subject"] = "Invoice attached"

    res = _decide(client, item_id)

    assert res.json()["item"]["execution_status"] == "failed"
    assert res.json()["result"]["code"] == "subject_mismatch"
    assert gmail.send_attempts == []
    assert gmail.sends == []
    assert _nothing_was_filed(app, person_id)
    assert _sends_when_nothing_is_wrong(tmp_path, "control") == 1


def test_a_changed_gmail_account_sends_nothing(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)
    gmail.account_email = "someone.else@sourcecado.test"

    res = _decide(client, item_id)

    assert res.json()["item"]["execution_status"] == "failed"
    assert res.json()["result"]["code"] == "account_mismatch"
    assert gmail.send_attempts == []
    assert gmail.sends == []
    assert _nothing_was_filed(app, person_id)
    assert _sends_when_nothing_is_wrong(tmp_path, "control") == 1


def test_a_failed_submission_sends_nothing_and_a_retry_does_not_resend(tmp_path):
    gmail = _gmail(FailingSendGmail, failures=5)
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)

    first = _decide(client, item_id)
    retry = _decide(client, item_id)

    # It really reached Gmail: one submission, refused there.
    assert gmail.send_attempts == [draft["id"]]
    assert gmail.sends == []
    assert first.json()["ok"] is False
    assert first.json()["result"]["code"] == "gmail_failed"
    assert first.json()["item"]["execution_status"] == "failed"
    # The retry hits the terminal guard instead of submitting again.
    assert retry.status_code == 200
    assert retry.json()["idempotent"] is True
    assert retry.json()["result"] == first.json()["result"]
    assert gmail.send_attempts == [draft["id"]]
    assert _nothing_was_filed(app, person_id)


def test_a_send_that_fails_leaves_the_person_unadvanced(tmp_path):
    gmail = _gmail(FailingSendGmail, failures=5)
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)

    _decide(client, item_id)

    assert _nothing_was_filed(app, person_id)
    assert gmail.sends == []


# --------------------------------------------------------------------------
# Criterion 7 — duplicate submission, retry, reconnect
# --------------------------------------------------------------------------


def test_a_duplicate_allow_submitted_again_sends_once(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)

    first = _decide(client, item_id)
    second = _decide(client, item_id)
    third = _decide(client, item_id)

    assert gmail.send_attempts == [draft["id"]]
    assert gmail.sends == [{"draft_id": draft["id"]}]
    assert first.json()["ok"] is True
    assert "idempotent" not in first.json()
    for repeat in (second, third):
        assert repeat.status_code == 200
        assert repeat.json()["idempotent"] is True
        assert repeat.json()["result"] == first.json()["result"]
    assert len([r for r in app.state.people.timeline(person_id) if r["kind"] == "send"]) == 1


def test_two_concurrent_allow_submissions_send_once(tmp_path):
    gmail = _gmail(SlowSendGmail, delay=0.3)
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)

    start = threading.Barrier(2)
    responses: list = []
    lock = threading.Lock()

    def submit():
        start.wait(timeout=5)
        response = _decide(client, item_id)
        with lock:
            responses.append(response)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    # Both submissions really were in flight against a send that had begun.
    assert gmail.entered.is_set()
    assert gmail.send_attempts == [draft["id"]]
    assert gmail.sends == [{"draft_id": draft["id"]}]
    assert [r.status_code for r in responses] == [200, 200]
    winners = [r for r in responses if "idempotent" not in r.json()]
    losers = [r for r in responses if r.json().get("idempotent") is True]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0].json()["result"] == winners[0].json()["result"]
    assert len([r for r in app.state.people.timeline(person_id) if r["kind"] == "send"]) == 1


def test_a_reconnect_and_resubmit_during_the_send_does_not_repeat_it(tmp_path):
    """The transport drops mid-send; the window comes back and asks again."""
    gmail = _gmail(SlowSendGmail, delay=0.4)
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)

    in_flight: dict = {}

    def submit():
        in_flight["response"] = _decide(client, item_id)

    sender = threading.Thread(target=submit)
    sender.start()
    assert gmail.entered.wait(timeout=5), "the send never started"

    # The window reconnects while the send is still running and resubmits.
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]):
        pass
    resubmit = _decide(client, item_id)
    sender.join(timeout=15)

    assert not sender.is_alive()
    assert gmail.send_attempts == [draft["id"]]
    assert gmail.sends == [{"draft_id": draft["id"]}]
    assert in_flight["response"].json()["ok"] is True
    # The resubmission either waited out the live claim and saw the same
    # outcome, or was told the work is still in flight. Never a second send.
    if resubmit.status_code == 202:
        assert resubmit.json()["pending"] is True
    else:
        assert resubmit.status_code == 200
        assert resubmit.json()["idempotent"] is True
        assert resubmit.json()["result"] == in_flight["response"].json()["result"]


def test_two_approvals_for_one_draft_send_it_only_once(tmp_path):
    """Two authorities, one draft. The per-approval claim cannot see this."""
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    first_id = _park(client, person_id, session_id, draft)
    second_id = _park(client, person_id, session_id, draft)

    assert first_id != second_id
    first = _decide(client, first_id)
    second = _decide(client, second_id)

    assert first.json()["ok"] is True
    assert gmail.sends == [{"draft_id": draft["id"]}]
    # The second approval was claimed and executed. It stopped at the draft.
    assert second.json()["item"]["execution_status"] == "failed"
    assert second.json()["ok"] is False
    assert second.json()["result"]["code"] == "draft_unavailable"
    assert gmail.send_attempts == [draft["id"]]
    assert len([r for r in app.state.people.timeline(person_id) if r["kind"] == "send"]) == 1


def test_a_sent_draft_can_no_longer_be_read_back_as_a_draft(tmp_path):
    """Gmail deletes a draft on send. Nothing can bind to it afterwards."""
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    before = client.get(
        f"/v1/people/{person_id}/outreach/draft/{draft['id']}", headers=HEADERS
    )
    _decide(client, _park(client, person_id, session_id, draft))

    after = client.get(
        f"/v1/people/{person_id}/outreach/draft/{draft['id']}", headers=HEADERS
    )

    assert before.status_code == 200
    assert before.json()["draft"]["sent"] is False
    assert after.status_code == 404
    assert after.json()["code"] == "draft_unavailable"
    assert gmail.send_attempts == [draft["id"]]
    assert gmail.sends == [{"draft_id": draft["id"]}]


def test_an_approval_cannot_be_parked_for_an_already_sent_draft(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    _decide(client, _park(client, person_id, session_id, draft))

    res = _approval(client, person_id, session_id, draft)

    assert res.status_code == 409
    assert res.json()["code"] == "draft_unavailable"
    assert gmail.send_attempts == [draft["id"]]
    assert gmail.sends == [{"draft_id": draft["id"]}]


def test_a_send_whose_receipt_cannot_be_filed_is_still_reported_as_sent(tmp_path):
    """The message is gone. Saying otherwise would be a lie the operator acts on."""
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    draft = _draft(client, person_id, session_id)
    item_id = _park(client, person_id, session_id, draft)
    # The person file goes away between approval and send.
    app.state.people.delete(
        person_id,
        expected_version=int(app.state.people.get(person_id)["version"]),
        actor="director",
        rationale_summary="Removed mid-flight",
    )

    res = _decide(client, item_id)

    assert gmail.sends == [{"draft_id": draft["id"]}]
    assert res.json()["ok"] is True
    assert res.json()["result"]["message_id"] == "msg_1"
    assert res.json()["result"]["person_event_id"] is None
    assert "unknown person" in res.json()["result"]["receipt_error"]


def test_gmail_send_is_never_replayed_by_a_provider_retry(tmp_path):
    from coworker.permissions import ASK, AUTO, RETRY_SAFE
    from coworker.turn import _SAFE_RETRY_TOOLS

    assert "gmail_send" in ASK
    assert "gmail_send" not in AUTO
    assert "gmail_send" not in RETRY_SAFE
    assert "gmail_send" not in _SAFE_RETRY_TOOLS
    assert "apollo_enrich_contact" not in _SAFE_RETRY_TOOLS


def test_the_send_authority_refuses_a_draft_it_already_consumed():
    """Below the approval layer: the client itself will not send twice."""
    gmail = _gmail()
    gmail.create_draft(to=ADA_EMAIL, subject=SUBJECT, body=BODY)
    authority = SendAuthority(
        approval_id="ap-1",
        person_id="per_1",
        draft_id="draft_1",
        account=ACCOUNT,
        to=ADA_EMAIL,
        subject=SUBJECT,
        body_digest=body_digest(BODY),
    )

    sent = send_reviewed_draft(gmail, authority)
    try:
        send_reviewed_draft(gmail, authority)
        repeated = True
    except GmailError:
        repeated = False

    assert sent["message_id"] == "msg_1"
    assert repeated is False
    assert gmail.send_attempts == ["draft_1"]
    assert gmail.sends == [{"draft_id": "draft_1"}]
