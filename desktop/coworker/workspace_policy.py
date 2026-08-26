"""Risk classification and exact standing authority for workspace tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class RiskClass(StrEnum):
    READ = "read"
    REVERSIBLE_WRITE = "reversible_write"
    DESTRUCTIVE_WRITE = "destructive_write"
    VETTED_READ_ONLY_COMMAND = "vetted_read_only_command"
    CONSEQUENTIAL_COMMAND = "consequential_command"
    BOUNDARY_EXPANSION = "boundary_expansion"


_SHELL_OPERATORS = frozenset({";", "|", "&", ">", "<", "`", "$", "\n", "\r"})
_READ_ONLY_COMMANDS = frozenset({"pwd", "ls"})
_SCRIPT_INTERPRETERS = frozenset(
    {"bash", "sh", "zsh", "python", "python3", "node", "ruby", "perl"}
)
_PERMANENT_WRAPPERS = frozenset(
    {"env", "command", "xargs", "sudo", "nice", "time", "nohup"}
)
_INLINE_CODE_FLAGS = frozenset({"-c", "--command", "-e", "--eval"})
_SECRET_OPTION = re.compile(
    r"(?i)^--?(?:api[-_]?key|token|password|secret|authorization)(?:=|$)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)^(?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY))=(.*)$"
)
_SHELL_BUILTINS = frozenset({"pwd", "printf", "echo", "true", "false"})


def classify_shell(command: str) -> RiskClass:
    text = str(command or "").strip()
    if not text or any(operator in text for operator in _SHELL_OPERATORS):
        return RiskClass.CONSEQUENTIAL_COMMAND
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return RiskClass.CONSEQUENTIAL_COMMAND
    if not tokens:
        return RiskClass.CONSEQUENTIAL_COMMAND
    if "/" in tokens[0] or "\\" in tokens[0]:
        return RiskClass.CONSEQUENTIAL_COMMAND
    binary = Path(tokens[0]).name
    if binary == "git":
        return RiskClass.CONSEQUENTIAL_COMMAND
    if binary not in _READ_ONLY_COMMANDS:
        return RiskClass.CONSEQUENTIAL_COMMAND
    if binary == "ls" and any(
        argument in {"-R", "--recursive"} for argument in tokens[1:]
    ):
        return RiskClass.CONSEQUENTIAL_COMMAND
    return RiskClass.VETTED_READ_ONLY_COMMAND


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _display_command(command: str) -> tuple[str, bool]:
    try:
        tokens = shlex.split(str(command), posix=True)
    except ValueError:
        return "Command cannot be displayed safely", True
    redacted = False
    display: list[str] = []
    redact_next = False
    for token in tokens:
        if redact_next:
            display.append("[REDACTED]")
            redact_next = False
            redacted = True
            continue
        if _SECRET_OPTION.match(token):
            if "=" in token:
                display.append(token.split("=", 1)[0] + "=[REDACTED]")
                redacted = True
            else:
                display.append(token)
                redact_next = True
            continue
        if _SECRET_ASSIGNMENT.match(token):
            display.append(token.split("=", 1)[0] + "=[REDACTED]")
            redacted = True
            continue
        display.append(token)
    return shlex.join(display), redacted


def command_fingerprint(
    command: str,
    *,
    cwd: str | Path,
    environment: dict[str, str] | None,
    execution_target: str,
    resolve_executable: bool = True,
    shell_executable: str | Path | None = None,
) -> dict[str, Any]:
    resolved_cwd = Path(cwd).expanduser().resolve(strict=True)
    safe_environment = {
        str(key): str(value) for key, value in sorted((environment or {}).items())
    }
    try:
        tokens = shlex.split(str(command), posix=True)
    except ValueError:
        tokens = []
    binary = Path(tokens[0]).name if tokens else "command"
    argument_count = max(0, len(tokens) - 1)
    command_summary = (
        f"{binary} · {argument_count} argument{'s' if argument_count != 1 else ''}"
    )
    path_value = safe_environment.get("PATH") or os.defpath
    executable_path = (
        shutil.which(tokens[0], path=path_value)
        if tokens and resolve_executable
        else None
    )
    executable = None
    if executable_path:
        resolved_executable = Path(executable_path).resolve(strict=True)
        executable = {
            "path": str(resolved_executable),
            "sha256": _hash_file(resolved_executable),
        }
    scripts: list[dict[str, str]] = []
    for token in tokens[1:]:
        if token.startswith("-"):
            continue
        candidate = Path(token).expanduser()
        if not candidate.is_absolute():
            candidate = resolved_cwd / candidate
        try:
            candidate = candidate.resolve(strict=True)
        except OSError:
            continue
        if candidate.is_file():
            scripts.append({"path": str(candidate), "sha256": _hash_file(candidate)})
    scripts.sort(key=lambda item: item["path"])
    shell_executor = None
    if shell_executable is not None:
        resolved_shell = Path(shell_executable).resolve(strict=True)
        shell_executor = {
            "path": str(resolved_shell),
            "sha256": _hash_file(resolved_shell),
        }
    command_display, command_was_redacted = _display_command(command)
    has_shell_syntax = any(
        marker in str(command) for marker in (*_SHELL_OPERATORS, "*", "?", "[", "]")
    )
    wrapper = binary in _PERMANENT_WRAPPERS
    inline_code = binary in _SCRIPT_INTERPRETERS and any(
        token in _INLINE_CODE_FLAGS for token in tokens[1:]
    )
    missing_script = (
        binary in _SCRIPT_INTERPRETERS
        and not scripts
        and not all(token.startswith("-") for token in tokens[1:])
    )
    resolvable_binary = executable is not None or binary in _SHELL_BUILTINS
    arguments_safe = True
    for token in tokens[1:]:
        if token.startswith("-"):
            continue
        candidate = Path(token).expanduser()
        if not candidate.is_absolute():
            candidate = resolved_cwd / candidate
        if not candidate.exists():
            arguments_safe = False
            break
    permanent_eligible = bool(
        tokens
        and resolve_executable
        and resolvable_binary
        and not wrapper
        and not inline_code
        and not missing_script
        and not has_shell_syntax
        and not command_was_redacted
        and arguments_safe
    )
    environment_fingerprint = _digest(safe_environment)
    components = {
        "command": str(command),
        "cwd": str(resolved_cwd),
        "environment_fingerprint": environment_fingerprint,
        "execution_target": str(execution_target),
        "executable": executable,
        "shell_executable": shell_executor,
        "scripts": scripts,
    }
    return {
        "digest": _digest(components),
        "command_summary": command_summary,
        "command_display": command_display,
        "cwd": str(resolved_cwd),
        "environment_fingerprint": environment_fingerprint,
        "execution_target": str(execution_target),
        "executable": executable,
        "shell_executable": shell_executor,
        "scripts": scripts,
        "permanent_eligible": permanent_eligible,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()


class HostApprovalStore:
    VERSION = 1

    def __init__(self, state_root: str | Path) -> None:
        self.state_root = Path(state_root).expanduser()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.path = self.state_root / "host_command_approvals.json"
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": self.VERSION, "approvals": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": self.VERSION, "approvals": []}
        if (
            not isinstance(data, dict)
            or data.get("version") != self.VERSION
            or not isinstance(data.get("approvals"), list)
        ):
            return {"version": self.VERSION, "approvals": []}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        fd, raw_path = tempfile.mkstemp(
            prefix=".host-approvals-", suffix=".tmp", dir=self.state_root
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

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._load()["approvals"]]

    def allows(self, fingerprint: dict[str, Any]) -> bool:
        if not fingerprint.get("permanent_eligible"):
            return False
        digest = str(fingerprint.get("digest") or "")
        return any(
            item.get("fingerprint") == digest and item.get("revoked_at") is None
            for item in self.list_all()
        )

    def allow_always(
        self, fingerprint: dict[str, Any], *, actor: str
    ) -> dict[str, Any]:
        if not fingerprint.get("permanent_eligible"):
            raise ValueError("command is not eligible for permanent approval")
        stamp = _now()
        approval = {
            "id": f"host_approval_{uuid.uuid4().hex}",
            "fingerprint": str(fingerprint["digest"]),
            "command_summary": str(fingerprint["command_summary"]),
            "cwd": str(fingerprint["cwd"]),
            "environment_fingerprint": str(fingerprint["environment_fingerprint"]),
            "execution_target": "host",
            "executable": fingerprint.get("executable"),
            "shell_executable": fingerprint.get("shell_executable"),
            "scripts": list(fingerprint.get("scripts") or []),
            "permanent_eligible": bool(fingerprint.get("permanent_eligible")),
            "actor": str(actor or "operator"),
            "created_at": stamp,
            "revoked_at": None,
        }
        with self._lock:
            data = self._load()
            existing = next(
                (
                    item
                    for item in data["approvals"]
                    if item.get("fingerprint") == approval["fingerprint"]
                    and item.get("revoked_at") is None
                ),
                None,
            )
            if existing is not None:
                return dict(existing)
            data["approvals"].append(approval)
            self._save(data)
        return dict(approval)

    def revoke(self, approval_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            for index, raw in enumerate(data["approvals"]):
                if raw.get("id") != approval_id:
                    continue
                item = dict(raw)
                if item.get("revoked_at") is None:
                    item["revoked_at"] = _now()
                    data["approvals"][index] = item
                    self._save(data)
                return item
        raise KeyError(approval_id)
