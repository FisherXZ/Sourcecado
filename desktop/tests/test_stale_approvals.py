"""S5: acting on a stale approval gets a defined, honest answer — never silence."""

import asyncio
import time

from fastapi.testclient import TestClient

from coworker.server import TOKEN_HEADER, create_app

from tests.ws_driver import WSDriver

TOKEN = "test-token-stale"


def _park_stale(app, sid, ttl):
    app.state.store.approval_ttl_seconds = ttl
    app.state.inbox.park(
        "gmail_send",
        {"draft_id": "d1"},
        item_id="call-stale",
        reason="consequential",
        session_id=sid,
        run_id="r1",
        message_id="m1",
        part_id="p1",
    )


def test_ws_allow_on_an_expired_approval_returns_the_expiry_receipt(tmp_path):
    async def scenario():
        app = create_app(token=TOKEN, provider=None, state=tmp_path)
        sid = "thread1"
        app.state.store.create_session(sid)
        _park_stale(app, sid, ttl=0.05)
        ws = WSDriver(app, TOKEN)
        await ws.start()
        await asyncio.sleep(0.15)  # let it expire

        await ws.send_json(
            {"type": "permission", "id": "call-stale", "decision": "allow"}
        )
        receipt = await ws.wait_for(
            lambda out: next(
                (e for e in out if e.get("type") == "approval_resolved"), None
            )
        )
        assert receipt is not None, "operator got silence for a stale approval"
        assert receipt["resolution"] == "expired"
        assert receipt["decision"] is None
        assert receipt["execution_status"] == "expired"
        durable = [
            e
            for e in app.state.store.load_events(sid)
            if e["type"] == "approval_resolved" and e["id"] == "call-stale"
        ]
        assert len(durable) == 1

        # A second click replays the same receipt — same event_id, no new row.
        before = len(ws.outbox)
        await ws.send_json(
            {"type": "permission", "id": "call-stale", "decision": "deny"}
        )
        replayed = await ws.wait_for(
            lambda out: next(
                (
                    e
                    for e in out[before:]
                    if e.get("type") == "approval_resolved"
                ),
                None,
            )
        )
        assert replayed["event_id"] == receipt["event_id"]
        assert len(
            [
                e
                for e in app.state.store.load_events(sid)
                if e["type"] == "approval_resolved"
            ]
        ) == 1
        await ws.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_ws_decision_on_an_unknown_approval_returns_an_error_frame(tmp_path):
    async def scenario():
        app = create_app(token=TOKEN, provider=None, state=tmp_path)
        ws = WSDriver(app, TOKEN)
        await ws.start()
        await ws.send_json(
            {"type": "permission", "id": "no-such-item", "decision": "allow"}
        )
        frame = await ws.wait_for(
            lambda out: next(
                (e for e in out if e.get("type") == "error"), None
            )
        )
        assert frame is not None, "operator got silence for an unknown approval"
        assert "approval" in frame["message"]
        await ws.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_session_restore_persists_the_expiry_receipt(tmp_path):
    """A thread restored before anyone polls the Inbox must not show a
    live-looking card for an already-expired approval."""
    app = create_app(token=TOKEN, provider=None, state=tmp_path)
    client = TestClient(app)
    sid = "thread1"
    app.state.store.create_session(sid)
    _park_stale(app, sid, ttl=0.05)
    time.sleep(0.15)

    response = client.get(f"/v1/sessions/{sid}", headers={TOKEN_HEADER: TOKEN})
    assert response.status_code == 200
    receipts = [
        e
        for e in response.json()["events"]
        if e["type"] == "approval_resolved" and e["id"] == "call-stale"
    ]
    assert [r["resolution"] for r in receipts] == ["expired"]
    # Idempotent across restores: still exactly one durable receipt.
    client.get(f"/v1/sessions/{sid}", headers={TOKEN_HEADER: TOKEN})
    durable = [
        e
        for e in app.state.store.load_events(sid)
        if e["type"] == "approval_resolved"
    ]
    assert len(durable) == 1
