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

import http.client
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

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

    def handle_error(self, request, client_address):
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()

import socket, time as _t
_t0 = _t.monotonic()
try:
    socket.getfqdn("127.0.0.1")
    _fqdn = f"{_t.monotonic() - _t0:.2f}s"
except Exception as _exc:
    _fqdn = f"failed {_exc}"
print(f"getfqdn took {_fqdn}", file=sys.stderr, flush=True)

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


def _assert_healthy(install, timeout=25.0):
    if sidecar_health_check(install, timeout=timeout):
        return
    raise AssertionError(
        "health handshake failed\n" + "\n".join(_health_diagnostics(install))
    )


def _health_diagnostics(install) -> list[str]:
    """Why the handshake failed, in a form a CI log reader can act on.

    Every probe is individually guarded. A diagnostic that raises replaces the
    diagnosis with its own error, which is exactly what happened before (#140):
    `communicate()` was called on a stub that ends in `serve_forever()`, so it
    always timed out and the collected details were discarded.
    """
    binary = install.bundle_path / SIDECAR_RELATIVE
    details: list[str] = []

    def probe(label, fn):
        try:
            details.append(f"{label}: {fn()}")
        except Exception as exc:  # never let a probe hide the diagnosis
            details.append(f"{label}: PROBE FAILED {type(exc).__name__}: {exc}")

    probe("wrapper", lambda: "\n" + binary.read_text())
    probe("executable", lambda: sys.executable)
    probe(
        "binary",
        lambda: f"exists={binary.is_file()} x_ok={os.access(binary, os.X_OK)}",
    )

    env = os.environ.copy()
    env["CLUB_STATE_DIR"] = str(install.state_root / "diag-state")
    env["CLUB_API_TOKEN"] = "diag-token"
    env.pop("CLUB_EXIT_WITH_PARENT", None)
    probe("STUB_HEALTH", lambda: env.get("STUB_HEALTH"))

    port = _free_diagnostic_port()
    probe("diagnostic port", lambda: port)

    # Can this interpreter run anything at all? The stub's first statement
    # writes to stderr, and on the failing runner that line never appears,
    # so the question is whether the interpreter itself starts (#140).
    def interpreter_smoke():
        done = subprocess.run(
            [sys.executable, "-c", "import sys; sys.stderr.write('ALIVE\\n')"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return f"rc={done.returncode} out={done.stdout!r} err={done.stderr!r}"

    probe("interpreter smoke", interpreter_smoke)

    # And can the wrapper itself run a trivial program the same way sh execs it?
    def wrapper_smoke():
        script = binary.with_name("smoke-probe.py")
        script.write_text("import sys; sys.stderr.write('WRAPPER_ALIVE\\n')\n")
        wrapper = binary.with_name("smoke-wrapper.sh")
        wrapper.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))}\n"
        )
        wrapper.chmod(0o755)
        done = subprocess.run(
            [str(wrapper)], capture_output=True, text=True, timeout=15
        )
        return f"rc={done.returncode} out={done.stdout!r} err={done.stderr!r}"

    probe("wrapper smoke", wrapper_smoke)
    probe("stub head", lambda: repr(binary.with_name("health-stub.py").read_text()[:80]))
    try:
        proc = subprocess.Popen(
            [str(binary), "--host", "127.0.0.1", "--port", str(port)],
            cwd=tempfile.gettempdir(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as exc:
        details.append(f"launch: FAILED {type(exc).__name__}: {exc}")
        return details

    try:
        time.sleep(0.5)
        probe("poll after 0.5s", proc.poll)
        time.sleep(3.0)
        probe("poll after 3.5s", proc.poll)

        def raw_connect():
            import socket as _s

            started = time.monotonic()
            with _s.create_connection(("127.0.0.1", port), timeout=5):
                return f"tcp connect ok in {time.monotonic() - started:.2f}s"

        def handshake():
            started = time.monotonic()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request(
                    "GET", "/v1/health", headers={"X-Club-Token": "diag-token"}
                )
                sent = time.monotonic() - started
                resp = conn.getresponse()
                body = resp.read()
                return (
                    f"{resp.status} {body!r} "
                    f"(request {sent:.2f}s, total {time.monotonic() - started:.2f}s)"
                )
            finally:
                conn.close()

        if proc.poll() is None:
            probe("tcp", raw_connect)
            probe("http", handshake)
        else:
            details.append("http: skipped, process already exited")
    finally:
        # Kill first. The stub serves forever, so reading its output before
        # ending it is a guaranteed timeout, which is the original bug.
        if proc.poll() is None:
            proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=5)
            details.append(f"stdout: {stdout!r}")
            details.append(f"stderr: {stderr!r}")
        except Exception as exc:
            details.append(f"stdout: UNREADABLE {type(exc).__name__}: {exc}")
            details.append("stderr: UNREADABLE")

    return details


def _free_diagnostic_port() -> int:
    """A port nobody else holds, so a busy runner cannot fail the probe."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_a_sidecar_that_answers_the_health_handshake_passes(tmp_path):
    install = _install_with_sidecar(tmp_path)
    try:
        _assert_healthy(install, timeout=25.0)
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
        _assert_healthy(install, timeout=25.0)
    finally:
        os.environ.pop("STUB_HEALTH", None)
    assert marker.read_bytes() == b"operator state"
    assert sorted(item.name for item in install.state_root.iterdir()) == ["club.db"]


# --------------------------------------------------------------------------
# Issue #140 - the diagnostic that explains a failed handshake must actually
# reach the operator. It used to die inside itself and take the diagnosis
# with it, which is why the CI failure went unexplained for six days.
# --------------------------------------------------------------------------


def test_the_health_diagnostic_reports_instead_of_dying_inside_itself(tmp_path):
    """The stub serves forever, so anything that waits for it to exit hangs.

    `_assert_healthy` collected a useful `details` list and then called
    `communicate()` on a process that never exits. The resulting
    TimeoutExpired replaced the diagnosis with a subprocess error.
    """
    install = _install_with_sidecar(tmp_path, health="degraded")
    try:
        with pytest.raises(AssertionError) as caught:
            _assert_healthy(install, timeout=5.0)
    finally:
        os.environ.pop("STUB_HEALTH", None)

    report = str(caught.value)
    assert "health handshake failed" in report
    # The things a reader needs in order to act, not just that it broke.
    assert "wrapper:" in report
    assert "poll after" in report
    assert "stdout:" in report
