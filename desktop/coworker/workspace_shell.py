"""Docker-first workspace shell with explicit, exact host fallback authority."""

from __future__ import annotations

import json
import os
import re
import signal
import shlex
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coworker.workspace import GrantUnavailable, WorkspaceGrantStore
from coworker.workspace_files import (
    StaleWorkspaceWrite,
    WorkspaceApprovalRequired,
    WorkspaceFilesystem,
    WorkspacePathError,
    is_protected_workspace_path,
)
from coworker.workspace_policy import (
    HostApprovalStore,
    RiskClass,
    classify_shell,
    command_fingerprint,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ShellDecision:
    allowed: bool
    needs_approval: bool
    reason: str
    risk_class: RiskClass
    execution_target: str
    fingerprint: dict[str, Any]


class ShellTaskStore:
    VERSION = 1

    def __init__(self, state_root: str | Path, *, reconcile: bool = False) -> None:
        self.state_root = Path(state_root).expanduser()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.path = self.state_root / "shell_tasks.json"
        self._lock = threading.RLock()
        self.reconciled = self._reconcile() if reconcile else []

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": self.VERSION, "tasks": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": self.VERSION, "tasks": []}
        if (
            not isinstance(data, dict)
            or data.get("version") != self.VERSION
            or not isinstance(data.get("tasks"), list)
        ):
            return {"version": self.VERSION, "tasks": []}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        fd, raw_path = tempfile.mkstemp(
            prefix=".shell-tasks-", suffix=".tmp", dir=self.state_root
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

    def put(self, task: dict[str, Any]) -> dict[str, Any]:
        safe = {
            key: task.get(key)
            for key in (
                "task_id",
                "grant_id",
                "status",
                "execution_target",
                "command_summary",
                "command_fingerprint",
                "process_id",
                "process_group_id",
                "process_marker",
                "container_name",
                "started_at",
                "finished_at",
                "duration_ms",
                "exit_code",
                "truncated",
                "error",
            )
            if key in task
        }
        with self._lock:
            data = self._load()
            index = next(
                (
                    index
                    for index, item in enumerate(data["tasks"])
                    if item.get("task_id") == safe.get("task_id")
                ),
                None,
            )
            if index is None:
                data["tasks"].append(safe)
            else:
                data["tasks"][index] = safe
            self._save(data)
        return dict(safe)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._load()["tasks"]]

    def get(self, task_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.list() if item.get("task_id") == task_id),
            None,
        )

    def _reconcile(self) -> list[dict[str, Any]]:
        reconciled: list[dict[str, Any]] = []
        with self._lock:
            data = self._load()
            changed = False
            for index, raw in enumerate(data["tasks"]):
                if raw.get("status") != "running":
                    continue
                item = dict(raw)
                item.update(
                    {
                        "status": "interrupted",
                        "finished_at": _now(),
                        "exit_code": None,
                        "error": "Outcome unknown after Sourcecado restart.",
                    }
                )
                data["tasks"][index] = item
                reconciled.append(dict(item))
                changed = True
            if changed:
                self._save(data)
        return reconciled


