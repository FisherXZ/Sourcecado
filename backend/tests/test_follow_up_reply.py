"""#211: prepare, edit, review and send a follow-up on the intended Gmail thread.

Like the approved-send suite, every test counts real calls into the fake Gmail
service: ``send_attempts`` records every call into ``send``, ``sends`` only
the messages that left. A negative test first proves the flow reached the gate
it is testing, then proves nothing was sent.
"""

import base64
import email

from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp
from coworker.connectors.google_oauth import save_google
from coworker.gmail import (
    DRAFTS_URL,
    THREADS_URL,
    FakeGmail,
    GmailApi,
    GmailError,
    ReplyTarget,
    reply_subject,
)
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-follow-up"
HEADERS = {TOKEN_HEADER: TOKEN}
ACCOUNT = "director@sourcecado.test"
ADA_EMAIL = "ada@analytic.example"
SUBJECT = "Thursday?"
REPLY_BODY = "Hi Ada,\n\nFollowing up on the deck you asked for. It is attached.\n\nFisher"


class TripwireHttp(FakeHttp):
    """Apollo must never be reached: preparing a draft does not enrich."""

    def post(self, url, **kwargs):
        raise AssertionError(f"follow-up preparation reached {url}")

    def get(self, url, **kwargs):
        raise AssertionError(f"follow-up preparation reached {url}")


def _gmail(cls=FakeGmail) -> FakeGmail:
    gmail = cls()
    gmail.account_email = ACCOUNT
    return gmail


def _app(tmp_path, gmail):
    return create_app(
        token=TOKEN,
        provider=None,
        state=tmp_path,
        gmail=gmail,
        http=TripwireHttp(),
        apollo_key="unused",
    )


