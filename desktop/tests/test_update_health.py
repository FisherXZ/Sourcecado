"""The launch check: does the version that was just installed actually come up?

This is what makes criterion 4's "failed launch" a real branch rather than a
callable somebody passes in. The check launches the sidecar that is inside the
installed application, exactly as the shell does -- loopback, its own token, an
isolated state directory -- and waits for the health handshake.

The isolated state directory is the part worth stating twice. A launch check
that opened the operator's real state would be a change the rollback could not
undo, so it never does.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import update_fixtures as fx  # noqa: E402
from coworker.update_channel.apply import SIDECAR_RELATIVE, sidecar_health_check  # noqa: E402

# The installed sidecar is a real executable. The test stand-in has to be too,
# because sidecar_health_check execs it the way the shell would. Putting
# sys.executable on a shebang fails on the macOS 26 runner (Python.app is not
# a valid interpreter path there), so the stub is /bin/sh wrapping that same
# interpreter.
STUB_PY = '''
import argparse, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

STATUS = os.environ.get("STUB_HEALTH", "ok")
if STATUS == "crash":
    sys.exit(3)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get("X-Club-Token") is None:
            self.send_response(401)
            self.end_headers()
            return
        # Prove the launch check gave us a throwaway state directory.
        body = json.dumps({
            "status": STATUS,
            "state_dir": os.environ.get("CLUB_STATE_DIR", ""),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class ReuseHTTPServer(HTTPServer):
    allow_reuse_address = True

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()
ReuseHTTPServer((args.host, args.port), Handler).serve_forever()
'''


def _install_with_sidecar(tmp_path, *, health: str = "ok"):
    install = fx.installation(tmp_path, version="0.0.2")
    fx.app_tree(install.bundle_path.parent, version="0.0.2", sidecar="#!/bin/sh\nexit 1\n")
    binary = install.bundle_path / SIDECAR_RELATIVE
    script = binary.with_name("health-stub.py")
    script.write_text(STUB_PY, encoding="utf-8")
    binary.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(sys.executable)} {shlex.quote(str(script))} \"$@\"\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    os.environ["STUB_HEALTH"] = health
    return install


def test_a_sidecar_that_answers_the_health_handshake_passes(tmp_path):
    install = _install_with_sidecar(tmp_path)
    try:
        assert sidecar_health_check(install, timeout=25.0) is True
    finally:
        os.environ.pop("STUB_HEALTH", None)


def test_a_sidecar_that_reports_an_unhealthy_status_fails(tmp_path):
    install = _install_with_sidecar(tmp_path, health="degraded")
    try:
        assert sidecar_health_check(install, timeout=25.0) is False
    finally:
        os.environ.pop("STUB_HEALTH", None)


def test_a_sidecar_that_exits_immediately_fails(tmp_path):
    install = _install_with_sidecar(tmp_path, health="crash")
    try:
        assert sidecar_health_check(install, timeout=25.0) is False
    finally:
        os.environ.pop("STUB_HEALTH", None)


def test_an_application_with_no_sidecar_in_it_fails(tmp_path):
    install = fx.installation(tmp_path, version="0.0.2")
    fx.app_tree(install.bundle_path.parent, version="0.0.2")
    assert not (install.bundle_path / SIDECAR_RELATIVE).exists()
    assert sidecar_health_check(install, timeout=5.0) is False


def test_the_launch_check_never_opens_the_operators_state(tmp_path):
    """The check runs against a throwaway directory, so a rollback stays complete."""
    install = _install_with_sidecar(tmp_path)
    install.state_root.mkdir(parents=True, exist_ok=True)
    marker = install.state_root / "club.db"
    marker.write_bytes(b"operator state")
    try:
        assert sidecar_health_check(install, timeout=25.0) is True
    finally:
        os.environ.pop("STUB_HEALTH", None)
    assert marker.read_bytes() == b"operator state"
    assert sorted(item.name for item in install.state_root.iterdir()) == ["club.db"]
