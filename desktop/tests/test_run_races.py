"""S2/S3: second-actor races — one run per session, one retry per command."""

import asyncio
import threading

from coworker.events import TurnIdentity, build_event
from coworker.provider import StreamChunk, ToolCall
from coworker.server import create_app
from coworker.turn import RunControl, RunCoordinator, new_turn_identity
from coworker import turn as turn_mod

from tests.ws_driver import WSDriver

TOKEN = "test-token-races"


class CountingProvider:
    """Slow-streaming provider that records concurrent stream overlap."""

    model_id = "fake"

    def __init__(self, delay=0.05):
        self.delay = delay
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def astream(self, *, messages, tools=None):
        self.calls.append(list(messages))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            for delta in ("A", "B"):
                await asyncio.sleep(self.delay)
                yield StreamChunk(text_delta=delta)
        finally:
            self.active -= 1


def test_coordinator_refuses_a_second_live_run_per_session():
    coordinator = RunCoordinator()
    first = RunControl(new_turn_identity("thread-x"))
    second = RunControl(new_turn_identity("thread-x"))
    assert coordinator.register(first) is True
    assert coordinator.register(second) is False
    first.abandon()
    assert coordinator.register(second) is True
    # Superseded terminal controls are evicted: the registry stays bounded.
    third = RunControl(new_turn_identity("thread-x"))
    second.abandon()
    assert coordinator.register(third) is True
    assert coordinator.latest_per_session() == [third]
    assert coordinator.get("thread-x", first.identity.run_id) is None


def test_chat_during_the_drain_window_never_runs_two_turns_at_once(tmp_path):
    """A chat frame landing in the claim->launch window must not fork the
    session: exactly one run at a time, queue order preserved, no message
    destroyed, nothing stranded in 'sending'."""

    async def scenario():
        provider = CountingProvider()
        app = create_app(token=TOKEN, provider=provider, state=tmp_path)
        store = app.state.store
        sid = "race"
        store.create_session(sid)
        # Widen the drain's snapshot send so the race window is deterministic.
        ws = WSDriver(
            app, TOKEN, slow_send_types={"queue_snapshot"}, slow_send_delay=0.25
        )
        await ws.start()

        await ws.send_json({"type": "chat", "session_id": sid, "text": "first"})
        for _ in range(200):
            await asyncio.sleep(0.01)
            if app.state.run_coordinator.active_for(sid) is not None:
                break
        await ws.send_json(
            {"type": "chat", "session_id": sid, "text": "queued-second"}
        )
        # Wait for run one's terminal drain snapshot, then race a chat frame
        # into the window while that snapshot send is still in flight.
        assert await ws.wait_for(
            lambda out: any(
                e.get("type") == "queue_snapshot"
                and str(e.get("command_id", "")).startswith("terminal:")
                for e in out
            ),
            timeout=10,
        )
        await ws.send_json(
            {"type": "chat", "session_id": sid, "text": "typed-third"}
        )

        # Everything settles: queue empty, no run active.
        for _ in range(600):
            await asyncio.sleep(0.01)
            if (
                app.state.run_coordinator.active_for(sid) is None
                and store.list_queue(sid) == []
            ):
                break

        messages = store.load(sid)
        users = [m["content"] for m in messages if m.get("role") == "user"]
        assistants = [m for m in messages if m.get("role") == "assistant"]
        assert provider.max_active == 1  # never two concurrent turns
        assert users == ["first", "queued-second", "typed-third"]
        assert len(assistants) == 3  # no replace_all destroyed an answer
        assert store.list_queue(sid) == []  # nothing stranded in 'sending'
        await ws.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), 60))


def _seed_failed_step(store, sid, tool):
    ident = TurnIdentity(
        session_id=sid, run_id="run1", message_id="m1", part_id="p1"
    )
    store.append_event(
        sid,
        build_event(
            ident,
            "tool_started",
            event_id="e1",
            id="call1",
            name=tool,
            arguments={"query": "x"},
            started_at="t",
        ),
    )
    failure = turn_mod._tool_failure(
        ToolCall(id="call1", name=tool, arguments={}),
        {"error": "request timed out"},
        ident,
    )
    store.append_event(
        sid,
        build_event(
            ident,
            "tool_finished",
            event_id="e2",
            id="call1",
            name=tool,
            ok=False,
            result={"error": "request timed out"},
            finished_at="t",
            failure=failure,
        ),
    )


