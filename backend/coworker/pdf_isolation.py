"""Reads a PDF in a child process that cannot outlive its limits.

A PDF is hostile input. ``pypdf`` can spend minutes of CPU, and hundreds of
megabytes, on a file of a few kilobytes. A thread cannot be stopped from
outside, so the read runs in a child process (``pdf_worker``). This module
starts it and watches three things:

- the wall clock: the child is killed after ``PDF_SECONDS``;
- the child's resident memory: it is killed above ``MAX_WORKER_RSS``;
- the caller: setting ``cancel`` (the upload was dropped, or the chip was
  removed) kills the child at once.

The child gets no environment but ``PATH``, so no key reaches it. It runs in
its own process group and the whole group is killed. At most ``MAX_PARALLEL``
children run at once, so a burst of uploads cannot take every core.

A packaged build has no ``python -m``. The frozen backend runs itself with
``--pdf-worker`` instead; ``packaging/backend_entry.py`` handles that flag.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# Wall clock for one read, child start to kill. The child stops reading new
# pages three seconds sooner and returns what it has.
PDF_SECONDS = 15.0
_SOFT_SECONDS = PDF_SECONDS - 3.0
MAX_PDF_PAGES = 200
# One content stream, after decompression. Real pages are far smaller.
MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_WORKER_RSS = 768 * 1024 * 1024
MAX_PARALLEL = 2
_POLL_SECONDS = 0.25

_slots = threading.BoundedSemaphore(MAX_PARALLEL)


class PdfRefused(Exception):
    """The read did not finish. ``code`` is stable; ``message`` is for the director."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


UNREADABLE = PdfRefused(
    "corrupt_pdf", "This PDF could not be read. It may be damaged."
)


def _command(max_chars: int) -> tuple[list[str], str | None]:
    options = [
        "--max-chars", str(max_chars),
        "--max-pages", str(MAX_PDF_PAGES),
        "--max-stream", str(MAX_STREAM_BYTES),
        "--seconds", str(_SOFT_SECONDS),
    ]  # fmt: skip
    if getattr(sys, "frozen", False):
        return [sys.executable, "--pdf-worker", *options], None
    # The directory that holds the `coworker` package, so `-m` finds it.
    return (
        [sys.executable, "-m", "coworker.pdf_worker", *options],
        str(Path(__file__).resolve().parent.parent),
    )


def _rss_bytes(pid: int) -> int:
    """Resident memory of a process, or 0 when it cannot be read."""
    try:
        with open(f"/proc/{pid}/statm", encoding="ascii") as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip()
        return int(out) * 1024 if out else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _kill(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass


def read_pdf(
    data: bytes, *, max_chars: int, cancel: threading.Event | None = None
) -> dict:
    """The worker's result dict. Raises ``PdfRefused`` when the read is stopped."""
    while not _slots.acquire(timeout=_POLL_SECONDS):
        if cancel is not None and cancel.is_set():
            raise PdfRefused("cancelled", "The upload was cancelled.")
    try:
        command, cwd = _command(max_chars)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": os.environ.get("PATH", os.defpath)},
            start_new_session=True,
        )
        started = time.monotonic()
        pending: bytes | None = data
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise PdfRefused("cancelled", "The upload was cancelled.")
                if time.monotonic() - started > PDF_SECONDS:
                    raise PdfRefused(
                        "pdf_timeout",
                        "This PDF took too long to read. It may be too complex.",
                    )
                if _rss_bytes(process.pid) > MAX_WORKER_RSS:
                    raise PdfRefused(
                        "pdf_too_complex", "This PDF is too complex to read."
                    )
                try:
                    out, _ = process.communicate(input=pending, timeout=_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pending = None
                except BrokenPipeError:
                    out = b""
                    break
        finally:
            _kill(process)
        try:
            result = json.loads(out)
        except ValueError:
            raise UNREADABLE from None
        if not isinstance(result, dict):
            raise UNREADABLE
        return result
    finally:
        _slots.release()
