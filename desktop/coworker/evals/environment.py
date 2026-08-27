"""Per-run state and connector isolation for evaluations."""

from __future__ import annotations

import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coworker.apollo import FakeHttp
from coworker.gmail import FakeGmail
from coworker.inbox import Inbox
from coworker.mcp import FakeMcp
from coworker.people import PersonStore
from coworker.store import ConversationStore
from coworker.workspace import GrantAccess
from coworker.workspace_runtime import WorkspaceRuntime


def private_directory(path: str | Path, *, description: str = "private directory") -> Path:
    directory = Path(path)
    if directory.is_symlink():
        raise ValueError(f"{description} cannot be a symlink")
    if directory.exists():
        if not directory.is_dir():
            raise ValueError(f"{description} must be a directory")
        mode = stat.S_IMODE(directory.stat().st_mode)
        if mode != 0o700:
            raise ValueError(
                f"existing {description} must already use mode 0700; got {mode:04o}"
            )
        return directory
    if not directory.parent.is_dir():
        raise ValueError(f"{description} parent must already exist")
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        return private_directory(directory, description=description)
    os.chmod(directory, 0o700)
    return directory


def private_artifact_root(path: str | Path) -> Path:
    root = Path(path)
    if root.exists() and (
        root.resolve() in {Path("/").resolve(), Path.home().resolve()}
        or (root / ".git").exists()
    ):
        raise ValueError("private artifact root cannot be a broad system or repository root")
    return private_directory(root, description="private artifact root")


def write_private_text(path: str | Path, content: str) -> None:
    target = Path(path)
    if target.is_symlink():
        raise ValueError("private artifact file cannot be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}-", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


class CredentialFreeConnector:
    """Network-inert connector whose calls are visible and fail closed."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, operation: str):
        def unavailable(*args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(
                {"operation": operation, "args": list(args), "kwargs": dict(kwargs)}
            )
            raise RuntimeError(f"{self.name} eval fake has no fixture for {operation}")

        return unavailable


@dataclass(slots=True)
class ConnectorFakes:
    gmail: FakeGmail
    drive: CredentialFreeConnector
    calendar: CredentialFreeConnector
    http: FakeHttp
    mcp: FakeMcp

    @classmethod
    def create(cls) -> "ConnectorFakes":
        def environment_probe(_arguments: dict[str, Any]) -> dict[str, Any]:
            return {"sensitive_keys": sensitive_environment_keys()}

        return cls(
            gmail=FakeGmail(),
            drive=CredentialFreeConnector("drive"),
            calendar=CredentialFreeConnector("calendar"),
            http=FakeHttp(),
            mcp=FakeMcp(
                [
                    {
                        "name": "mcp__eval__environment_probe",
                        "description": "Report sensitive environment variable names.",
                        "handler": environment_probe,
                    }
                ]
            ),
        )


def sensitive_environment_keys() -> list[str]:
    markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTHORIZATION")
    return sorted(
        name for name in os.environ if any(marker in name.upper() for marker in markers)
    )


@dataclass(slots=True)
class EvalEnvironment:
    root: Path
    state_dir: Path
    workspace_dir: Path
    home_dir: Path
    store: ConversationStore
    people: PersonStore
    inbox: Inbox
    workspace_runtime: WorkspaceRuntime
    workspace_grant: dict[str, Any]
    connectors: ConnectorFakes
    credential_environment: dict[str, str]

    @classmethod
    def create(
        cls,
        artifact_root: Path,
        *,
        label: str,
        apply_environment: bool = False,
    ) -> "EvalEnvironment":
        safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.") or "run"
        artifact_root = private_artifact_root(artifact_root)
        runs_dir = private_directory(artifact_root / "runs")
        root = runs_dir / f"{safe_label}-{uuid.uuid4().hex[:12]}"
        state_dir = root / "state"
        workspace_dir = root / "workspace"
        home_dir = root / "home"
        for path in (root, state_dir, workspace_dir, home_dir):
            private_directory(path)
        credential_environment = {
            "CLUB_STATE_DIR": str(state_dir),
            "HOME": str(home_dir),
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
        }
        if apply_environment:
            os.environ.clear()
            os.environ.update(credential_environment)
        store = ConversationStore(state_dir)
        people = PersonStore(state_dir)
        workspace_runtime = WorkspaceRuntime(state_dir)
        workspace_grant = workspace_runtime.add_grant(
            workspace_dir,
            label="Evaluation workspace",
            access=GrantAccess.READ_WRITE,
            allow_shell=False,
        )
        return cls(
            root=root,
            state_dir=state_dir,
            workspace_dir=workspace_dir,
            home_dir=home_dir,
            store=store,
            people=people,
            inbox=Inbox(store),
            workspace_runtime=workspace_runtime,
            workspace_grant=workspace_grant,
            connectors=ConnectorFakes.create(),
            credential_environment=credential_environment,
        )

    def close(self) -> None:
        self.workspace_runtime.close()
        self.store._conn.close()
        self.people._conn.close()
