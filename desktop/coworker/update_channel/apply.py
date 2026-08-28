"""Applying one update, and putting the last usable version back when it fails.

The order of the stages is the design. Everything that can fail without
consequence happens before anything is touched:

    verify -> drain -> compatibility -> stage -> backup -> migrate -> activate
    -> health

Verification, the drain gate, the compatibility read, and unpacking the archive
all change nothing, so a failure in any of them leaves the running installation
exactly as it was. Only after all four pass does the update take a backup and
begin to move.

Migrating before activating is deliberate. The migration registry that runs is
this build's, so the state it produces is state this build already understands:
a crash between the migration and the bundle swap leaves a consistent system,
and a failure at the migration step leaves the old bundle untouched with only
the state to restore. The reverse order would open a window where a new bundle
sits over state it has not migrated yet.

State compatibility and backup are `coworker/migrations.py`'s job, and this
module calls it rather than repeating it. What this module adds is the one
thing that registry cannot know about: whether an update is safe to start at
all. That is `drain`, and it is the reason this file does not contain the
obvious "stop, swap, start" sequence.

One gap in the registry is compensated for here rather than worked around.
`migrations.restore_backup` restores store contents but not `state_versions.json`,
which is where JSONL logs, opaque files, and config documents record their
version. Rolling back without it would leave those stores' recorded versions
ahead of their restored contents, so the updater snapshots that file into the
backup directory before it migrates and puts it back when it rolls back. The
registry itself should do this; see `docs/update-channel.md`.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from coworker import migrations
from coworker.update_channel import drain as drain_module
from coworker.update_channel.drain import DEFAULT_POLL, DEFAULT_TIMEOUT, DrainBlocker
from coworker.update_channel.manifest import (
    BoundManifest,
    BuildIdentity,
    Refusal,
    verify_manifest,
)
from coworker.update_channel.redaction import registered_secrets, safe_text

STAGING_DIR_NAME = ".sourcecado-update"
PREVIOUS_SUFFIX = ".previous"
RUN_STORE_NAME = "agent_runs.db"
VERSIONS_SNAPSHOT = "pre-update-state-versions.json"
SIDECAR_RELATIVE = Path(
    "Contents/Resources/resources/sourcecado-sidecar/sourcecado-sidecar"
)
HEALTH_TIMEOUT = 40.0


class UpdateStage(StrEnum):
    """How far the update got. A refusal names the gate that stopped it."""

    VERIFY = "verify"
    DRAIN = "drain"
    COMPATIBILITY = "compatibility"
    STAGE = "stage"
    BACKUP = "backup"
    MIGRATE = "migrate"
    ACTIVATE = "activate"
    HEALTH = "health"
    DONE = "done"


class UpdateStatus(StrEnum):
    APPLIED = "applied"
    # Stopped before anything was touched.
    REFUSED = "refused"
    # Stopped by active work. Try again when it drains.
    BLOCKED = "blocked"
    # Something changed and was put back.
    ROLLED_BACK = "rolled_back"
    # Something changed and could not be put back. Loud on purpose.
    FAILED = "failed"


@dataclass(frozen=True)
class Installation:
    """Where this Sourcecado lives and what it is."""

    identity: BuildIdentity
    bundle_path: Path
    state_root: Path

    @property
    def previous_path(self) -> Path:
        return self.bundle_path.with_name(self.bundle_path.name + PREVIOUS_SUFFIX)

    @property
    def staging_root(self) -> Path:
        return self.bundle_path.parent / STAGING_DIR_NAME

    def at_version(self, version: str) -> "Installation":
        return replace(self, identity=replace(self.identity, version=str(version)))


@dataclass(frozen=True)
class UpdateOutcome:
    status: UpdateStatus
    stage: UpdateStage
    reason: str
    guidance: str
    from_version: str
    to_version: str | None = None
    backup_id: str | None = None
    blockers: tuple[DrainBlocker, ...] = ()
    # Runs that were open when the update ran and are safe to pick up after it:
    # they live in the run store and `agent_run_resume.restart()` classifies them
    # on the next launch. Recorded so the decision is auditable afterwards.
    continuable: tuple[str, ...] = ()
    migrated: tuple[str, ...] = ()
    restored: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is UpdateStatus.APPLIED

    def to_dict(self) -> dict[str, Any]:
        """The whole outcome as plain data. Nothing here carries a credential."""
        return {
            "status": str(self.status),
            "stage": str(self.stage),
            "reason": self.reason,
            "guidance": self.guidance,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "backup_id": self.backup_id,
            "continuable": list(self.continuable),
            "blockers": [
                {
                    "run_id": blocker.run_id,
                    "status": str(blocker.status),
                    "reason": blocker.reason,
                    "effect_id": blocker.effect_id,
                    "tool_name": blocker.tool_name,
                }
                for blocker in self.blockers
            ],
            "migrated": list(self.migrated),
            "restored": list(self.restored),
        }


_GUIDANCE: dict[str, str] = {
    str(Refusal.UNTRUSTED_KEY): (
        "This build does not trust the key that signed that update. Download the "
        "preview build again from the Sourcecado release page."
    ),
    str(Refusal.BAD_SIGNATURE): (
        "The download does not match what was signed. Delete it and download it "
        "again. Nothing on this Mac was changed."
    ),
    str(Refusal.ARTIFACT_DIGEST_MISMATCH): (
        "The download is not the file the manifest describes. Delete it and "
        "download it again."
    ),
    str(Refusal.CHANNEL_MISMATCH): (
        "That update is for a different release channel. A preview build only "
        "installs preview updates."
    ),
    "run_store_not_inspected": (
        "Sourcecado could not read its own run store, so it cannot tell whether "
        "work is in flight. Run Sourcecado Doctor and try again."
    ),
    "state_incompatible": (
        "Your local data is not in a state this update can move. Run Sourcecado "
        "Doctor and follow what it reports. Nothing was changed."
    ),
    "artifact_shape": (
        "The download did not contain a single application. Delete it and "
        "download it again."
    ),
    "backup_failed": (
        "Sourcecado could not write the backup it takes before changing your "
        "data, so it stopped. Free up disk space and try again."
    ),
}

_DRAIN_GUIDANCE = {
    drain_module.DrainStatus.ACTIVE_WORK: (
        "Sourcecado is still working. The update did not start. Let the current "
        "run finish and try again."
    ),
    drain_module.DrainStatus.UNSETTLED_EFFECT: (
        "A send has gone out and has not reported back yet, so nobody knows if "
        "it arrived. Sourcecado will not restart while that is true, because a "
        "restart could send it twice. Wait for the run to finish, then update."
    ),
    drain_module.DrainStatus.QUARANTINED_EFFECT: (
        "An external action is waiting for you to say what happened to it. "
        "Settle it in review, then update. Sourcecado will not decide it for you."
    ),
}


def _outcome(
    status: UpdateStatus,
    stage: UpdateStage,
    reason: str,
    installation: Installation,
    *,
    guidance: str = "",
    **extra: Any,
) -> UpdateOutcome:
    """Assemble the outcome, and scan the operator-facing sentence one last time.

    Call sites wrap the variable half of a message -- an exception string, a
    registry error -- in `safe_text` themselves, so a credential in there is
    withheld in place and the fixed sentence survives. This second pass exists
    for the call site that forgets: it withholds the whole sentence rather than
    letting it through, which is the direction this has to fail in.
    """
    text = guidance or _GUIDANCE.get(reason, "")
    return UpdateOutcome(
        status=status,
        stage=stage,
        reason=reason,
        guidance=safe_text(
            text,
            state_root=installation.state_root,
            registered=registered_secrets(installation.state_root),
        ),
        from_version=installation.identity.version,
        **extra,
    )


def _detail(text: Any, installation: Installation) -> str:
    """The variable half of a message, withheld on its own if it matches."""
    return safe_text(
        text,
        state_root=installation.state_root,
        registered=registered_secrets(installation.state_root),
    )


# --- the bundle on disk --------------------------------------------------


def _clear(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


def stage_artifact(installation: Installation, artifact_path: Path) -> Path:
    """Unpack the archive and return the single application it contains.

    Raises `ValueError` when the archive holds anything other than exactly one
    top-level directory. Content authenticity is already settled by the
    manifest's digest; this only refuses a shape the installer cannot place.
    """
    staging = installation.staging_root
    _clear(staging)
    staging.mkdir(parents=True, exist_ok=True)
    os.chmod(staging, migrations.DIR_MODE)
    shutil.unpack_archive(str(artifact_path), str(staging))
    entries = [item for item in staging.iterdir() if not item.name.startswith(".")]
    directories = [item for item in entries if item.is_dir()]
    if len(entries) != 1 or len(directories) != 1:
        raise ValueError(
            f"the archive holds {len(entries)} top-level entries; an update holds one"
        )
    return directories[0]


def activate(installation: Installation, staged: Path) -> None:
    """Put the new application where the old one is, keeping the old one aside.

    Two renames on one filesystem. Between them the application is briefly
    absent, which is the smallest window this can be reduced to without a
    filesystem that can swap two directories in one call.
    """
    _clear(installation.previous_path)
    if installation.bundle_path.exists():
        os.replace(installation.bundle_path, installation.previous_path)
    os.replace(staged, installation.bundle_path)


def restore_bundle(installation: Installation) -> bool:
    """Put the previous application back. False when there is nothing to put back."""
    if not installation.previous_path.exists():
        return False
    _clear(installation.bundle_path)
    os.replace(installation.previous_path, installation.bundle_path)
    return True


# --- the version manifest the registry does not back up ------------------


def _snapshot_versions(state_root: Path, backup: migrations.Backup) -> None:
    """Record `state_versions.json` as it stands, including not existing yet.

    Absence is as much a fact as a version number. A state directory that has
    never been migrated has no version manifest at all, and a rollback that
    left the migration's new one behind would report versions for stores whose
    contents had just been restored to an earlier shape.
    """
    live = state_root / migrations.MANIFEST_NAME
    payload: dict[str, Any] = {"present": live.is_file(), "document": None}
    if payload["present"]:
        try:
            payload["document"] = json.loads(live.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload["present"] = False
    target = backup.path / VERSIONS_SNAPSHOT
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(target, migrations.FILE_MODE)


def _restore_versions(state_root: Path, backup_id: str) -> None:
    source = state_root / migrations.BACKUPS_DIR_NAME / backup_id / VERSIONS_SNAPSHOT
    if not source.is_file():
        return
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    live = state_root / migrations.MANIFEST_NAME
    if not payload.get("present"):
        live.unlink(missing_ok=True)
        return
    live.write_text(
        json.dumps(payload.get("document") or {}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(live, migrations.FILE_MODE)


def _restore_state(state_root: Path, backup_id: str | None) -> tuple[str, ...]:
    if backup_id is None:
        return ()
    restored = migrations.restore_backup(state_root, backup_id)
    _restore_versions(state_root, backup_id)
    return tuple(restored.get("restored") or ())


# --- health --------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def sidecar_health_check(
    installation: Installation, *, timeout: float = HEALTH_TIMEOUT
) -> bool:
    """Launch the installed sidecar against throwaway state and ask if it is well.

    The state directory is a temporary one, never the operator's. A health check
    that wrote to real state would be a change the rollback could not undo.
    """
    import secrets as secrets_module

    binary = installation.bundle_path / SIDECAR_RELATIVE
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return False
    token = secrets_module.token_hex(16)
    scratch = tempfile.mkdtemp(prefix="sourcecado-update-health-")
    environment = dict(os.environ)
    environment["CLUB_STATE_DIR"] = scratch
    environment["CLUB_API_TOKEN"] = token
    environment.pop("CLUB_EXIT_WITH_PARENT", None)
    process = None
    port = 0
    try:
        deadline = time.monotonic() + timeout
        starts = 0
        while time.monotonic() < deadline:
            if process is None:
                if starts >= 3:
                    return False
                starts += 1
                port = _free_port()
                process = subprocess.Popen(
                    [str(binary), "--host", "127.0.0.1", "--port", str(port)],
                    cwd=tempfile.gettempdir(),
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            if process.poll() is not None:
                process.wait(timeout=1)
                process = None
                continue
            connection = None
            try:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", port, timeout=2
                )
                connection.request(
                    "GET", "/v1/health", headers={"X-Club-Token": token}
                )
                response = connection.getresponse()
                if response.status != 200:
                    # The port we reserved was taken by something else.
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    process = None
                    continue
                return json.loads(response.read()).get("status") == "ok"
            except (OSError, ValueError):
                time.sleep(0.25)
            finally:
                if connection is not None:
                    connection.close()
        return False
    finally:
        if process is not None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(scratch, ignore_errors=True)


# --- the update ----------------------------------------------------------


def apply_update(
    document: Any,
    *,
    installation: Installation,
    artifact_path: str | Path,
    trust: Any = None,
    runs: Any = None,
    health_check: Callable[[Installation], bool] = sidecar_health_check,
    drain_timeout: float = DEFAULT_TIMEOUT,
    drain_poll: float = DEFAULT_POLL,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: datetime | None = None,
) -> UpdateOutcome:
    """Install a verified update, or explain why this Mac is staying where it is."""
    artifact = Path(artifact_path)
    state_root = Path(installation.state_root)

    # 1. Authenticate. Nothing has been touched and nothing will be if this fails.
    verification = verify_manifest(
        document,
        installed=installation.identity,
        artifact_path=artifact,
        trust=trust,
    )
    if not verification.ok or verification.manifest is None:
        return _outcome(
            UpdateStatus.REFUSED,
            UpdateStage.VERIFY,
            str(verification.refusal),
            installation,
            guidance=_GUIDANCE.get(
                str(verification.refusal),
                "Sourcecado refused that update: "
                f"{_detail(verification.detail, installation)}. Nothing on this "
                "Mac was changed.",
            ),
        )
    bound: BoundManifest = verification.manifest

    # 2. Is now a safe moment? This is the gate that makes an updater safe on a
    #    machine that sends real mail. It reads; it never writes.
    blocked, continuable = _drain_gate(
        installation,
        bound,
        runs,
        state_root,
        drain_timeout=drain_timeout,
        drain_poll=drain_poll,
        sleep=sleep,
        clock=clock,
        now=now,
    )
    if blocked is not None:
        return blocked

    # The gate has passed, so the updater now owns the state directory. Closing
    # the run store here is not tidiness: migrating or restoring a SQLite file
    # underneath an open connection can corrupt it, and every step below this
    # line does one or the other. The application is not running at this point;
    # `docs/update-channel.md` records that precondition.
    _close_quietly(runs)

    # 3. Can this build's migration registry move the state? Its answer, not ours.
    plan = migrations.plan_migrations(state_root)
    if plan.blocked:
        detail = ", ".join(
            f"{item.store_id}: {item.status}" for item in plan.blockers
        )
        return _outcome(
            UpdateStatus.REFUSED,
            UpdateStage.COMPATIBILITY,
            "state_incompatible",
            installation,
            guidance=(
                f"{_GUIDANCE['state_incompatible']} Doctor will report: "
                f"{_detail(detail, installation)}."
            ),
            to_version=bound.version,
        )

    # 4. Unpack. Still nothing touched.
    try:
        staged = stage_artifact(installation, artifact)
    except (OSError, ValueError, shutil.ReadError) as exc:
        _clear(installation.staging_root)
        return _outcome(
            UpdateStatus.REFUSED,
            UpdateStage.STAGE,
            "artifact_shape",
            installation,
            guidance=f"{_GUIDANCE['artifact_shape']} ({_detail(exc, installation)})",
            to_version=bound.version,
        )

    # 5. Back up before the first change, when there is a change to make.
    backup: migrations.Backup | None = None
    if plan.pending:
        try:
            backup = migrations.create_backup(
                state_root,
                [spec.store_id for spec in migrations.REGISTRY],
                reason=f"update {installation.identity.version} to {bound.version}",
            )
            _snapshot_versions(state_root, backup)
        except (migrations.BackupFailed, OSError) as exc:
            _clear(installation.staging_root)
            return _outcome(
                UpdateStatus.REFUSED,
                UpdateStage.BACKUP,
                "backup_failed",
                installation,
                guidance=(
                    f"{_GUIDANCE['backup_failed']} ({_detail(exc, installation)})"
                ),
                to_version=bound.version,
            )

    return _install(
        installation,
        bound,
        plan=plan,
        backup=backup,
        staged=staged,
        health_check=health_check,
        state_root=state_root,
        continuable=continuable,
    )


def _close_quietly(runs: Any) -> None:
    if runs is None:
        return
    try:
        runs.close()
    except Exception:  # already closed, or never ours to close
        pass


def _drain_gate(
    installation: Installation,
    bound: BoundManifest,
    runs: Any,
    state_root: Path,
    *,
    drain_timeout: float,
    drain_poll: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    now: datetime | None,
) -> tuple[UpdateOutcome | None, tuple[str, ...]]:
    """Criterion 3, in one place.

    Returns the outcome that stops the update, or None plus the runs that are
    safe to pick up after it. An installation with no run store has nothing in
    flight. An installation with a run store the caller did not open is not the
    same thing, and it refuses rather than assuming.
    """
    if runs is None:
        if (state_root / RUN_STORE_NAME).exists():
            return (
                _outcome(
                    UpdateStatus.REFUSED,
                    UpdateStage.DRAIN,
                    "run_store_not_inspected",
                    installation,
                    to_version=bound.version,
                ),
                (),
            )
        return None, ()
    assessment = drain_module.wait_for_drain(
        runs,
        timeout=drain_timeout,
        poll=drain_poll,
        sleep=sleep,
        clock=clock,
        now=now,
    )
    if assessment.ready:
        return None, assessment.continuable
    return (
        _outcome(
            UpdateStatus.BLOCKED,
            UpdateStage.DRAIN,
            str(assessment.status),
            installation,
            guidance=_DRAIN_GUIDANCE[assessment.status],
            to_version=bound.version,
            blockers=assessment.blockers,
            continuable=assessment.continuable,
        ),
        assessment.continuable,
    )


def _install(
    installation: Installation,
    bound: BoundManifest,
    *,
    plan: migrations.MigrationPlan,
    backup: migrations.Backup | None,
    staged: Path,
    health_check: Callable[[Installation], bool],
    state_root: Path,
    continuable: tuple[str, ...] = (),
) -> UpdateOutcome:
    backup_id = backup.backup_id if backup is not None else None
    migrated: tuple[str, ...] = ()

    if plan.pending:
        outcome = migrations.apply_migrations(state_root, plan=plan, backup=backup)
        if outcome.blocked or outcome.error:
            restored = _restore_state(state_root, backup_id)
            _clear(installation.staging_root)
            return _outcome(
                UpdateStatus.ROLLED_BACK,
                UpdateStage.MIGRATE,
                "migration_failed",
                installation,
                guidance=(
                    "The update could not move your data, so Sourcecado put it "
                    f"back and stayed on {installation.identity.version}. "
                    f"({_detail(outcome.error, installation)})"
                ),
                to_version=bound.version,
                backup_id=backup_id,
                restored=restored,
            )
        migrated = tuple(step.store_id for step in outcome.applied)

    try:
        activate(installation, staged)
    except OSError as exc:
        restore_bundle(installation)
        restored = _restore_state(state_root, backup_id)
        _clear(installation.staging_root)
        return _outcome(
            UpdateStatus.ROLLED_BACK,
            UpdateStage.ACTIVATE,
            "activation_failed",
            installation,
            guidance=(
                "The new version could not be put in place, so Sourcecado "
                f"stayed on {installation.identity.version}. "
                f"({_detail(exc, installation)})"
            ),
            to_version=bound.version,
            backup_id=backup_id,
            restored=restored,
        )

    try:
        healthy = bool(health_check(installation.at_version(bound.version)))
        failure = ""
    except Exception as exc:  # a health check that raises is a health check that failed
        healthy = False
        failure = f" ({exc})"
    if not healthy:
        previous_restored = restore_bundle(installation)
        if not previous_restored:
            _clear(installation.bundle_path)
        restored = _restore_state(state_root, backup_id)
        _clear(installation.staging_root)
        if not previous_restored:
            return _outcome(
                UpdateStatus.FAILED,
                UpdateStage.HEALTH,
                "health_check_failed",
                installation,
                guidance=(
                    f"Version {bound.version} did not start correctly, and there "
                    "was no previous version to put back. Sourcecado is not "
                    "installed. Download it again and try a different build."
                    f"{_detail(failure, installation) if failure else ''}"
                ),
                to_version=bound.version,
                backup_id=backup_id,
                restored=restored,
            )
        return _outcome(
            UpdateStatus.ROLLED_BACK,
            UpdateStage.HEALTH,
            "health_check_failed",
            installation,
            guidance=(
                f"Version {bound.version} did not start correctly, so Sourcecado "
                f"put version {installation.identity.version} back and restored "
                f"your data.{_detail(failure, installation) if failure else ''}"
            ),
            to_version=bound.version,
            backup_id=backup_id,
            restored=restored,
        )

    _clear(installation.staging_root)
    return _outcome(
        UpdateStatus.APPLIED,
        UpdateStage.DONE,
        "applied",
        installation,
        guidance=(
            f"Sourcecado is now on version {bound.version}. The previous version "
            "is kept until the next update, so you can roll back."
        ),
        to_version=bound.version,
        backup_id=backup_id,
        migrated=migrated,
        continuable=continuable,
    )


def rollback(
    installation: Installation, *, backup_id: str | None = None
) -> UpdateOutcome:
    """Deliberately go back to the version this Mac was on before the last update."""
    state_root = Path(installation.state_root)
    if not installation.previous_path.exists():
        return _outcome(
            UpdateStatus.REFUSED,
            UpdateStage.ACTIVATE,
            "no_previous_version",
            installation,
            guidance=(
                "There is no previous version kept on this Mac, so there is "
                "nothing to roll back to. Download the version you want and "
                "install it."
            ),
        )
    restore_bundle(installation)
    restored: tuple[str, ...] = ()
    if backup_id is not None:
        try:
            restored = _restore_state(state_root, backup_id)
        except (migrations.BackupFailed, OSError, ValueError) as exc:
            return _outcome(
                UpdateStatus.FAILED,
                UpdateStage.MIGRATE,
                "state_not_restored",
                installation,
                guidance=(
                    "The previous version is back, but your data could not be "
                    f"restored from backup {backup_id}. Run Sourcecado Doctor "
                    f"before using it. ({_detail(exc, installation)})"
                ),
                backup_id=backup_id,
            )
    return _outcome(
        UpdateStatus.ROLLED_BACK,
        UpdateStage.DONE,
        "rolled_back",
        installation,
        guidance=(
            "Sourcecado is back on the version it was running before the last "
            "update, and your data was restored with it."
        ),
        backup_id=backup_id,
        restored=restored,
    )


def running_identity(*, channel: str, version: str) -> BuildIdentity:
    """Describe this build for verification: what it is, and what state it reaches."""
    import platform

    from coworker.update_channel.manifest import PRODUCT, registry_state_versions

    system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(
        platform.system(), platform.system().lower()
    )
    machine = {"arm64": "aarch64", "AMD64": "x86_64"}.get(
        platform.machine(), platform.machine()
    )
    return BuildIdentity(
        product=PRODUCT,
        channel=str(channel),
        version=str(version),
        platform=system,
        arch=machine,
        state_versions=registry_state_versions(),
    )


__all__ = [
    "Installation",
    "UpdateOutcome",
    "UpdateStage",
    "UpdateStatus",
    "activate",
    "apply_update",
    "restore_bundle",
    "rollback",
    "running_identity",
    "sidecar_health_check",
    "stage_artifact",
]
