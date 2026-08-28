#!/usr/bin/env python3
"""Smoke-tests the packaged Sourcecado sidecar the way the Tauri shell spawns it:
loopback bind, health/auth handshake, isolated per-launch state, and a clean
shutdown that leaves no orphaned process.

Usage: python3 smoke_test.py <path-to-sourcecado-sidecar-binary>
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
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

    # Launch from outside the repo, same as the packaged app's own cwd.
    cwd = tempfile.gettempdir()

    proc = subprocess.Popen(
        [binary, "--host", "127.0.0.1", "--port", str(port)],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
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
    finally:
        proc.kill()  # matches the Tauri shell's Child::kill() on Quit
        timed_out = False
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            timed_out = True

    if timed_out:
        print("FAIL sidecar did not exit after kill()", file=sys.stderr)
        return 1
    if proc.poll() is None:
        print("FAIL sidecar process still alive after kill()", file=sys.stderr)
        return 1
    print("OK   sidecar exited cleanly, no orphaned process")

    shutil.rmtree(state_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
