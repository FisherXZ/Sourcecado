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
    assert saved["mail_analysis"]["status"] == "retrying"
    assert saved["mail_analysis"]["analyzed_revision"] == 0
    assert saved["mail_analysis"]["latest_evidence_revision"] > 0
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


def test_manual_and_background_api_refresh_share_collection_without_model_wait(tmp_path):
    import asyncio
    import threading
    import httpx
    app = create_app(token="mail-api-test", state=tmp_path, provider=None, gmail=mailbox())
    calls = []
    async def scenario():
        started, analyzing, finish_analysis = asyncio.Event(), asyncio.Event(), asyncio.Event()
        finish_collection = threading.Event()
        loop = asyncio.get_running_loop()
        def collect():
            calls.append("collection")
            loop.call_soon_threadsafe(started.set)
            assert finish_collection.wait(5)
            return {"status": "ok", "filed": 2}
        class SlowAnalysis:
            async def run_pending(self):
                analyzing.set()
                await finish_analysis.wait()
        app.state.crm_refresh.collect = collect
        app.state.crm_refresh.analysis = SlowAnalysis()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                manual = asyncio.create_task(client.post("/v1/replies/refresh", headers=HEADERS))
                await asyncio.wait_for(started.wait(), timeout=2)
                background = await client.post("/v1/crm/refresh", headers=HEADERS)
                assert background.status_code == 202
                finish_collection.set()
                response = await asyncio.wait_for(manual, timeout=2)
                assert response.json()["refresh"]["filed"] == 2
                await asyncio.wait_for(analyzing.wait(), timeout=2)
                assert not app.state.crm_refresh._current.done()
        finally:
            finish_collection.set()
            finish_analysis.set()
            if app.state.crm_refresh._current:
                await app.state.crm_refresh._current
    asyncio.run(scenario())
    assert calls == ["collection"]


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