def test_duplicate_retry_command_executes_the_step_exactly_once(
    tmp_path, monkeypatch
):
    """S3: the same retry command from two sockets runs the tool once and
    records one recovery outcome."""
    calls = []
    release = threading.Event()

    def blocked_execute(name, arguments, **kwargs):
        calls.append(name)
        assert release.wait(timeout=5)
        return True, {"ok": True}

    monkeypatch.setattr("coworker.turn.execute", blocked_execute)
    monkeypatch.setattr("coworker.server.execute", blocked_execute)

    async def scenario():
        app = create_app(token=TOKEN, provider=None, state=tmp_path)
        store = app.state.store
        sid = "thread1"
        store.create_session(sid)
        _seed_failed_step(store, sid, "gmail_search")

        a = WSDriver(app, TOKEN)
        b = WSDriver(app, TOKEN)
        await a.start()
        await b.start()
        command = {
            "type": "retry_failed_step",
            "session_id": sid,
            "run_id": "run1",
            "call_id": "call1",
            "command_id": "cmd-DUP",
        }
        await a.send_json(dict(command))
        await b.send_json(dict(command))
        await asyncio.sleep(0.3)  # both frames read while the tool blocks
        release.set()
        assert await a.wait_for(
            lambda out: any(e.get("type") == "tool_recovery" for e in out),
            timeout=10,
        )
        await asyncio.sleep(0.2)

        events = store.load_events(sid)
        recoveries = [
            e
            for e in events
            if e["type"] == "tool_recovery" and e["command_id"] == "cmd-DUP"
        ]
        finished = [
            e
            for e in events
            if e["type"] == "tool_finished" and e.get("recovery_command_id")
        ]
        assert calls == ["gmail_search"]  # the step ran exactly once
        assert [e["status"] for e in recoveries] == ["succeeded"]
        assert len(finished) == 1
        await a.disconnect()
        await b.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), 30))
    release.set()


def test_duplicate_unsafe_retry_parks_exactly_one_approval(tmp_path):
    """S3: one retry click on an unsafe tool must never park two independent
    send-authorizations."""

    async def scenario():
        app = create_app(token=TOKEN, provider=None, state=tmp_path)
        store = app.state.store
        sid = "thread1"
        store.create_session(sid)
        _seed_failed_step(store, sid, "gmail_send")

        a = WSDriver(app, TOKEN)
        b = WSDriver(app, TOKEN)
        await a.start()
        await b.start()
        command = {
            "type": "retry_failed_step",
            "session_id": sid,
            "run_id": "run1",
            "call_id": "call1",
            "command_id": "cmd-DUP",
        }
        await a.send_json(dict(command))
        await b.send_json(dict(command))
        await asyncio.sleep(0.5)

        events = store.load_events(sid)
        permissions = [
            e for e in events if e["type"] == "permission_required"
        ]
        recoveries = [
            e
            for e in events
            if e["type"] == "tool_recovery" and e["command_id"] == "cmd-DUP"
        ]
        assert len(permissions) == 1  # one approval card, not two
        assert [e["status"] for e in recoveries] == ["approval_required"]
        assert len(app.state.inbox.pending()) == 1
        await a.disconnect()
        await b.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_stale_cancel_does_not_pause_the_queue(tmp_path):
    """m1: cancelling a run this process does not track must not silently
    pause the thread's drain."""

    async def scenario():
        app = create_app(token=TOKEN, provider=None, state=tmp_path)
        store = app.state.store
        sid = "thread-cancel"
        store.create_session(sid)
        ws = WSDriver(app, TOKEN)
        await ws.start()
        await ws.send_json(
            {"type": "cancel", "session_id": sid, "run_id": "run_gone"}
        )
        frame = await ws.wait_for(
            lambda out: next(
                (e for e in out if e.get("type") == "error"), None
            )
        )
        assert frame is not None, "stale cancel got silence"
        assert store.queue_paused(sid) is False
        await ws.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), 30))
