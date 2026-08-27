# Sourcecado desktop runtime

Runtime-specific language for the local sidecar and window. Product/domain language lives in the root `CONTEXT.md`; that file wins if terminology conflicts.

## Language

**Tick**:
A due-job sweep. Every job whose next run is at or before now fires once. The 30s loop and `POST /v1/schedule/tick` are ticks.
_Avoid_: poll, cron fire

**Run now**:
A manual fire of one named job, even if it is not due. It does not move the weekly slot.
_Avoid_: tick, force tick, catch-up

**Weekly slot**:
The next Monday 09:00 America/Los_Angeles on a job. Only a tick consumes it.
_Avoid_: interval, now+7

**Sched session**:
The transcript for a job run, keyed `sched-{id}`. It never appears on the session rail.
_Avoid_: scheduled chat, hidden chat

**Local state directory**:
The directory containing Sourcecado's local databases, tokens, and connector state. It defaults to `~/.config/club/` and can be overridden with `CLUB_STATE_DIR` for isolated runs.
_Avoid_: repository data directory, shared tenant storage

**Approval**:
A durable decision attached to one concrete external or credit-sensitive action, such as enriching a person or sending a reviewed Gmail message. Approval is not a blanket grant.
_Avoid_: auto-send permission, silent confirmation

**Workspace Grant**:
Operator-approved authority over one canonical local directory. A grant is read-only or read-write, is addressed by an opaque ID, and can be revoked. Read-write authority covers reversible, version-checked typed changes; it does not silently authorize trash, conflicting overwrites, protected files, or boundary expansion.
_Avoid_: folder access, blanket filesystem permission

**Workspace Execution Grant**:
A read-write Workspace Grant that also authorizes shell execution inside the isolated workspace runtime. It never means access to the rest of the host account. Docker is the preferred target; direct-host execution remains a separate approval when isolation is unavailable.
_Avoid_: shell permission, full computer access

**Host Shell Approval**:
Authority for one exact direct-host command fingerprint when Docker is unavailable. The fingerprint binds command, resolved working directory, sanitized environment, execution target, executable, and referenced script hashes. An allow-always decision can persist, remains visible and revocable, and stops matching when any bound component changes.
_Avoid_: command allowlist, trusted shell
