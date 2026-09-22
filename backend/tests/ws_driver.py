"""Raw ASGI websocket driver: deterministic multi-socket tests on one loop.

Modeled on the QA harness so reconnect and duplicate-command interleavings
can be driven without portals, threads, or blocking receives.
"""

import asyncio
import json


class WSDriver:
    def __init__(self, app, token, slow_send_types=(), slow_send_delay=0.0):
        self.app = app
        self.token = token
        self.inbox = asyncio.Queue()  # frames to deliver to the server
        self.outbox = []  # everything the server sent
        self.slow_send_types = set(slow_send_types)
        self.slow_send_delay = slow_send_delay
        self._connected = asyncio.Event()
        self._closed = False
        self.task = None

    def scope(self):
        return {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": "/ws/chat",
            "raw_path": b"/ws/chat",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"127.0.0.1:8765"),
                (b"sec-websocket-protocol", f"club, {self.token}".encode()),
            ],
            "client": ("127.0.0.1", 55555),
            "server": ("127.0.0.1", 8765),
            "subprotocols": ["club", self.token],
            "state": {},
        }

    async def _receive(self):
        return await self.inbox.get()

    async def _send(self, message):
        if message["type"] == "websocket.accept":
            self._connected.set()
            return
        if message["type"] == "websocket.close":
            self._closed = True
            return
        if message["type"] == "websocket.send":
            payload = json.loads(message.get("text") or "{}")
            self.outbox.append(payload)
            if payload.get("type") in self.slow_send_types:
                await asyncio.sleep(self.slow_send_delay)
            else:
                await asyncio.sleep(0)  # natural checkpoint

    async def start(self):
        await self.inbox.put({"type": "websocket.connect"})
        self.task = asyncio.create_task(
            self.app(self.scope(), self._receive, self._send)
        )
        await asyncio.wait_for(self._connected.wait(), 5)

    async def send_json(self, obj):
        await self.inbox.put({"type": "websocket.receive", "text": json.dumps(obj)})

    async def disconnect(self):
        await self.inbox.put({"type": "websocket.disconnect", "code": 1000})
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, 5)
            except asyncio.TimeoutError:
                pass

    def types(self):
        return [e.get("type") for e in self.outbox]

    async def wait_for(self, predicate, timeout=5.0):
        """Poll the outbox until predicate(outbox) is truthy."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            found = predicate(self.outbox)
            if found:
                return found
            await asyncio.sleep(0.01)
        return predicate(self.outbox)
