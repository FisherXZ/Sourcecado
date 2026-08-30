#!/usr/bin/env python3
"""Smoke-tests the packaged Sourcecado sidecar the way the Tauri shell spawns it:
loopback bind, health/auth handshake, isolated per-launch state, and a clean
shutdown that leaves no orphaned process.

Usage: python3 smoke_test.py <path-to-sourcecado-sidecar-binary>
"""

from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time


def wait_for_port(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection(host, port, timeout=1)
            conn.connect()
            conn.close()
            return True
        except OSError:
            time.sleep(0.25)
    return False


def get(host: str, port: int, path: str, token: str | None) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"X-Club-Token": token} if token else {}
    conn.request("GET", path, headers=headers)
    return conn.getresponse()


def post(
    host: str,
    port: int,
    path: str,
    token: str,
    payload: dict,
) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(
        "POST",
        path,
        body=json.dumps(payload),
        headers={
            "X-Club-Token": token,
            "Content-Type": "application/json",
        },
    )
    return conn.getresponse()


class FakeOpenAI:
    """Local deterministic provider used by the packaged person-chat smoke."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.tool_call_responses = 0
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
                length = int(self.headers.get("content-length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    body = {}
                owner.requests.append(body if isinstance(body, dict) else {})
                if len(owner.requests) == 1:
                    owner.tool_call_responses += 1
                    event = {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "packaged-smoke-now",
                                            "function": {
                                                "name": "now",
                                                "arguments": "{}",
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    }
                else:
                    event = {
                        "choices": [
                            {
                                "delta": {"content": "PACKAGED_PERSON_CHAT_OK"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                payload = (
                    f"data: {json.dumps(event)}\n\n"
                    "data: [DONE]\n\n"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def run_chat(host: str, port: int, token: str, session_id: str, text: str) -> list[dict]:
    from websockets.sync.client import connect

    events: list[dict] = []
    with connect(
        f"ws://{host}:{port}/ws/chat",
        subprotocols=["club", token],
        open_timeout=5,
    ) as socket:
        socket.send(json.dumps({"type": "chat", "text": text, "session_id": session_id}))
        deadline = time.time() + 20
        while time.time() < deadline:
            event = json.loads(socket.recv(timeout=5))
            events.append(event)
            if event.get("type") in {"turn_end", "error"}:
                break
    return events


def stop_process(proc: subprocess.Popen) -> bool:
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return proc.poll() is not None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", help="path to the frozen sourcecado-sidecar binary")
    args = parser.parse_args()

    binary = os.path.abspath(args.binary)
    if not os.path.isfile(binary):
        print(f"error: no such file: {binary}", file=sys.stderr)
        return 1

    state_dir = tempfile.mkdtemp(prefix="sourcecado-smoke-state-")
    token = secrets.token_hex(16)
    port = 18900
    env = dict(os.environ)
    env["CLUB_STATE_DIR"] = state_dir
    env["CLUB_API_TOKEN"] = token
    env.pop("CLUB_EXIT_WITH_PARENT", None)
    for key in (
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        env[key] = ""
    fake_provider = FakeOpenAI()
    env["OPENAI_API_KEY"] = "packaged-smoke-key"
    env["OPENAI_BASE_URL"] = fake_provider.base_url
    env["CLUB_MODEL"] = "gpt-4o-mini"

    # Launch from outside the repo, same as the packaged app's own cwd.
    cwd = tempfile.gettempdir()

    def launch() -> subprocess.Popen:
        return subprocess.Popen(
            [binary, "--host", "127.0.0.1", "--port", str(port)],
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    proc = launch()
    try:
        if not wait_for_port("127.0.0.1", port):
            print("FAIL sidecar never opened its loopback port", file=sys.stderr)
            return 1

        res = get("127.0.0.1", port, "/v1/health", token)
        if res.status != 200:
            print(f"FAIL health check returned {res.status}", file=sys.stderr)
            return 1
        health = json.loads(res.read())
        if health.get("status") != "ok":
            print(f"FAIL unexpected health payload: {health}", file=sys.stderr)
            return 1
        print(f"OK   health handshake: {health}")

        res = get("127.0.0.1", port, "/v1/health", None)
        if res.status != 401:
            print(f"FAIL unauthenticated request should be rejected, got {res.status}", file=sys.stderr)
            return 1
        print("OK   auth handshake: unauthenticated request rejected")

        # Isolated state: the weekly job coworker.run seeds on first boot, and
        # the sqlite file it lives in, must land under this launch's own
        # CLUB_STATE_DIR rather than a shared or repo-relative location.
        res = get("127.0.0.1", port, "/v1/schedule", token)
        if res.status != 200:
            print(f"FAIL schedule read returned {res.status}", file=sys.stderr)
            return 1
        jobs = json.loads(res.read())["jobs"]
        if not jobs:
            print("FAIL expected the seeded weekly job in isolated state", file=sys.stderr)
            return 1
        db_path = os.path.join(state_dir, "club.db")
        if not os.path.exists(db_path):
            print(f"FAIL expected isolated state at {db_path}", file=sys.stderr)
            return 1
        print(f"OK   isolated state created under {state_dir}")

        sessions = json.loads(get("127.0.0.1", port, "/v1/sessions", token).read())
        session_id = sessions.get("open_id")
        if not isinstance(session_id, str) or not session_id:
            print("FAIL expected an open conversation for person-chat smoke", file=sys.stderr)
            return 1
        curated = post(
            "127.0.0.1",
            port,
            "/v1/apollo/curate",
            token,
            {
                "session_id": session_id,
                "target": "packaged retained-chat smoke",
                "bind_original": True,
                "people": [
                    {
                        "apolloId": "packaged-smoke-person",
                        "firstName": "Ada",
                        "lastNameObfuscated": "L.",
                        "title": "Founder",
                        "organizationName": "Analytic",
                    }
                ],
            },
        )
        if curated.status != 200:
            print(f"FAIL person curation returned {curated.status}", file=sys.stderr)
            return 1
        curated.read()

        first = run_chat(
            "127.0.0.1",
            port,
            token,
            session_id,
            "Use the safe time tool, then confirm this person chat works.",
        )
        if not first or first[-1].get("type") != "turn_end" or first[-1].get("state") != "complete":
            print("FAIL packaged person chat did not complete its first turn", file=sys.stderr)
            return 1

        if not stop_process(proc):
            print("FAIL sidecar did not stop for restart smoke", file=sys.stderr)
            return 1
        proc = launch()
        if not wait_for_port("127.0.0.1", port):
            print("FAIL restarted sidecar never opened its loopback port", file=sys.stderr)
            return 1
        second = run_chat(
            "127.0.0.1",
            port,
            token,
            session_id,
            "Confirm the retained person-bound chat still works after restart.",
        )
        text = "".join(
            str(event.get("delta") or "")
            for event in second
            if event.get("type") == "assistant_delta"
        )
        if (
            not second
            or second[-1].get("type") != "turn_end"
            or second[-1].get("state") != "complete"
            or "PACKAGED_PERSON_CHAT_OK" not in text
        ):
            print("FAIL retained person chat did not complete after restart", file=sys.stderr)
            return 1
        retained_tool_result = any(
            message.get("role") == "tool"
            and message.get("tool_call_id") == "packaged-smoke-now"
            for message in (fake_provider.requests[-1].get("messages") or [])
            if isinstance(message, dict)
        )
        if fake_provider.tool_call_responses != 1 or not retained_tool_result:
            print("FAIL completed tool history was replayed or discarded", file=sys.stderr)
            return 1
        print("OK   retained person chat completed before and after sidecar restart")
    finally:
        stopped = stop_process(proc)  # matches Tauri Child::kill() on Quit
        fake_provider.close()

    if not stopped:
        print("FAIL sidecar did not exit after kill()", file=sys.stderr)
        return 1
    print("OK   sidecar exited cleanly, no orphaned process")

    shutil.rmtree(state_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
