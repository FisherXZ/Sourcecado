"""Criterion 3: when an update may proceed, and what it must never do to get there.

The obvious updater stops the app, swaps the bundle, and starts it again. That
is the wrong shape here. If a run has dispatched a Gmail send and does not yet
know the outcome, killing the process turns a knowable outcome into a
permanently ambiguous one; a restart that "resumes" it can put a second real
email in front of a real person.

So the drain gate has exactly two safe answers -- wait, or refuse -- and one
forbidden one: proceed. It also has a second obligation that is easy to miss.
It must not *record* anything. Quarantining an unreported effect is
`agent_run_resume.restart()`'s decision after a crash, not an updater's, and
resolving a quarantine is a person's. Every assertion about writes below exists
because a helpful updater is how "we do not know" quietly becomes "it worked".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from coworker.agent_run_approval import EffectStatus
from coworker.agent_run_repository import AgentRunRepository
from coworker.update_channel import drain

NOW = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)
SEND_ARGS = {"to": "dana@ramp.test", "subject": "Intro", "body": "Hello Dana."}


def _repo(tmp_path):
    repo = AgentRunRepository(tmp_path)
    return repo, repo.registry.register()


def _run(repo, owner, *, session_id="sess-1", now=NOW):
    return repo.create_run(
        session_id=session_id,
        trigger="chat",
        goal="reach out to Dana",
        owner=owner,
        lease_seconds=600,
        now=now,
    )


def _snapshot(repo, run_id) -> tuple:
    """Everything an update must leave exactly as it found it."""
    return (
        repo.get_run(run_id),
        repo.list_effects(run_id),
        len(repo.list_checkpoints(run_id)),
    )


# --- nothing to drain ----------------------------------------------------


def test_an_idle_installation_is_ready(tmp_path):
    repo, _ = _repo(tmp_path)
    assessment = drain.assess_drain(repo)
    assert assessment.status is drain.DrainStatus.READY
    assert assessment.ready
    assert assessment.blockers == ()


def test_a_finished_run_does_not_block(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _run(repo, owner)
    repo.checkpoint(
        started.lease,
        kind="terminal",
        state="complete",
        terminal_result={"status": "success"},
        now=NOW,
    )
    assert drain.assess_drain(repo).status is drain.DrainStatus.READY


# --- active work: wait ---------------------------------------------------


def test_a_run_a_live_owner_is_working_makes_the_update_wait(tmp_path):
    repo, owner = _repo(tmp_path)
    _run(repo, owner)
    assessment = drain.assess_drain(repo, now=NOW)
    assert assessment.status is drain.DrainStatus.ACTIVE_WORK
    assert assessment.may_wait
    assert not assessment.ready


def test_waiting_returns_ready_once_the_run_finishes(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _run(repo, owner)
    polls: list[float] = []

    def sleep(seconds: float) -> None:
        polls.append(seconds)
        if len(polls) == 2:
            repo.checkpoint(
                started.lease,
                kind="terminal",
                state="complete",
                terminal_result={"status": "success"},
                now=NOW,
            )

    assessment = drain.wait_for_drain(
        repo, timeout=30.0, poll=0.5, sleep=sleep, clock=_clock(), now=NOW
    )
    assert assessment.status is drain.DrainStatus.READY
    assert polls, "the gate must actually have waited before reporting ready"


def test_waiting_gives_up_and_refuses_rather_than_forcing(tmp_path):
    repo, owner = _repo(tmp_path)
    _run(repo, owner)
    assessment = drain.wait_for_drain(
        repo, timeout=2.0, poll=0.5, sleep=lambda _: None, clock=_clock(), now=NOW
    )
    assert assessment.status is drain.DrainStatus.ACTIVE_WORK
    assert not assessment.ready


# --- restart-safe continuation -------------------------------------------


def test_a_run_parked_on_a_person_is_restart_safe_and_does_not_block(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _run(repo, owner)
    repo.checkpoint(
        started.lease,
        kind="waiting_approval",
        state="waiting_approval",
        payload={"approval_id": "appr-1"},
        now=NOW,
    )
    assessment = drain.assess_drain(repo, now=NOW)
    assert assessment.status is drain.DrainStatus.READY
    assert started.run["run_id"] in assessment.continuable


def test_a_crashed_run_with_no_external_effect_is_restart_safe(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _run(repo, owner)
    repo.release_lease(started.lease, now=NOW)
    assessment = drain.assess_drain(repo, now=NOW + timedelta(hours=1))
    assert assessment.status is drain.DrainStatus.READY
    assert started.run["run_id"] in assessment.continuable


# --- the effect that must never be killed and replayed -------------------


def test_a_dispatched_send_with_no_outcome_stops_the_update(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _run(repo, owner)
    commit = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    before = _snapshot(repo, started.run["run_id"])

    assessment = drain.assess_drain(repo, now=NOW)

    assert assessment.status is drain.DrainStatus.UNSETTLED_EFFECT
    assert not assessment.ready
    blocker = assessment.blockers[0]
    assert blocker.run_id == started.run["run_id"]
    assert blocker.effect_id == commit.effect["effect_id"]
    assert blocker.tool_name == "gmail_send"
    # The gate looked, and changed nothing.
    assert _snapshot(repo, started.run["run_id"]) == before
    assert (
        repo.list_effects(started.run["run_id"])[0]["status"]
        == EffectStatus.DISPATCHED
    )


def test_waiting_on_an_unsettled_send_never_quarantines_it(tmp_path):
    """The gate may wait for the dispatching process to report. It may not decide."""
    repo, owner = _repo(tmp_path)
    started = _run(repo, owner)
    repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    before = _snapshot(repo, started.run["run_id"])

    assessment = drain.wait_for_drain(
        repo, timeout=5.0, poll=0.5, sleep=lambda _: None, clock=_clock(), now=NOW
    )

    assert assessment.status is drain.DrainStatus.UNSETTLED_EFFECT
    assert _snapshot(repo, started.run["run_id"]) == before
    assert repo.list_quarantined_effects() == []


def test_waiting_clears_once_the_dispatching_process_reports(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _run(repo, owner)
    commit = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    polls: list[float] = []

    def sleep(seconds: float) -> None:
        polls.append(seconds)
        if len(polls) == 1:
            settled = repo.record_effect_outcome(
                commit.lease, commit.effect["effect_id"], ok=True, now=NOW
            )
            repo.checkpoint(
                settled.lease,
                kind="terminal",
                state="complete",
                terminal_result={"status": "success"},
                now=NOW,
            )

    assessment = drain.wait_for_drain(
        repo, timeout=30.0, poll=0.5, sleep=sleep, clock=_clock(), now=NOW
    )
    assert assessment.status is drain.DrainStatus.READY
    assert polls


# --- a quarantine is a person's, and waiting cannot help ------------------


def test_a_quarantined_effect_stops_the_update_immediately(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _run(repo, owner)
    commit = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    repo.quarantine_effect(
        commit.lease,
        commit.effect["effect_id"],
        reason="the process died before the send reported",
        now=NOW,
    )
    before = _snapshot(repo, started.run["run_id"])
    polls: list[float] = []

    assessment = drain.wait_for_drain(
        repo,
        timeout=60.0,
        poll=0.5,
        sleep=lambda seconds: polls.append(seconds),
        clock=_clock(),
        now=NOW,
    )

    assert assessment.status is drain.DrainStatus.QUARANTINED_EFFECT
    assert not assessment.may_wait
    assert polls == [], "no amount of waiting settles a quarantine; a person does"
    assert _snapshot(repo, started.run["run_id"]) == before
    assert (
        repo.list_quarantined_effects()[0]["status"] == EffectStatus.AMBIGUOUS
    ), "the update must not settle what a person owns"


def test_a_quarantine_on_a_finished_run_still_stops_the_update(tmp_path):
    """A quarantined effect outlives its run. The sweep is over effects, not runs."""
    repo, owner = _repo(tmp_path)
    started = _run(repo, owner)
    commit = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    repo.quarantine_effect(
        commit.lease, commit.effect["effect_id"], reason="unknown", now=NOW
    )
    lease = repo.acquire_lease(started.run["run_id"], owner, now=NOW)
    repo.checkpoint(
        lease,
        kind="terminal",
        state="partial",
        terminal_result={"status": "partial"},
        now=NOW,
    )
    assert repo.get_run(started.run["run_id"])["current_state"] == "partial"

    assessment = drain.assess_drain(repo, now=NOW)
    assert assessment.status is drain.DrainStatus.QUARANTINED_EFFECT


def test_a_quarantine_outranks_active_work_in_the_reported_status(tmp_path):
    repo, owner = _repo(tmp_path)
    held = _run(repo, owner, session_id="sess-1")
    commit = repo.dispatch_effect(
        held.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    repo.quarantine_effect(
        commit.lease, commit.effect["effect_id"], reason="unknown", now=NOW
    )
    _run(repo, owner, session_id="sess-2")

    assessment = drain.assess_drain(repo, now=NOW)
    assert assessment.status is drain.DrainStatus.QUARANTINED_EFFECT
    statuses = {blocker.status for blocker in assessment.blockers}
    assert drain.DrainStatus.ACTIVE_WORK in statuses


def _clock():
    """A monotonic clock that advances one second per reading."""
    ticks = iter(range(0, 10_000))
    return lambda: float(next(ticks))
