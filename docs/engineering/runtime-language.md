# Sourcecado desktop runtime

Runtime-specific language for the local backend and window. Product language lives in [the domain guide](../domain.md) and takes precedence if terminology conflicts.

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

**Workspace Grant**, **Workspace Execution Grant**, **Host Shell Approval**:
Removed in migration slice S23. The hosted service has no local file tools and no shell. See [ADR 0002](adr/0002-sourcecado-workspace-runtime.md) for the history.
