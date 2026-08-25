# Club

Local sidecar and window for Fisher. One operator, one Google login, one weekly job.

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
