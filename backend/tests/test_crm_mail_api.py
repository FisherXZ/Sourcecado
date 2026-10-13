from fastapi.testclient import TestClient

from coworker.gmail import MissingGmail
from coworker.server import TOKEN_HEADER, create_app
from tests.test_crm_mail import ACCOUNT, deliver, mailbox, tracked_person

HEADERS = {TOKEN_HEADER: "mail-api-test"}


def test_refresh_and_saved_reads_share_activity_and_freshness_after_restart(tmp_path):
    gmail = mailbox()
    app = create_app(token="mail-api-test", state=tmp_path, provider=None, gmail=gmail)
    pid = tracked_person(app.state.people)
    deliver(gmail)
    deliver(gmail, mid="outside-response", sender=ACCOUNT, to="ada@example.com", labels=("SENT",))
    client = TestClient(app)
    assert client.post("/v1/replies/refresh").status_code == 401
    refreshed = client.post("/v1/replies/refresh", headers=HEADERS).json()
    assert refreshed["refresh"]["outgoing"] == 1
    saved = client.get(f"/v1/people/{pid}", headers=HEADERS).json()
    assert saved["mail_sync"]["status"] == "ok"
    assert saved["mail_analysis"]["status"] == "pending"
    assert all("body" not in event["payload"] for event in saved["timeline"])
    mails = [event for event in saved["timeline"] if event["kind"] == "mail"]
    assert {event["payload"]["direction"] for event in mails} == {"inbound", "outbound"}
    assert all(event["payload"]["source_ref"]["url"].startswith("https://mail.google.com/") for event in mails)
    success = saved["mail_sync"]["last_success_at"]
    app.state.gmail = MissingGmail()
    assert client.post("/v1/replies/refresh", headers=HEADERS).json()["refresh"]["status"] == "failed"
    app.state.people._conn.close()
    app.state.store._conn.close()
    restarted = TestClient(create_app(token="mail-api-test", state=tmp_path, provider=None, gmail=MissingGmail()))
    state = restarted.get("/v1/board?include_mail_state=true", headers=HEADERS).json()["mail_sync"]
    assert state["status"] == "failed"
    assert state["last_success_at"] == success
    assert len(restarted.get(f"/v1/people/{pid}", headers=HEADERS).json()["timeline"]) == len(saved["timeline"])


def test_resolved_warning_disappears_from_the_brief_as_well_as_mail_attention(tmp_path):
    gmail = mailbox()
    app = create_app(token="mail-api-test", state=tmp_path, provider=None, gmail=gmail)
    pid = tracked_person(app.state.people)
    message = deliver(gmail, sender="colleague@example.com")
    client = TestClient(app)
    client.post("/v1/replies/refresh", headers=HEADERS)
    before = client.get(f"/v1/people/{pid}", headers=HEADERS).json()
    question = before["person"]["knowledge_gaps"][0]["fields"]["question"]
    assert any(gap["text"] == question for gap in before["brief"]["gaps"])
    message["payload"]["headers"][0]["value"] = "ada@example.com"
    client.post("/v1/replies/refresh", headers=HEADERS)
    after = client.get(f"/v1/people/{pid}", headers=HEADERS).json()
    assert not any(gap["text"] == question for gap in after["brief"]["gaps"])
