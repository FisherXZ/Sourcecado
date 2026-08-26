"""Sourcecado-owned workspace capability, policy, execution, and receipt boundary."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coworker.workspace import GrantAccess, GrantUnavailable, WorkspaceGrantStore
from coworker.workspace_audit import WorkspaceAuditStore
from coworker.workspace_files import (
    StaleWorkspaceWrite,
    WorkspaceApprovalRequired,
    WorkspaceFilesystem,
    WorkspacePathError,
    is_protected_workspace_path,
)
from coworker.workspace_policy import RiskClass
from coworker.workspace_shell import DockerSandbox, ShellDecision, ShellRuntime


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_GRANT = {"type": "string", "description": "Opaque workspace grant ID."}
_PATH = {
    "type": "string",
    "description": "Path relative to the authorized workspace root.",
}
_LIMIT = {"type": "integer", "minimum": 1}

WORKSPACE_TOOL_SCHEMAS = [
    _schema(
        "fs_stat",
        "Inspect safe metadata for one workspace path.",
        {"grant_id": _GRANT, "path": _PATH},
        ["grant_id", "path"],
    ),
    _schema(
        "fs_list",
        "List one authorized directory with bounded pagination.",
        {
            "grant_id": _GRANT,
            "path": _PATH,
            "offset": {"type": "integer", "minimum": 0},
            "max_entries": _LIMIT,
        },
        ["grant_id", "path"],
    ),
    _schema(
        "fs_find",
        "Find files by a filename glob below an authorized directory.",
        {
            "grant_id": _GRANT,
            "path": _PATH,
            "pattern": {"type": "string"},
            "max_results": _LIMIT,
        },
        ["grant_id", "path", "pattern"],
    ),
    _schema(
        "fs_search",
        "Search bounded UTF-8 workspace files for literal text.",
        {
            "grant_id": _GRANT,
            "path": _PATH,
            "query": {"type": "string"},
            "max_results": _LIMIT,
        },
        ["grant_id", "path", "query"],
    ),
    _schema(
        "fs_read",
        "Read a bounded page of UTF-8 text or return binary metadata.",
        {
            "grant_id": _GRANT,
            "path": _PATH,
            "offset": {"type": "integer", "minimum": 0},
            "max_chars": _LIMIT,
        },
        ["grant_id", "path"],
    ),
    _schema(
        "fs_mkdir",
        "Create a directory inside a read-write workspace.",
        {"grant_id": _GRANT, "path": _PATH, "parents": {"type": "boolean"}},
        ["grant_id", "path"],
    ),
    _schema(
        "fs_write",
        "Atomically create or version-check and replace a UTF-8 file.",
        {
            "grant_id": _GRANT,
            "path": _PATH,
            "content": {"type": "string"},
            "expected_before_hash": {"type": ["string", "null"]},
            "create_parents": {"type": "boolean"},
        },
        ["grant_id", "path", "content"],
    ),
    _schema(
        "fs_patch",
        "Apply exact text replacements to a version-checked UTF-8 file.",
        {
            "grant_id": _GRANT,
            "path": _PATH,
            "replacements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "old": {"type": "string"},
                        "new": {"type": "string"},
                    },
                    "required": ["old", "new"],
                    "additionalProperties": False,
                },
            },
            "expected_before_hash": {"type": ["string", "null"]},
        },
        ["grant_id", "path", "replacements"],
    ),
    _schema(
        "fs_copy",
        "Atomically copy a regular file between authorized paths.",
        {
            "grant_id": _GRANT,
            "path": _PATH,
            "destination_grant_id": _GRANT,
            "destination_path": _PATH,
            "expected_destination_hash": {"type": ["string", "null"]},
            "create_parents": {"type": "boolean"},
        },
        ["grant_id", "path", "destination_path"],
    ),
    _schema(
        "fs_move",
        "Move a version-checked regular file between authorized paths.",
        {
            "grant_id": _GRANT,
            "path": _PATH,
            "destination_grant_id": _GRANT,
            "destination_path": _PATH,
            "expected_source_hash": {"type": ["string", "null"]},
            "expected_destination_hash": {"type": ["string", "null"]},
            "create_parents": {"type": "boolean"},
        },
        ["grant_id", "path", "destination_path"],
    ),
    _schema(
        "fs_trash",
        "Move a file or directory into recoverable Sourcecado trash after approval.",
        {"grant_id": _GRANT, "path": _PATH},
        ["grant_id", "path"],
    ),
    _schema(
        "request_directory",
        "Ask the operator to select and authorize a local folder in Settings.",
        {
            "label": {"type": "string"},
            "access": {"type": "string", "enum": ["read_only", "read_write"]},
            "allow_shell": {"type": "boolean"},
        },
        ["label", "access"],
    ),
    _schema(
        "shell_exec",
        "Run one explicit command in Docker or through approved host fallback.",
        {
            "grant_id": _GRANT,
            "command": {"type": "string"},
            "cwd": _PATH,
            "environment": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "background": {"type": "boolean"},
            "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 300},
        },
        ["grant_id", "command"],
    ),
    _schema(
        "shell_poll",
        "Read bounded incremental output and status for a shell task.",
        {"task_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}},
        ["task_id"],
    ),
    _schema(
        "shell_write_stdin",
        "Write explicit text to a running shell task after approval.",
        {"task_id": {"type": "string"}, "text": {"type": "string"}},
        ["task_id", "text"],
    ),
    _schema(
        "shell_kill",
        "Cancel a running shell task.",
        {"task_id": {"type": "string"}},
        ["task_id"],
    ),
]
WORKSPACE_TOOL_NAMES = frozenset(
    schema["function"]["name"] for schema in WORKSPACE_TOOL_SCHEMAS
)

_READ_TOOLS = frozenset(
    {"fs_stat", "fs_list", "fs_find", "fs_search", "fs_read", "shell_poll"}
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class RuntimeDecision:
    allowed: bool
    needs_approval: bool
    reason: str
    risk_class: RiskClass
    execution_target: str
    command_fingerprint: str | None = None


class DirectoryRequestStore:
    VERSION = 1

    def __init__(self, state_root: str | Path) -> None:
        self.state_root = Path(state_root).expanduser()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.path = self.state_root / "directory_requests.json"
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": self.VERSION, "requests": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": self.VERSION, "requests": []}
        if not isinstance(data, dict) or not isinstance(data.get("requests"), list):
            return {"version": self.VERSION, "requests": []}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        fd, raw_path = tempfile.mkstemp(
            prefix=".directory-requests-", suffix=".tmp", dir=self.state_root
        )
        temp_path = Path(raw_path)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(self.path)
            os.chmod(self.path, 0o600)
        finally:
            temp_path.unlink(missing_ok=True)

    def create(
        self, payload: dict[str, Any], *, session_id: str | None, run_id: str | None
    ) -> dict[str, Any]:
        request = {
            "id": f"directory_request_{uuid.uuid4().hex}",
            "label": str(payload.get("label") or "Workspace").strip() or "Workspace",
            "access": str(payload.get("access") or GrantAccess.READ_ONLY.value),
            "allow_shell": bool(payload.get("allow_shell")),
            "session_id": session_id,
            "run_id": run_id,
            "created_at": _now(),
            "resolved_at": None,
            "grant_id": None,
        }
        if request["access"] not in {item.value for item in GrantAccess}:
            raise WorkspacePathError("directory request access is invalid")
        if request["allow_shell"] and request["access"] != GrantAccess.READ_WRITE.value:
            raise WorkspacePathError("shell access requires a read_write request")
        with self._lock:
            data = self._load()
            data["requests"].append(request)
            self._save(data)
        return request

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(item)
                for item in self._load()["requests"]
                if item.get("resolved_at") is None
            ]

    def resolve(self, request_id: str, grant_id: str) -> None:
        with self._lock:
            data = self._load()
            for index, raw in enumerate(data["requests"]):
                if raw.get("id") != request_id:
                    continue
                item = dict(raw)
                item["grant_id"] = grant_id
                item["resolved_at"] = _now()
                data["requests"][index] = item
                self._save(data)
                return
        raise KeyError(request_id)


class WorkspaceRuntime:
    def __init__(
        self, state_root: str | Path, *, docker: DockerSandbox | None = None
    ) -> None:
        self.state_root = Path(state_root).expanduser()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.grants = WorkspaceGrantStore(self.state_root)
        self.files = WorkspaceFilesystem(self.grants, state_root=self.state_root)
        self.audit = WorkspaceAuditStore(self.state_root)
        self.directory_requests = DirectoryRequestStore(self.state_root)
        self.shell = ShellRuntime(
            state_root=self.state_root,
            grants=self.grants,
            files=self.files,
            docker=docker,
        )
        for task in self.shell.tasks.reconciled:
            self.audit.record(
                receipt_type="interrupted",
                tool="shell_exec",
                risk_class=RiskClass.CONSEQUENTIAL_COMMAND.value,
                decision="unknown",
                execution_target=str(task.get("execution_target") or "unknown"),
                grant_id=str(task.get("grant_id") or "") or None,
                command_fingerprint=str(task.get("command_fingerprint") or "") or None,
                task_id=str(task.get("task_id") or "") or None,
                started_at=str(task.get("started_at") or "") or None,
                finished_at=str(task.get("finished_at") or "") or None,
                status="interrupted",
                summary="Shell outcome unknown after Sourcecado restart",
            )

    def add_grant(
        self,
        path: str | Path,
        *,
        label: str,
        access: GrantAccess | str,
        allow_shell: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        grant = self.grants.add(
            path, label=label, access=access, allow_shell=allow_shell
        )
        if request_id:
            self.directory_requests.resolve(request_id, grant["id"])
        return grant

    def update_grant(self, grant_id: str, **changes: Any) -> dict[str, Any]:
        grant = self.grants.update(grant_id, **changes)
        stopped = self.shell.stop_for_grant(grant_id)
        self.shell.docker.close()
        return {**grant, "stopped_task_ids": stopped}

    def revoke_grant(self, grant_id: str) -> dict[str, Any]:
        grant = self.grants.revoke(grant_id)
        stopped = self.shell.stop_for_grant(grant_id)
        self.shell.docker.close()
        return {**grant, "stopped_task_ids": stopped}

    @staticmethod
    def _protected(path: str) -> bool:
        return is_protected_workspace_path(path)

    @staticmethod
    def _from_shell(decision: ShellDecision) -> RuntimeDecision:
        return RuntimeDecision(
            decision.allowed,
            decision.needs_approval,
            decision.reason,
            decision.risk_class,
            decision.execution_target,
            str(decision.fingerprint.get("digest") or "") or None,
        )

    def _decide(self, name: str, args: dict[str, Any]) -> RuntimeDecision:
        if name not in WORKSPACE_TOOL_NAMES:
            return RuntimeDecision(
                False,
                False,
                f"unknown workspace tool {name}",
                RiskClass.BOUNDARY_EXPANSION,
                "none",
            )
        if name in _READ_TOOLS:
            if name != "shell_poll":
                self.files.resolve(
                    str(args.get("grant_id") or ""), str(args.get("path") or ".")
                )
                if self._protected(str(args.get("path") or ".")):
                    return RuntimeDecision(
                        False,
                        True,
                        "Reading a protected file requires explicit approval.",
                        RiskClass.READ,
                        "typed_filesystem",
                    )
            return RuntimeDecision(
                True,
                False,
                "authorized workspace read",
                RiskClass.READ,
                "typed_filesystem",
            )
        if name == "request_directory":
            return RuntimeDecision(
                True,
                False,
                "creates a request; the operator must select the folder",
                RiskClass.BOUNDARY_EXPANSION,
                "operator_picker",
            )
        if name == "shell_exec":
            return self._from_shell(
                self.shell.decide(
                    grant_id=str(args.get("grant_id") or ""),
                    command=str(args.get("command") or ""),
                    cwd=str(args.get("cwd") or "."),
                    environment=args.get("environment")
                    if isinstance(args.get("environment"), dict)
                    else {},
                )
            )
        if name == "shell_kill":
            return RuntimeDecision(
                True,
                False,
                "cancels a running task",
                RiskClass.REVERSIBLE_WRITE,
                "shell_task",
            )
        if name == "shell_write_stdin":
            return RuntimeDecision(
                False,
                True,
                "Shell input may trigger consequential work and requires approval.",
                RiskClass.CONSEQUENTIAL_COMMAND,
                "shell_task",
            )
        grant_id = str(args.get("grant_id") or "")
        path = str(args.get("path") or "")
        must_exist = name in {"fs_patch", "fs_move", "fs_trash"}
        _grant, _root, target = self.files.resolve(
            grant_id, path, must_exist=must_exist, write=True
        )
        if name == "fs_trash":
            return RuntimeDecision(
                False,
                True,
                "Trash is destructive and requires approval.",
                RiskClass.DESTRUCTIVE_WRITE,
                "typed_filesystem",
            )
        destination_grant = str(args.get("destination_grant_id") or grant_id)
        destination_path = str(args.get("destination_path") or path)
        if self._protected(path) or self._protected(destination_path):
            return RuntimeDecision(
                False,
                True,
                "A protected file requires explicit approval.",
                RiskClass.DESTRUCTIVE_WRITE,
                "typed_filesystem",
            )
        if name in {"fs_write", "fs_patch"} and target.exists():
            expected = args.get("expected_before_hash")
            actual = self.files.stat(grant_id, path).get("sha256")
            if expected is None:
                return RuntimeDecision(
                    False,
                    True,
                    "A conflicting overwrite requires approval.",
                    RiskClass.DESTRUCTIVE_WRITE,
                    "typed_filesystem",
                )
            if expected != actual:
                return RuntimeDecision(
                    False,
                    False,
                    "stale: workspace target changed",
                    RiskClass.REVERSIBLE_WRITE,
                    "typed_filesystem",
                )
        if name == "fs_copy":
            _grant, _root, destination = self.files.resolve(
                destination_grant, destination_path, must_exist=False, write=True
            )
            if destination.exists() and args.get("expected_destination_hash") is None:
                return RuntimeDecision(
                    False,
                    True,
                    "A copy overwrite requires approval.",
                    RiskClass.DESTRUCTIVE_WRITE,
                    "typed_filesystem",
                )
        if name == "fs_move":
            if destination_grant != grant_id:
                return RuntimeDecision(
                    False,
                    True,
                    "A cross-root move requires approval.",
                    RiskClass.DESTRUCTIVE_WRITE,
                    "typed_filesystem",
                )
            if args.get("expected_source_hash") is None:
                return RuntimeDecision(
                    False,
                    True,
                    "A move requires the observed source hash or approval.",
                    RiskClass.DESTRUCTIVE_WRITE,
                    "typed_filesystem",
                )
            _grant, _root, destination = self.files.resolve(
                destination_grant,
                destination_path,
                must_exist=False,
                write=True,
            )
            if destination.exists():
                expected_destination = args.get("expected_destination_hash")
                if expected_destination is None:
                    return RuntimeDecision(
                        False,
                        True,
                        "A move overwrite requires approval.",
                        RiskClass.DESTRUCTIVE_WRITE,
                        "typed_filesystem",
                    )
                actual_destination = self.files.stat(
                    destination_grant, destination_path
                ).get("sha256")
                if expected_destination != actual_destination:
                    return RuntimeDecision(
                        False,
                        False,
                        "stale: move destination changed",
                        RiskClass.REVERSIBLE_WRITE,
                        "typed_filesystem",
                    )
        return RuntimeDecision(
            True,
            False,
            "authorized reversible workspace write",
            RiskClass.REVERSIBLE_WRITE,
            "typed_filesystem",
        )

    def decide_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        *,
        actor: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> RuntimeDecision:
        args = dict(arguments or {})
        try:
            decision = self._decide(name, args)
        except (WorkspacePathError, GrantUnavailable, KeyError, ValueError) as exc:
            decision = RuntimeDecision(
                False, False, str(exc), RiskClass.BOUNDARY_EXPANSION, "none"
            )
        self.audit.record(
            receipt_type="proposal",
            tool=name,
            risk_class=decision.risk_class.value,
            decision="auto"
            if decision.allowed
            else "ask"
            if decision.needs_approval
            else "deny",
            execution_target=decision.execution_target,
            grant_id=str(args.get("grant_id") or "") or None,
            path=str(args.get("path") or "") or None,
            command_fingerprint=decision.command_fingerprint,
            actor=actor,
            session_id=session_id,
            run_id=run_id,
            status="proposed",
            summary=f"{name} policy decision: {'auto' if decision.allowed else 'approval required' if decision.needs_approval else 'denied'}",
        )
        return decision

    def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        *,
        approval_granted: bool = False,
        approval_scope: str = "once",
        approval_fingerprint: str | None = None,
        actor: str = "assistant",
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        args = dict(arguments or {})
        started = time_monotonic_ms()
        decision = RuntimeDecision(
            False,
            False,
            "workspace decision unavailable",
            RiskClass.BOUNDARY_EXPANSION,
            "none",
        )
        try:
            decision = self._decide(name, args)
            if not decision.allowed and not decision.needs_approval:
                raise WorkspacePathError(decision.reason)
            if decision.needs_approval and not approval_granted:
                raise WorkspaceApprovalRequired(decision.reason)
            grant_id = str(args.get("grant_id") or "")
            path = str(args.get("path") or ".")
            if name == "fs_stat":
                result = {"receipt_type": "read", **self.files.stat(grant_id, path)}
            elif name == "fs_list":
                result = {
                    "receipt_type": "read",
                    **self.files.list(
                        grant_id,
                        path,
                        offset=int(args.get("offset") or 0),
                        max_entries=int(args.get("max_entries") or 100),
                    ),
                }
            elif name == "fs_find":
                result = {
                    "receipt_type": "read",
                    **self.files.find(
                        grant_id,
                        path,
                        pattern=str(args.get("pattern") or ""),
                        max_results=int(args.get("max_results") or 100),
                    ),
                }
            elif name == "fs_search":
                result = {
                    "receipt_type": "read",
                    **self.files.search(
                        grant_id,
                        path,
                        query=str(args.get("query") or ""),
                        max_results=int(args.get("max_results") or 50),
                    ),
                }
            elif name == "fs_read":
                result = {
                    "receipt_type": "read",
                    **self.files.read(
                        grant_id,
                        path,
                        offset=int(args.get("offset") or 0),
                        max_chars=int(args.get("max_chars") or 20_000),
                    ),
                }
            elif name == "fs_mkdir":
                result = self.files.mkdir(
                    grant_id, path, parents=bool(args.get("parents"))
                )
            elif name == "fs_write":
                result = self.files.write(
                    grant_id,
                    path,
                    str(args.get("content") or ""),
                    expected_before_hash=args.get("expected_before_hash"),
                    create_parents=bool(args.get("create_parents")),
                    approved=approval_granted,
                )
            elif name == "fs_patch":
                result = self.files.patch(
                    grant_id,
                    path,
                    replacements=list(args.get("replacements") or []),
                    expected_before_hash=args.get("expected_before_hash"),
                    approved=approval_granted,
                )
            elif name == "fs_copy":
                result = self.files.copy(
                    grant_id,
                    path,
                    str(args.get("destination_grant_id") or grant_id),
                    str(args.get("destination_path") or ""),
                    expected_destination_hash=args.get("expected_destination_hash"),
                    create_parents=bool(args.get("create_parents")),
                    approved=approval_granted,
                )
            elif name == "fs_move":
                result = self.files.move(
                    grant_id,
                    path,
                    str(args.get("destination_grant_id") or grant_id),
                    str(args.get("destination_path") or ""),
                    expected_source_hash=args.get("expected_source_hash"),
                    expected_destination_hash=args.get("expected_destination_hash"),
                    create_parents=bool(args.get("create_parents")),
                    approved=approval_granted,
                )
            elif name == "fs_trash":
                result = self.files.trash(grant_id, path, approved=approval_granted)
            elif name == "request_directory":
                request = self.directory_requests.create(
                    args, session_id=session_id, run_id=run_id
                )
                result = {
                    "receipt_type": "directory_requested",
                    "request_id": request["id"],
                    "status": "operator_action_required",
                    "settings_path": "#/settings",
                }
            elif name == "shell_exec":
                result = self.shell.exec(
                    grant_id=grant_id,
                    command=str(args.get("command") or ""),
                    cwd=str(args.get("cwd") or "."),
                    environment=args.get("environment")
                    if isinstance(args.get("environment"), dict)
                    else {},
                    background=bool(args.get("background")),
                    timeout_seconds=float(args.get("timeout_seconds") or 60),
                    approved=approval_granted,
                    approval_scope=approval_scope,
                    approval_fingerprint=approval_fingerprint,
                    actor=actor,
                )
                result["receipt_type"] = (
                    "shell_auto_read"
                    if decision.allowed
                    and decision.risk_class == RiskClass.VETTED_READ_ONLY_COMMAND
                    and decision.execution_target == "docker"
                    else "shell_approved"
                )
            elif name == "shell_poll":
                result = {
                    "receipt_type": "read",
                    **self.shell.poll(
                        str(args.get("task_id") or ""),
                        offset=int(args.get("offset") or 0),
                    ),
                }
            elif name == "shell_write_stdin":
                result = {
                    "receipt_type": "shell_approved",
                    **self.shell.write_stdin(
                        str(args.get("task_id") or ""), str(args.get("text") or "")
                    ),
                }
            elif name == "shell_kill":
                result = {
                    "receipt_type": "interrupted",
                    **self.shell.kill(str(args.get("task_id") or "")),
                }
            else:
                raise WorkspacePathError(f"unknown workspace tool {name}")
            ok = True
        except StaleWorkspaceWrite as exc:
            ok, result = (
                False,
                {"receipt_type": "stale", "status": "stale", "error": str(exc)},
            )
        except WorkspaceApprovalRequired as exc:
            ok, result = (
                False,
                {
                    "receipt_type": "denied",
                    "status": "approval_required",
                    "error": str(exc),
                },
            )
        except OSError:
            ok, result = (
                False,
                {
                    "receipt_type": "denied",
                    "status": "failed",
                    "error": "workspace operation failed",
                },
            )
        except KeyError:
            ok, result = (
                False,
                {
                    "receipt_type": "denied",
                    "status": "failed",
                    "error": "workspace item was not found",
                },
            )
        except (WorkspacePathError, GrantUnavailable, ValueError) as exc:
            ok, result = (
                False,
                {"receipt_type": "denied", "status": "failed", "error": str(exc)},
            )
        duration_ms = max(0, time_monotonic_ms() - started)
        receipt = self.audit.record(
            receipt_type=str(
                result.get("receipt_type") or ("read" if ok else "denied")
            ),
            tool=name,
            risk_class=decision.risk_class.value,
            decision="approved" if approval_granted else "auto" if ok else "denied",
            execution_target=str(
                result.get("execution_target") or decision.execution_target
            ),
            grant_id=str(args.get("grant_id") or "") or None,
            path=str(result.get("path") or args.get("path") or "") or None,
            command_fingerprint=str(
                result.get("command_fingerprint") or decision.command_fingerprint or ""
            )
            or None,
            before_hash=result.get("before_hash"),
            after_hash=result.get("after_hash"),
            actor=actor,
            session_id=session_id,
            run_id=run_id,
            task_id=result.get("task_id"),
            status=str(result.get("status") or ("succeeded" if ok else "failed")),
            duration_ms=duration_ms,
            exit_code=result.get("exit_code"),
            truncated=result.get("truncated"),
            summary=f"{name} {'completed' if ok else 'did not run'}",
        )
        result["receipt_id"] = receipt["id"]
        return ok, result

    def diagnostics(self) -> dict[str, Any]:
        docker = self.shell.docker.diagnostics()
        return {
            "docker": docker,
            "execution_target": "docker" if docker["available"] else "host_fallback",
            "host_fallback_enabled": True,
            "grants": self.grants.list_active(),
            "directory_requests": self.directory_requests.pending(),
            "host_approvals": [
                item
                for item in self.shell.approvals.list_all()
                if item.get("revoked_at") is None
            ],
            "tasks": self.shell.tasks.list(),
        }

    def record_permission_decision(self, item: dict[str, Any]) -> dict[str, Any] | None:
        name = str(item.get("name") or "")
        if name not in WORKSPACE_TOOL_NAMES:
            return None
        arguments = (
            dict(item.get("arguments") or {})
            if isinstance(item.get("arguments"), dict)
            else {}
        )
        try:
            outcome = self._decide(name, arguments)
        except Exception:
            outcome = RuntimeDecision(
                False,
                False,
                "workspace decision unavailable",
                RiskClass.BOUNDARY_EXPANSION,
                "none",
            )
        decision = str(item.get("decision") or "none")
        return self.audit.record(
            receipt_type="denied" if decision == "deny" else "permission_allowed",
            tool=name,
            risk_class=outcome.risk_class.value,
            decision=decision,
            execution_target=outcome.execution_target,
            grant_id=str(arguments.get("grant_id") or "") or None,
            path=str(arguments.get("path") or "") or None,
            command_fingerprint=outcome.command_fingerprint,
            actor=str(item.get("actor") or "") or None,
            session_id=str(item.get("session_id") or "") or None,
            run_id=str(item.get("run_id") or "") or None,
            status=str(item.get("execution_status") or "not_run"),
            summary=f"{name} permission {'denied' if decision == 'deny' else 'allowed'}",
        )

    def close(self) -> None:
        self.shell.close()


def time_monotonic_ms() -> int:
    import time

    return int(time.monotonic() * 1000)