def _person(app, *, email: str | None = ADA_EMAIL):
    people = app.state.people
    person = people.keep_from_apollo(
        apollo_id="ada",
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


def _conversation(app, gmail: FakeGmail, person_id: str) -> str:
    """Our sent outreach, then Ada's verified reply, on one Gmail thread."""
    draft = gmail.create_draft(to=ADA_EMAIL, subject=SUBJECT, body="Hi Ada")
    sent = gmail.send(draft_id=draft["id"])
    thread_id = sent["threadId"]
    app.state.people.record_approved_send(
        person_id,
        message_id=sent["id"],
        thread_id=thread_id,
        draft_id=draft["id"],
        to=ADA_EMAIL,
        subject=SUBJECT,
        body_digest="digest",
        account=ACCOUNT,
        approval_id="appr_first",
    )
    gmail.deliver(
        thread_id=thread_id,
        message_id="in_ada_1",
        sender=f"Ada <{ADA_EMAIL}>",
        to=ACCOUNT,
        subject=f"Re: {SUBJECT}",
        references=f"<{sent['id']}@mail.example.test>",
    )
    app.state.people.file_inbound_reply(
        person_id,
        message_id="in_ada_1",
        thread_id=thread_id,
        sender=ADA_EMAIL,
        subject=f"Re: {SUBJECT}",
        snippet="Could you send the deck?",
        received_at=None,
    )
    # Forget the setup's draft and attempt. `sends` is kept: the fake numbers
    # message ids from it, and the follow-up needs an id of its own.
    gmail.drafts.clear()
    gmail.send_attempts.clear()
    return thread_id


def _task(client, person_id, title="Send Ada the deck"):
    res = client.post(
        f"/v1/people/{person_id}/tasks",
        headers=HEADERS,
        json={"title": title, "details": "She asked on Thursday.", "operation_id": "op-create-deck-task"},
    )
    assert res.status_code == 201, res.text
    return res.json()["task"]


def _prepare(client, person_id, task_id, **params):
    return client.get(
        f"/v1/people/{person_id}/tasks/{task_id}/follow-up",
        headers=HEADERS,
        params=params,
    )


def _reply_draft(client, person_id, session_id, *, thread_id, task_id, body=REPLY_BODY):
    return client.post(
        f"/v1/people/{person_id}/outreach/draft",
        headers=HEADERS,
        json={
            "session_id": session_id,
            "thread_id": thread_id,
            "task_id": task_id,
            "body": body,
        },
    )


def _edit(client, person_id, session_id, draft, body):
    return client.put(
        f"/v1/people/{person_id}/outreach/draft/{draft['id']}",
        headers=HEADERS,
        json={
            "session_id": session_id,
            "body": body,
            "expected_body_digest": draft["body_digest"],
        },
    )


def _park(client, person_id, session_id, draft, *, thread_id, task_id):
    return client.post(
        f"/v1/people/{person_id}/outreach/send-approval",
        headers=HEADERS,
        json={
            "session_id": session_id,
            "draft_id": draft["id"],
            "reviewed_body_digest": draft["body_digest"],
            "expected_thread_id": thread_id,
            "task_id": task_id,
        },
    )


def _decide(client, item_id, decision="allow"):
    return client.post(
        f"/v1/inbox/{item_id}",
        headers=HEADERS,
        json={"decision": decision, "actor": "Fisher", "scope": "once"},
    )


def _task_state(client, person_id, task_id) -> str:
    res = client.get("/v1/crm/tasks", headers=HEADERS, params={"person_id": person_id, "state": "all"})
    return next(row for row in res.json()["tasks"] if row["task_id"] == task_id)["state"]


def _flow(tmp_path, gmail=None):
    gmail = gmail or _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _person(app)
    thread_id = _conversation(app, gmail, person_id)
    task = _task(client, person_id)
    return gmail, app, client, person_id, session_id, thread_id, task


# --------------------------------------------------------------------------
# Preparation
# --------------------------------------------------------------------------


def test_prepare_names_the_task_person_conversation_account_and_sends_nothing(tmp_path):
    gmail, app, client, person_id, _session, thread_id, task = _flow(tmp_path)

    res = _prepare(client, person_id, task["task_id"])

    assert res.status_code == 200, res.text
    prepared = res.json()["follow_up"]
    assert prepared["task"]["task_id"] == task["task_id"]
    assert prepared["person"]["person_id"] == person_id
    assert prepared["recipient"] == ADA_EMAIL
    assert prepared["account"] == ACCOUNT
    assert prepared["thread"]["thread_id"] == thread_id
    assert prepared["thread"]["last_direction"] == "inbound"
    assert prepared["subject"] == f"Re: {SUBJECT}"
    assert prepared["body"].startswith("Hi Ada,")
    assert prepared["talking_points"][0] == {"origin": "task", "text": task["title"]}
    assert prepared["blockers"] == []
    assert task["title"] in prepared["assistant_prompt"]
    assert thread_id in prepared["assistant_prompt"]
    # Preparing is read-only: no draft, no send, the task is still open.
    assert gmail.drafts == [] and gmail.send_attempts == []
    assert _task_state(client, person_id, task["task_id"]) == "open"


def test_prepare_without_an_email_still_writes_and_never_enriches(tmp_path):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    person_id, session_id = _person(app, email=None)
    task = _task(client, person_id)

    res = _prepare(client, person_id, task["task_id"])

    assert res.status_code == 200, res.text
    prepared = res.json()["follow_up"]
    assert prepared["recipient"] is None
    assert prepared["thread"] is None
    assert [row["code"] for row in prepared["blockers"]] == ["no_recipient"]
    assert "Do not look it up or enrich it" in prepared["assistant_prompt"]
    # Drafting in Gmail waits for an address; the TripwireHttp proves Apollo
    # was never called on the way.
    draft = client.post(
        f"/v1/people/{person_id}/outreach/draft",
        headers=HEADERS,
        json={"session_id": session_id, "subject": "Deck", "body": REPLY_BODY},
    )
    assert draft.status_code == 409
    assert draft.json()["code"] == "no_recipient"
    assert gmail.drafts == [] and gmail.send_attempts == []
    assert app.state.people.get(person_id)["email"] is None


def test_prepare_refuses_a_thread_that_is_not_this_persons(tmp_path):
    gmail, app, client, person_id, _session, _thread, task = _flow(tmp_path)

    res = _prepare(client, person_id, task["task_id"], thread_id="thread_someone_else")

    assert res.status_code == 404
    assert res.json()["code"] == "thread_not_on_file"


def test_prepare_refuses_a_finished_task_and_an_unknown_one(tmp_path):
    gmail, app, client, person_id, _session, _thread, task = _flow(tmp_path)
    done = client.patch(
        f"/v1/people/{person_id}/tasks/{task['task_id']}",
        headers=HEADERS,
        json={
            "changes": {"state": "completed"},
            "expected_version": task["version"],
            "operation_id": "op-complete-deck",
        },
    )
    assert done.status_code == 200, done.text

    assert _prepare(client, person_id, task["task_id"]).json()["code"] == "task_not_open"
    assert _prepare(client, person_id, "task_" + "0" * 32).status_code == 404


# --------------------------------------------------------------------------
# Create, edit, review and send on the intended thread
# --------------------------------------------------------------------------


def test_a_reply_is_drafted_edited_approved_and_sent_on_the_same_thread(tmp_path):
    gmail, app, client, person_id, session_id, thread_id, task = _flow(tmp_path)

    created = _reply_draft(
        client, person_id, session_id, thread_id=thread_id, task_id=task["task_id"]
    )
    assert created.status_code == 201, created.text
    draft = created.json()["draft"]
    assert draft["thread_id"] == thread_id
    assert draft["subject"] == f"Re: {SUBJECT}"
    assert draft["to"] == ADA_EMAIL
    live = gmail.drafts[0]
    # Gmail threads a reply by thread id plus the answered Message-ID.
    assert live["thread_id"] == thread_id
    assert live["in_reply_to"] == "<in_ada_1@mail.example.test>"
    assert live["references"].endswith("<in_ada_1@mail.example.test>")

    edited_body = REPLY_BODY.replace("It is attached.", "It is attached, with notes.")
    edited = _edit(client, person_id, session_id, draft, edited_body)
    assert edited.status_code == 200, edited.text
    reviewed = edited.json()["draft"]
    assert reviewed["body"] == edited_body
    assert reviewed["body_digest"] != draft["body_digest"]
    assert reviewed["thread_id"] == thread_id
    assert reviewed["subject"] == f"Re: {SUBJECT}"

    parked = _park(
        client, person_id, session_id, reviewed, thread_id=thread_id, task_id=task["task_id"]
    )
    assert parked.status_code == 201, parked.text
    item = parked.json()["item"]
    assert item["resource"]["thread_id"] == thread_id
    assert item["resource"]["task_id"] == task["task_id"]
    assert gmail.send_attempts == []

    res = _decide(client, item["id"])
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert gmail.send_attempts == [draft["id"]]
    assert res.json()["result"]["thread_id"] == thread_id

    message_id = res.json()["result"]["message_id"]
    receipt = next(
        row
        for row in app.state.people.timeline(person_id)
        if row["kind"] == "send" and row["payload"].get("message_id") == message_id
    )
    assert receipt["payload"]["thread_id"] == thread_id
    assert receipt["payload"]["task_id"] == task["task_id"]
    # Sending is not completing: the task stays open for the director.
    assert _task_state(client, person_id, task["task_id"]) == "open"


def test_an_edit_against_a_version_changed_in_gmail_is_refused_with_the_current_text(tmp_path):
    gmail, app, client, person_id, session_id, thread_id, task = _flow(tmp_path)
    draft = _reply_draft(
        client, person_id, session_id, thread_id=thread_id, task_id=task["task_id"]
    ).json()["draft"]
    gmail.drafts[0]["body"] = "Edited in Gmail."

    res = _edit(client, person_id, session_id, draft, "My edit in Sourcecado.")

    assert res.status_code == 409
    assert res.json()["code"] == "stale_draft"
    assert res.json()["draft"]["body"] == "Edited in Gmail."
    assert gmail.drafts[0]["body"] == "Edited in Gmail."


def test_a_message_edited_after_approval_sends_nothing(tmp_path):
    gmail, app, client, person_id, session_id, thread_id, task = _flow(tmp_path)
    draft = _reply_draft(
        client, person_id, session_id, thread_id=thread_id, task_id=task["task_id"]
    ).json()["draft"]
    item = _park(
        client, person_id, session_id, draft, thread_id=thread_id, task_id=task["task_id"]
    ).json()["item"]

    assert _edit(client, person_id, session_id, draft, "Changed after review.").status_code == 200
    res = _decide(client, item["id"])

    assert res.status_code == 200, res.text
    assert res.json()["ok"] is False
    assert res.json()["result"]["code"] == "stale_draft"
    assert gmail.send_attempts == []


def test_a_draft_moved_to_another_conversation_after_approval_sends_nothing(tmp_path):
    gmail, app, client, person_id, session_id, thread_id, task = _flow(tmp_path)
    draft = _reply_draft(
        client, person_id, session_id, thread_id=thread_id, task_id=task["task_id"]
    ).json()["draft"]
    item = _park(
        client, person_id, session_id, draft, thread_id=thread_id, task_id=task["task_id"]
    ).json()["item"]

    gmail.drafts[0]["thread_id"] = "thread_elsewhere"
    res = _decide(client, item["id"])

    assert res.json()["ok"] is False
    assert res.json()["result"]["code"] == "thread_mismatch"
    assert gmail.send_attempts == []


def test_a_recipient_changed_after_approval_sends_nothing(tmp_path):
    gmail, app, client, person_id, session_id, thread_id, task = _flow(tmp_path)
    draft = _reply_draft(
        client, person_id, session_id, thread_id=thread_id, task_id=task["task_id"]
    ).json()["draft"]
    item = _park(
        client, person_id, session_id, draft, thread_id=thread_id, task_id=task["task_id"]
    ).json()["item"]

    gmail.drafts[0]["to"] = "someone@else.example"
    res = _decide(client, item["id"])

    assert res.json()["ok"] is False
    assert res.json()["result"]["code"] == "recipient_mismatch"
    assert gmail.send_attempts == []


def test_a_denied_follow_up_sends_nothing_and_leaves_the_task_open(tmp_path):
    gmail, app, client, person_id, session_id, thread_id, task = _flow(tmp_path)
    draft = _reply_draft(
        client, person_id, session_id, thread_id=thread_id, task_id=task["task_id"]
    ).json()["draft"]
    item = _park(
        client, person_id, session_id, draft, thread_id=thread_id, task_id=task["task_id"]
    ).json()["item"]

    res = _decide(client, item["id"], "deny")

    assert res.status_code == 200, res.text
    assert gmail.send_attempts == []
    assert _task_state(client, person_id, task["task_id"]) == "open"


# --------------------------------------------------------------------------
# The fallback: never start a different conversation in silence
# --------------------------------------------------------------------------


class ThreadlessGmail(FakeGmail):
    """A connector that accepts a reply but files it as a new conversation."""

    def create_draft(self, *, to, subject, body, reply=None):
        return super().create_draft(to=to, subject=subject, body=body)


class UnreadableThreadGmail(FakeGmail):
    def reply_target(self, *, thread_id):
        raise GmailError("Gmail returned 500.")


def test_a_draft_gmail_did_not_thread_is_reported_and_cannot_be_sent_as_a_reply(tmp_path):
    gmail, app, client, person_id, session_id, thread_id, task = _flow(
        tmp_path, _gmail(ThreadlessGmail)
    )

    res = _reply_draft(
        client, person_id, session_id, thread_id=thread_id, task_id=task["task_id"]
    )

    assert res.status_code == 409
    assert res.json()["code"] == "thread_not_preserved"
    stray = res.json()["draft"]
    assert stray["thread_id"] != thread_id
    # Even if someone tries, the reviewed-thread binding refuses it.
    parked = _park(
        client, person_id, session_id, stray, thread_id=thread_id, task_id=task["task_id"]
    )
    assert parked.status_code == 409
    assert parked.json()["code"] == "thread_mismatch"
    assert gmail.send_attempts == []


def test_an_unreadable_conversation_creates_no_draft(tmp_path):
    gmail, app, client, person_id, session_id, thread_id, task = _flow(
        tmp_path, _gmail(UnreadableThreadGmail)
    )

    res = _reply_draft(
        client, person_id, session_id, thread_id=thread_id, task_id=task["task_id"]
    )

    assert res.status_code == 502
    assert res.json()["code"] == "thread_unavailable"
    assert gmail.drafts == [] and gmail.send_attempts == []


def test_a_reply_cannot_be_drafted_into_a_thread_not_filed_on_the_person(tmp_path):
    gmail, app, client, person_id, session_id, _thread, task = _flow(tmp_path)

    res = _reply_draft(
        client, person_id, session_id, thread_id="thread_unknown", task_id=task["task_id"]
    )

    assert res.status_code == 409
    assert res.json()["code"] == "thread_not_on_file"
    assert gmail.drafts == []


def test_an_unbound_chat_cannot_draft_or_edit_a_follow_up(tmp_path):
    gmail, app, client, person_id, session_id, thread_id, task = _flow(tmp_path)
    draft = _reply_draft(
        client, person_id, session_id, thread_id=thread_id, task_id=task["task_id"]
    ).json()["draft"]
    stranger = app.state.store.create_session()["session_id"]

    res = _edit(client, person_id, stranger, draft, "Hijacked")

    assert res.status_code == 409
    assert res.json()["code"] == "unbound_session"
    assert gmail.drafts[0]["body"] == REPLY_BODY


# --------------------------------------------------------------------------
# The live connector puts the thread on the wire
# --------------------------------------------------------------------------


def _live(tmp_path, routes):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "r", "access_token": "a", "email": ACCOUNT})
    http = FakeHttp(routes)
    return GmailApi(secrets, http=http, client_id="cid", client_secret="sec"), http


