"""Who owns an Agent Run right now, and proof of whether that process still lives.

A lease names an owner. Recovery may only take a lease away from an owner it can
prove is gone, so ownership needs evidence the operating system maintains rather
than a value Sourcecado writes down. Each owner holds an exclusive `flock` on its
own marker file for the life of the process. The kernel releases that lock when
the process exits, including a kill or a panic, and never before. So:

- the marker locks against us, the owner is alive;
- the marker is free, the owner is dead;
- anything else -- another host, a missing marker, a platform without `flock` --
  is unknown, and unknown never authorizes a reclaim.

An unknown owner is not stranded. Its lease still expires, and an expired lease
is fenced by version, so reclaiming it cannot produce two writers.
"""

from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Sourcecado targets macOS and Linux
    fcntl = None  # type: ignore[assignment]

OWNERS_DIR_NAME = "agent_run_owners"
_MARKER_SUFFIX = ".owner"


class Liveness(StrEnum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RunOwner:
    """One process's claim on Agent Run work, for the life of that process."""

    owner_id: str
    host: str
    pid: int


class OwnerRegistry:
    def __init__(self, base_dir: str | Path) -> None:
        self.dir = Path(base_dir).expanduser() / OWNERS_DIR_NAME
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self.host = socket.gethostname()
        self._held: dict[str, int] = {}

    def register(self, owner_id: str | None = None) -> RunOwner:
        """Claim an owner identity and hold its liveness marker until exit."""
        pid = os.getpid()
        identity = owner_id or f"{self.host}-{pid}-{uuid.uuid4().hex[:12]}"
        descriptor = os.open(self._marker(identity), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, f"{self.host} {pid}\n".encode())
        except OSError:
            os.close(descriptor)
            raise
        self._held[identity] = descriptor
        return RunOwner(owner_id=identity, host=self.host, pid=pid)

    def release(self, owner_id: str) -> None:
        descriptor = self._held.pop(owner_id, None)
        if descriptor is None:
            return
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        self._marker(owner_id).unlink(missing_ok=True)

    def liveness_of(self, owner_id: str | None, host: str | None) -> Liveness:
        if not owner_id:
            return Liveness.UNKNOWN
        if owner_id in self._held:
            return Liveness.ALIVE
        if host and host != self.host:
            return Liveness.UNKNOWN
        if fcntl is None:
            return Liveness.UNKNOWN
        marker = self._marker(owner_id)
        if not marker.is_file():
            return Liveness.UNKNOWN
        try:
            descriptor = os.open(marker, os.O_RDWR)
        except OSError:
            return Liveness.UNKNOWN
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return Liveness.ALIVE
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return Liveness.DEAD
        finally:
            os.close(descriptor)

    def forget(self, owner_id: str) -> None:
        """Drop a marker after its owner was proven dead and its work reclaimed."""
        if owner_id in self._held:
            return
        self._marker(owner_id).unlink(missing_ok=True)

    def _marker(self, owner_id: str) -> Path:
        name = "".join(
            char if char.isalnum() or char in "._-" else "_" for char in owner_id
        )
        return self.dir / f"{name}{_MARKER_SUFFIX}"
