"""S1: a run's events reach whoever is attached, not whoever started it."""

import asyncio

from coworker.provider import StreamChunk
from coworker.server import create_app

from tests.ws_driver import WSDriver

TOKEN = "test-token-reconnect"


class GatedProvider:
    """Streams two deltas, holds mid-stream until released, then finishes."""

    model_id = "fake"

    def __init__(self):
        self.gate = asyncio.Event()

    async def astream(self, *, messages, tools=None):
        yield StreamChunk(text_delta="one ")
        yield StreamChunk(text_delta="two ")
        await self.gate.wait()
        yield StreamChunk(text_delta="three")


def _deltas(outbox):
    return "".join(
        e.get("delta", "") for e in outbox if e.get("type") == "assistant_delta"
    )


def test_mid_run_reconnect_receives_remaining_deltas_and_terminal(tmp_path):
    """Socket A dies mid-run; socket B must inherit the live stream."""

    async def scenario():
        provider = GatedProvider()
        app = create_app(token=TOKEN, provider=provider, state=tmp_path)
        store = app.state.store
        sid = store.open_session_id()

        a = WSDriver(app, TOKEN)
        await a.start()
        await a.send_json({"type": "chat", "session_id": sid, "text": "hi"})
        assert await a.wait_for(lambda out: "two" in _deltas(out))
        await a.disconnect()  # window reload mid-stream
        # The dead socket must be deregistered before B attaches.
        for _ in range(100):
            if not app.state.live_event_senders:
                break
            await asyncio.sleep(0.01)
        assert not app.state.live_event_senders

        b = WSDriver(app, TOKEN)
        await b.start()
        # Reconnect replay announces the live run with its ORIGINAL turn_start.
        replayed = await b.wait_for(
            lambda out: next(
                (e for e in out if e.get("type") == "turn_start"), None
            )
        )
        original = next(
            e for e in store.load_events(sid) if e["type"] == "turn_start"
        )
        assert replayed == original

        provider.gate.set()
        end = await b.wait_for(
            lambda out: next(
                (e for e in out if e.get("type") == "turn_end"), None
            )
        )
        # The reconnected client hears the rest of the stream and the outcome.
        assert end["state"] == "complete"
        assert end["text"] == "one two three"
        assert "three" in _deltas(b.outbox)
        await b.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_reconnect_after_the_run_ended_replays_the_original_terminal(tmp_path):
    """A client reconnecting after the end must still learn the outcome."""

    async def scenario():
        provider = GatedProvider()
        app = create_app(token=TOKEN, provider=provider, state=tmp_path)
        store = app.state.store
        sid = store.open_session_id()

        a = WSDriver(app, TOKEN)
        await a.start()
        await a.send_json({"type": "chat", "session_id": sid, "text": "hi"})
        assert await a.wait_for(lambda out: "two" in _deltas(out))
        await a.disconnect()
        provider.gate.set()  # run finishes with nobody attached
        for _ in range(200):
            if any(
                e["type"] == "turn_end" for e in store.load_events(sid)
            ):
                break
            await asyncio.sleep(0.01)
        original_end = next(
            e for e in store.load_events(sid) if e["type"] == "turn_end"
        )

        b = WSDriver(app, TOKEN)
        await b.start()
        replayed = await b.wait_for(
            lambda out: next(
                (e for e in out if e.get("type") == "turn_end"), None
            )
        )
        # The ORIGINAL terminal envelope — same event_id, clients dedupe.
        assert replayed == original_end
        await b.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_two_live_windows_both_receive_the_stream(tmp_path):
    """Events broadcast: the second window is not deaf to a run it did not start."""

    async def scenario():
        provider = GatedProvider()
        app = create_app(token=TOKEN, provider=provider, state=tmp_path)
        sid = app.state.store.open_session_id()

        a = WSDriver(app, TOKEN)
        await a.start()
        b = WSDriver(app, TOKEN)
        await b.start()
        await a.send_json({"type": "chat", "session_id": sid, "text": "hi"})
        assert await a.wait_for(lambda out: "two" in _deltas(out))
        provider.gate.set()
        end_a = await a.wait_for(
            lambda out: next(
                (e for e in out if e.get("type") == "turn_end"), None
            )
        )
        end_b = await b.wait_for(
            lambda out: next(
                (e for e in out if e.get("type") == "turn_end"), None
            )
        )
        assert end_a["text"] == "one two three"
        assert end_b == end_a
        assert _deltas(b.outbox) == "one two three"
        await a.disconnect()
        await b.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), 30))