def _decoded(raw: str) -> email.message.Message:
    return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def test_the_live_client_sends_thread_id_and_reply_headers(tmp_path):
    api, http = _live(
        tmp_path,
        {
            DRAFTS_URL: {"id": "d1", "message": {"threadId": "t1"}},
            f"{DRAFTS_URL}/d1": {"id": "d1", "message": {"threadId": "t1"}},
        },
    )
    reply = ReplyTarget(
        thread_id="t1",
        subject="Re: Thursday?",
        in_reply_to="<m2@x>",
        references="<m1@x> <m2@x>",
    )

    created = api.create_draft(to=ADA_EMAIL, subject=reply.subject, body="Hi", reply=reply)
    api.update_draft(draft_id="d1", to=ADA_EMAIL, subject=reply.subject, body="Hi again", reply=reply)

    assert created["thread_id"] == "t1"
    post, put = http.calls
    assert post["method"] == "POST" and put["method"] == "PUT"
    for call in (post, put):
        message = call["json"]["message"]
        assert message["threadId"] == "t1"
        parsed = _decoded(message["raw"])
        assert parsed["In-Reply-To"] == "<m2@x>"
        assert parsed["References"] == "<m1@x> <m2@x>"
        assert parsed["Subject"] == "Re: Thursday?"
    assert put["url"] == f"{DRAFTS_URL}/d1"


def test_the_live_reply_target_answers_the_newest_message(tmp_path):
    api, http = _live(
        tmp_path,
        {
            f"{THREADS_URL}/t1": {
                "id": "t1",
                "messages": [
                    {"payload": {"headers": [
                        {"name": "Subject", "value": "Thursday?"},
                        {"name": "Message-ID", "value": "<m1@x>"},
                    ]}},
                    {"payload": {"headers": [
                        {"name": "Subject", "value": "Re: Thursday?"},
                        {"name": "Message-ID", "value": "<m2@x>"},
                        {"name": "References", "value": "<m1@x>"},
                    ]}},
                ],
            }
        },
    )

    target = api.reply_target(thread_id="t1")

    assert target == ReplyTarget(
        thread_id="t1",
        subject="Re: Thursday?",
        in_reply_to="<m2@x>",
        references="<m1@x> <m2@x>",
    )
    # Metadata only: a reply never reads earlier message bodies.
    assert http.calls[0]["params"]["format"] == "metadata"


def test_reply_subject_keeps_an_existing_prefix():
    assert reply_subject("Thursday?") == "Re: Thursday?"
    assert reply_subject("RE: Thursday?") == "RE: Thursday?"
    assert reply_subject("") == "Re: (no subject)"