class DockerSandbox:
    """Creates hardened session containers without mounting implicit host state."""

    def __init__(
        self,
        *,
        docker_binary: str | None = "docker",
        image: str = "python:3.13-slim",
    ) -> None:
        self.docker_binary = docker_binary
        self.image = image
        self._diagnostics_cache: tuple[float, dict[str, Any]] | None = None
        self._containers: dict[str, str] = {}
        self._lock = threading.RLock()

    def diagnostics(self) -> dict[str, Any]:
        if (
            self._diagnostics_cache
            and time.monotonic() - self._diagnostics_cache[0] < 5
        ):
            return dict(self._diagnostics_cache[1])
        binary = self.docker_binary and shutil_which(self.docker_binary)
        result: dict[str, Any] = {
            "cli_available": bool(binary),
            "daemon_available": False,
            "image_available": False,
            "available": False,
            "image": self.image,
            "network": "unrestricted",
        }
        if binary:
            try:
                daemon = subprocess.run(
                    [binary, "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
                server_version = daemon.stdout.strip()
                daemon_error = daemon.stderr.casefold()
                result["daemon_available"] = bool(
                    daemon.returncode == 0
                    and server_version
                    and "cannot connect" not in daemon_error
                    and "error during connect" not in daemon_error
                )
                if result["daemon_available"]:
                    result["server_version"] = server_version
                    image = subprocess.run(
                        [binary, "image", "inspect", self.image, "--format", "{{.Id}}"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                        check=False,
                    )
                    result["image_available"] = bool(
                        image.returncode == 0
                        and image.stdout.strip()
                        and "cannot connect" not in image.stderr.casefold()
                    )
            except (OSError, subprocess.TimeoutExpired):
                pass
        result["available"] = bool(
            result["cli_available"]
            and result["daemon_available"]
            and result["image_available"]
        )
        self._diagnostics_cache = (time.monotonic(), result)
        return dict(result)

    def build_create_command(
        self,
        *,
        container_name: str,
        primary: dict[str, Any],
        grants: list[dict[str, Any]],
        uid: int,
        gid: int,
        protected_roots: list[str | Path] | None = None,
    ) -> list[str]:
        if not self.docker_binary:
            raise RuntimeError("Docker CLI is unavailable")
        validated_grants = [self._validated_grant(grant) for grant in grants]
        primary = next(
            (
                grant
                for grant in validated_grants
                if grant.get("id") == primary.get("id")
            ),
            None,
        )
        if primary is None:
            raise RuntimeError("primary Docker grant is unavailable")
        protected = [
            Path(path).resolve(strict=True) for path in (protected_roots or [])
        ]

        def overlaps_protected(grant: dict[str, Any]) -> bool:
            root = Path(str(grant["path"])).resolve(strict=True)
            for protected_root in protected:
                try:
                    protected_root.relative_to(root)
                    return True
                except ValueError:
                    pass
                try:
                    root.relative_to(protected_root)
                    return True
                except ValueError:
                    pass
            return False

        if overlaps_protected(primary):
            raise RuntimeError("primary Docker mount overlaps protected state")
        command = [
            self.docker_binary,
            "create",
            "--name",
            container_name,
            "--label",
            "com.sourcecado.workspace-runtime=1",
            "--init",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "1g",
            "--cpus",
            "2",
            "--user",
            f"{uid}:{gid}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=268435456",
            "--env",
            "HOME=/tmp/sourcecado-home",
            "--env",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "--env",
            "LANG=C.UTF-8",
            "--workdir",
            "/workspace",
            "--volume",
            f"{primary['path']}:/workspace:rw",
        ]
        for grant in sorted(validated_grants, key=lambda item: str(item.get("id"))):
            if grant.get("id") == primary.get("id") or overlaps_protected(grant):
                continue
            mode = "rw" if grant.get("access") == "read_write" else "ro"
            command.extend(
                [
                    "--volume",
                    f"{grant['path']}:/grants/{grant['id']}:{mode}",
                ]
            )
        command.extend([self.image, "sleep", "infinity"])
        return command

    @staticmethod
    def _validated_grant(grant: dict[str, Any]) -> dict[str, Any]:
        stored = Path(str(grant.get("path") or ""))
        if not stored.is_absolute() or stored.is_symlink():
            raise RuntimeError("Docker grant filesystem identity changed")
        try:
            canonical = stored.resolve(strict=True)
            info = canonical.stat()
        except OSError as exc:
            raise RuntimeError("Docker grant filesystem identity changed") from exc
        identity = grant.get("filesystem_identity") or {}
        if (
            not canonical.is_dir()
            or info.st_dev != identity.get("device")
            or info.st_ino != identity.get("inode")
        ):
            raise RuntimeError("Docker grant filesystem identity changed")
        return {**grant, "path": str(canonical)}

    def ensure_container(
        self,
        primary: dict[str, Any],
        grants: list[dict[str, Any]],
        *,
        protected_roots: list[str | Path] | None = None,
    ) -> str:
        if not self.diagnostics()["available"]:
            raise RuntimeError("Docker sandbox is unavailable")
        grant_id = str(primary["id"])
        with self._lock:
            existing = self._containers.get(grant_id)
            if existing:
                return existing
            name = f"sourcecado-{os.getpid()}-{grant_id[-12:]}"
            create = self.build_create_command(
                container_name=name,
                primary=primary,
                grants=grants,
                uid=os.getuid(),
                gid=os.getgid(),
                protected_roots=protected_roots,
            )
            try:
                subprocess.run(
                    create,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=True,
                )
                subprocess.run(
                    [str(self.docker_binary), "start", name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError):
                try:
                    subprocess.run(
                        [str(self.docker_binary), "rm", "-f", name],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass
                raise WorkspacePathError(
                    "Docker sandbox could not start; retry requires a fresh approval"
                ) from None
            self._containers[grant_id] = name
            return name

    def exec_command(
        self,
        *,
        container: str,
        task_id: str,
        command: str,
        cwd: str,
        environment: dict[str, str],
    ) -> list[str]:
        invocation = [
            str(self.docker_binary),
            "exec",
            "-i",
            "--workdir",
            cwd,
        ]
        for key, value in sorted(environment.items()):
            invocation.extend(["--env", f"{key}={value}"])
        pid_path = f"/tmp/{task_id}.pid"
        wrapper = (
            f"echo $$ > {pid_path}; "
            "trap 'trap - TERM; kill -TERM 0' TERM; "
            f"/bin/bash --noprofile --norc -c {shlex.quote(command)}; "
            "status=$?; exit $status"
        )
        invocation.extend([container, "/bin/sh", "-c", wrapper])
        return invocation

    def kill_task(self, container: str, task_id: str) -> None:
        try:
            subprocess.run(
                [
                    str(self.docker_binary),
                    "exec",
                    container,
                    "/bin/sh",
                    "-c",
                    (
                        f"if test -f /tmp/{task_id}.pid; then "
                        f"pid=$(cat /tmp/{task_id}.pid); "
                        "kill -TERM -- -$pid 2>/dev/null || kill -TERM $pid 2>/dev/null || true; "
                        "i=0; while kill -0 $pid 2>/dev/null && test $i -lt 10; do sleep 0.1; i=$((i+1)); done; "
                        "kill -KILL -- -$pid 2>/dev/null || kill -KILL $pid 2>/dev/null || true; "
                        "fi"
                    ),
                ],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def remove_container(self, name: str) -> None:
        try:
            subprocess.run(
                [str(self.docker_binary), "rm", "-f", name],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def close_grant(self, grant_id: str) -> str | None:
        with self._lock:
            name = self._containers.pop(str(grant_id), None)
        if name is not None:
            self.remove_container(name)
        return name

    def close(self) -> None:
        with self._lock:
            names = list(self._containers.values())
            self._containers.clear()
        for name in names:
            self.remove_container(name)


def shutil_which(binary: str) -> str | None:
    import shutil

    return shutil.which(binary)


class _LiveTask:
    MAX_OUTPUT = 256 * 1024

    def __init__(
        self,
        *,
        metadata: dict[str, Any],
        process: subprocess.Popen[bytes],
        task_store: ShellTaskStore,
        container: str | None,
        on_finished: Any = None,
    ) -> None:
        self.metadata = metadata
        self.process = process
        self.task_store = task_store
        self.container = container
        self.on_finished = on_finished
        self._started_monotonic = time.monotonic()
        self._output = ""
        self._base_offset = 0
        self._lock = threading.RLock()
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        assert self.process.stdout is not None
        descriptor = self.process.stdout.fileno()
        while True:
            try:
                chunk = os.read(descriptor, 4096)
            except OSError:
                break
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            with self._lock:
                self._output += text
                if len(self._output) > self.MAX_OUTPUT:
                    overflow = len(self._output) - self.MAX_OUTPUT
                    self._output = self._output[overflow:]
                    self._base_offset += overflow
                    self.metadata["truncated"] = True
        return_code = self.process.wait()
        with self._lock:
            if self.metadata.get("status") == "running":
                self.metadata["status"] = "succeeded" if return_code == 0 else "failed"
            self.metadata["exit_code"] = return_code
            self.metadata["finished_at"] = _now()
            self.metadata["duration_ms"] = int(
                (time.monotonic() - self._started_monotonic) * 1000
            )
            self.task_store.put(self.metadata)
            if self.on_finished is not None:
                self.on_finished(dict(self.metadata))

    def poll(self, offset: int) -> dict[str, Any]:
        with self._lock:
            start = max(0, int(offset))
            truncated = start < self._base_offset or bool(
                self.metadata.get("truncated")
            )
            effective = max(start, self._base_offset)
            output = self._output[effective - self._base_offset :]
            next_offset = self._base_offset + len(self._output)
            return {
                **self.metadata,
                "output": output,
                "next_offset": next_offset,
                "truncated": truncated,
            }


class ShellRuntime:
    _ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
    _BLOCKED_ENV = frozenset(
        {"BASH_ENV", "ENV", "HOME", "PATH", "SHELL", "PROMPT_COMMAND", "PYTHONPATH"}
    )

    def __init__(
        self,
        *,
        state_root: str | Path,
        grants: WorkspaceGrantStore,
        files: WorkspaceFilesystem,
        docker: DockerSandbox | None = None,
        on_task_finished: Any = None,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve(strict=True)
        self.grants = grants
        self.files = files
        self.docker = docker or DockerSandbox()
        self.approvals = HostApprovalStore(self.state_root)
        self.tasks = ShellTaskStore(self.state_root)
        self.on_task_finished = on_task_finished
        self._live: dict[str, _LiveTask] = {}
        self._lock = threading.RLock()
        self._reconcile_existing_tasks()

    def _reconcile_existing_tasks(self) -> None:
        reconciled: list[dict[str, Any]] = []
        for task in self.tasks.list():
            if task.get("status") != "running":
                continue
            target = task.get("execution_target")
            if target == "host":
                self._terminate_persisted_host_task(task)
            elif target == "docker":
                container = str(task.get("container_name") or "")
                if container:
                    self.docker.kill_task(container, str(task.get("task_id") or ""))
                    self.docker.remove_container(container)
            item = {
                **task,
                "status": "interrupted",
                "finished_at": _now(),
                "exit_code": None,
                "error": "Outcome unknown after Sourcecado restart.",
            }
            self.tasks.put(item)
            reconciled.append(item)
            if self.on_task_finished is not None:
                self.on_task_finished(dict(item))
        self.tasks.reconciled = reconciled

    @staticmethod
    def _terminate_persisted_host_task(task: dict[str, Any]) -> None:
        try:
            process_id = int(task.get("process_id") or 0)
            process_group_id = int(task.get("process_group_id") or 0)
        except (TypeError, ValueError):
            return
        marker = str(task.get("process_marker") or "")
        if process_id <= 0 or process_group_id <= 0 or not marker:
            return
        identity_deadline = time.monotonic() + 0.5
        while True:
            try:
                probe = subprocess.run(
                    ["/bin/ps", "-ww", "-p", str(process_id), "-o", "command="],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return
            if probe.returncode != 0:
                return
            if marker in probe.stdout:
                break
            if time.monotonic() >= identity_deadline:
                return
            time.sleep(0.02)
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            try:
                os.kill(process_id, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _environment(self, environment: dict[str, Any] | None) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_key, raw_value in (environment or {}).items():
            key = str(raw_key)
            if (
                not self._ENV_KEY.match(key)
                or key in self._BLOCKED_ENV
                or key.startswith("LD_")
                or key.startswith("DYLD_")
            ):
                raise WorkspacePathError(f"environment key {key!r} is not allowed")
            value = str(raw_value)
            if len(value) > 4096:
                raise WorkspacePathError("environment value is too large")
            result[key] = value
        return result

    def _cwd(self, grant_id: str, cwd: str) -> tuple[dict[str, Any], Path, str]:
        grant, root, resolved = self.files.resolve(grant_id, cwd or ".")
        if not grant.get("allow_shell"):
            raise WorkspacePathError("workspace grant does not allow shell execution")
        if not resolved.is_dir():
            raise WorkspacePathError("shell cwd is not a directory")
        relative = resolved.relative_to(root).as_posix()
        container_cwd = "/workspace" + (f"/{relative}" if relative != "." else "")
        return grant, resolved, container_cwd

    def decide(
        self,
        *,
        grant_id: str,
        command: str,
        cwd: str,
        environment: dict[str, Any] | None,
    ) -> ShellDecision:
        _grant, host_cwd, _container_cwd = self._cwd(grant_id, cwd)
        safe_environment = self._environment(environment)
        diagnostics = self.docker.diagnostics()
        target = "docker" if diagnostics["available"] else "host"
        risk = classify_shell(command)
        if risk == RiskClass.VETTED_READ_ONLY_COMMAND:
            try:
                command_tokens = shlex.split(command, posix=True)
            except ValueError:
                command_tokens = []
            if any(
                is_protected_workspace_path(token)
                or any(marker in token for marker in ("*", "?", "["))
                for token in command_tokens[1:]
            ):
                risk = RiskClass.CONSEQUENTIAL_COMMAND
        if safe_environment and risk == RiskClass.VETTED_READ_ONLY_COMMAND:
            risk = RiskClass.CONSEQUENTIAL_COMMAND
        effective_environment = (
            self._host_environment(safe_environment)
            if target == "host"
            else self._container_environment(safe_environment)
        )
        fingerprint = command_fingerprint(
            command,
            cwd=host_cwd,
            environment=effective_environment,
            execution_target=target,
            resolve_executable=target == "host",
            shell_executable="/bin/bash" if target == "host" else None,
        )
        if safe_environment:
            fingerprint["permanent_eligible"] = False
        if target == "docker" and risk == RiskClass.VETTED_READ_ONLY_COMMAND:
            return ShellDecision(
                True,
                False,
                "vetted read-only command in the Docker workspace",
                risk,
                target,
                fingerprint,
            )
        if target == "host" and self.approvals.allows(fingerprint):
            return ShellDecision(
                True,
                False,
                "exact permanent host approval matched",
                risk,
                target,
                fingerprint,
            )
        reason = (
            "Docker is unavailable. This command is not sandboxed and requires approval."
            if target == "host"
            else "This command may write, execute code, or use the network and requires approval."
        )
        return ShellDecision(False, True, reason, risk, target, fingerprint)

    def approval_resource(
        self,
        *,
        grant_id: str,
        command: str,
        cwd: str,
        environment: dict[str, Any] | None,
    ) -> dict[str, Any]:
        decision = self.decide(
            grant_id=grant_id,
            command=command,
            cwd=cwd,
            environment=environment,
        )
        return {
            "kind": "shell_command",
            "execution_target": decision.execution_target,
            "command_summary": decision.fingerprint["command_summary"],
            "command_display": decision.fingerprint["command_display"],
            "environment_keys": sorted(self._environment(environment)),
            "cwd": decision.fingerprint["cwd"],
            "fingerprint": decision.fingerprint["digest"],
            "unsandboxed": decision.execution_target == "host",
            "permanent_eligible": bool(decision.fingerprint.get("permanent_eligible")),
        }

    @staticmethod
    def _host_environment(explicit: dict[str, str]) -> dict[str, str]:
        baseline = {
            "HOME": str(Path.home()),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": tempfile.gettempdir(),
        }
        return {**baseline, **explicit}

    @staticmethod
    def _container_environment(explicit: dict[str, str]) -> dict[str, str]:
        return {
            "HOME": "/tmp/sourcecado-home",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            **explicit,
        }

    def exec(
        self,
        *,
        grant_id: str,
        command: str,
        cwd: str = ".",
        environment: dict[str, Any] | None = None,
        background: bool = False,
        timeout_seconds: float = 60,
        approved: bool = False,
        approval_scope: str = "once",
        approval_fingerprint: str | None = None,
        actor: str = "operator",
    ) -> dict[str, Any]:
        grant, host_cwd, container_cwd = self._cwd(grant_id, cwd)
        safe_environment = self._environment(environment)
        decision = self.decide(
            grant_id=grant_id,
            command=command,
            cwd=cwd,
            environment=safe_environment,
        )
        if decision.needs_approval and not approved:
            raise WorkspaceApprovalRequired(decision.reason)
        if decision.needs_approval and approved:
            if not approval_fingerprint:
                raise WorkspaceApprovalRequired(
                    "approved shell execution is missing its exact fingerprint"
                )
            if approval_fingerprint != decision.fingerprint["digest"]:
                raise StaleWorkspaceWrite(
                    "shell command or referenced code changed after approval"
                )
        if (
            approved
            and approval_scope == "always"
            and decision.execution_target == "host"
        ):
            if not decision.fingerprint.get("permanent_eligible"):
                raise WorkspaceApprovalRequired(
                    "This command is not eligible for permanent approval; use allow once."
                )
            self.approvals.allow_always(decision.fingerprint, actor=actor)
        task_id = f"shell_{uuid.uuid4().hex}"
        process_marker = f"sourcecado-task-{task_id}"
        container = None
        if decision.execution_target == "docker":
            mount_grants = []
            for item in self.grants.list_active():
                try:
                    mount_grants.append(self.grants.require(item["id"]))
                except GrantUnavailable:
                    if item["id"] == grant_id:
                        raise
            container = self.docker.ensure_container(
                grant,
                mount_grants,
                protected_roots=[self.state_root],
            )
            invocation = self.docker.exec_command(
                container=container,
                task_id=task_id,
                command=command,
                cwd=container_cwd,
                environment=safe_environment,
            )
            process_environment = None
            process_cwd = None
            new_session = False
        else:
            supervised_command = (
                "_sourcecado_parent=$PPID\n"
                "_sourcecado_group=$(/bin/ps -p $$ -o pgid= | tr -d ' ')\n"
                "(while kill -0 $_sourcecado_parent 2>/dev/null; do sleep 0.2; done; "
                "kill -TERM -- -$_sourcecado_group 2>/dev/null; sleep 0.5; "
                "kill -KILL -- -$_sourcecado_group 2>/dev/null) &\n"
                "_sourcecado_watchdog=$!\n"
                f"{command}\n"
                "_sourcecado_status=$?\n"
                "kill $_sourcecado_watchdog 2>/dev/null || true\n"
                "wait $_sourcecado_watchdog 2>/dev/null || true\n"
                "exit $_sourcecado_status"
            )
            invocation = [
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                supervised_command,
                process_marker,
            ]
            process_environment = self._host_environment(safe_environment)
            process_cwd = host_cwd
            new_session = True
        process = subprocess.Popen(
            invocation,
            cwd=process_cwd,
            env=process_environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=new_session,
        )
        metadata = {
            "task_id": task_id,
            "grant_id": grant_id,
            "status": "running",
            "execution_target": decision.execution_target,
            "command_summary": decision.fingerprint["command_summary"],
            "command_fingerprint": decision.fingerprint["digest"],
            "process_id": process.pid,
            "process_group_id": (
                process.pid if decision.execution_target == "host" else None
            ),
            "process_marker": (
                process_marker if decision.execution_target == "host" else None
            ),
            "container_name": container,
            "started_at": _now(),
            "exit_code": None,
            "truncated": False,
        }
        self.tasks.put(metadata)
        live = _LiveTask(
            metadata=metadata,
            process=process,
            task_store=self.tasks,
            container=container,
            on_finished=self.on_task_finished,
        )
        with self._lock:
            self._live[task_id] = live
        if background:
            return {
                **metadata,
                "unsandboxed": decision.execution_target == "host",
            }
        deadline = max(0.1, min(float(timeout_seconds), 300.0))
        try:
            process.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            self.kill(task_id)
        live._reader.join(timeout=2)
        return {
            **live.poll(0),
            "unsandboxed": decision.execution_target == "host",
        }

    def poll(self, task_id: str, *, offset: int = 0) -> dict[str, Any]:
        with self._lock:
            live = self._live.get(task_id)
        if live is not None:
            return live.poll(offset)
        stored = self.tasks.get(task_id)
        if stored is None:
            raise KeyError(task_id)
        return {**stored, "output": "", "next_offset": max(0, int(offset))}

    def write_stdin(self, task_id: str, text: str) -> dict[str, Any]:
        with self._lock:
            live = self._live.get(task_id)
        if live is None or live.metadata.get("status") != "running":
            raise WorkspacePathError("shell task is not running")
        if live.process.stdin is None:
            raise WorkspacePathError("shell task stdin is unavailable")
        live.process.stdin.write(str(text).encode("utf-8"))
        live.process.stdin.flush()
        return {
            "task_id": task_id,
            "status": "running",
            "bytes_written": len(text.encode()),
        }

    def kill(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            live = self._live.get(task_id)
        if live is None:
            stored = self.tasks.get(task_id)
            if stored is None:
                raise KeyError(task_id)
            return stored
        with live._lock:
            if live.metadata.get("status") == "running":
                live.metadata["status"] = "cancelled"
        if live.container:
            self.docker.kill_task(live.container, task_id)
            live.process.terminate()
        else:
            try:
                os.killpg(live.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            live.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            if live.container:
                live.process.kill()
            else:
                try:
                    os.killpg(live.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            live.process.wait(timeout=1)
        live._reader.join(timeout=1)
        return live.poll(0)

    def stop_for_grant(self, grant_id: str) -> list[str]:
        with self._lock:
            task_ids = [
                task_id
                for task_id, task in self._live.items()
                if task.metadata.get("grant_id") == grant_id
                and task.metadata.get("status") == "running"
            ]
        for task_id in task_ids:
            self.kill(task_id)
        return task_ids

    def close(self) -> None:
        with self._lock:
            task_ids = [
                task_id
                for task_id, task in self._live.items()
                if task.metadata.get("status") == "running"
            ]
        for task_id in task_ids:
            self.kill(task_id)
        self.docker.close()
