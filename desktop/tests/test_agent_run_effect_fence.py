"""Slice 3: the fence around an external effect that may already have happened.

The property under test is that an effect with a dispatch and no outcome never
becomes `succeeded` or `failed` by any route. Not a retry, not a resume, not a
second owner, not a raw SQL edit. It becomes `ambiguous`, and only a named
person moves it from there.
"""

import re
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from coworker.agent_run_approval import (
    EFFECT_SCHEMA,
    EFFECT_STATUSES,
    MACHINE_OUTCOMES,
    OPERATOR_OUTCOMES,
    AgentRunEffectFenced,
    EffectStatus,
    ReplayClass,
    effect_fingerprint,
    external_effect_evidence,
    replay_class,
)
from coworker.agent_run_owner import OwnerRegistry
from coworker.agent_run_repository import (
    DB_NAME,
    SCHEMA_VERSION,
    AgentRunLeaseLost,
    AgentRunRepository,
)
from coworker.agent_run_state import AgentRunTransitionError
from coworker.permissions import ASK, AUTO, RETRY_SAFE
from coworker.run_evidence import Evidence

NOW = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)
SEND_ARGS = {"to": "dana@ramp.test", "subject": "Intro", "body": "Hello Dana."}


def _repo(tmp_path):
    repo = AgentRunRepository(tmp_path)
    return repo, repo.registry.register()


def _start(repo, owner, **kwargs):
    return repo.create_run(
        session_id=kwargs.pop("session_id", "sess-1"),
        trigger="chat",
        goal="reach out to Dana",
        owner=owner,
        lease_seconds=kwargs.pop("lease_seconds", 600),
        now=kwargs.pop("now", NOW),
    )


def _stored_bytes(tmp_path) -> bytes:
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        path = tmp_path / f"{DB_NAME}{suffix}"
        if path.exists():
            blob += path.read_bytes()
    return blob


def _raw(tmp_path):
    """A connection that knows nothing about the repository's method contracts."""
    conn = sqlite3.connect(tmp_path / DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --- the fence agrees with permissions rather than restating it ----------


def test_replay_class_is_derived_from_permissions_retry_safe():
    """One list decides this. The fence reads it; it does not keep its own."""
    assert RETRY_SAFE, "the permission module must still publish a retry allowlist"
    for name in RETRY_SAFE:
        assert replay_class(name) is ReplayClass.SAFE, name
    for name in ASK:
        assert replay_class(name) is ReplayClass.CONSEQUENTIAL, name
    # Exactly the retry allowlist is safe: nothing the fence adds, nothing it drops.
    known = AUTO | ASK
    assert {name for name in known if replay_class(name) is ReplayClass.SAFE} == set(
        RETRY_SAFE
    )
    # gmail_send is the case the fence exists for, and it is fenced.
    assert "gmail_send" not in RETRY_SAFE
    assert replay_class("gmail_send") is ReplayClass.CONSEQUENTIAL
    # A tool nobody has classified is fenced, never replayed.
    assert replay_class("a_tool_from_a_later_build") is ReplayClass.CONSEQUENTIAL


def test_machine_and_operator_outcomes_share_no_value():
    assert MACHINE_OUTCOMES.isdisjoint(OPERATOR_OUTCOMES)
    assert MACHINE_OUTCOMES | OPERATOR_OUTCOMES <= EFFECT_STATUSES
    assert EffectStatus.AMBIGUOUS not in MACHINE_OUTCOMES | OPERATOR_OUTCOMES


def test_an_ambiguous_effect_is_reported_with_the_existing_evidence_word():
    """`run_evidence` already had the word. The fence reuses it."""
    assert external_effect_evidence([]) is None
    assert (
        external_effect_evidence([{"status": EffectStatus.SUCCEEDED}]) is None
    )
    assert (
        external_effect_evidence([{"status": EffectStatus.DISPATCHED}])
        is Evidence.MISSING
    )
    assert (
        external_effect_evidence(
            [{"status": EffectStatus.SUCCEEDED}, {"status": EffectStatus.AMBIGUOUS}]
        )
        is Evidence.AMBIGUOUS
    )


# --- the happy path still works -----------------------------------------


def test_a_dispatched_effect_is_recorded_before_the_call_and_settled_after(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    run_id = started.run["run_id"]

    dispatch = repo.dispatch_effect(
        started.lease,
        tool_name="gmail_send",
        arguments=SEND_ARGS,
        tool_call_id="call-1",
        approval_id="inbox-1",
        step=3,
        now=NOW,
    )
    effect_id = dispatch.effect["effect_id"]
    assert dispatch.effect["status"] == EffectStatus.DISPATCHED
    assert dispatch.effect["replay_class"] == ReplayClass.CONSEQUENTIAL
    assert dispatch.effect["arguments_fingerprint"] == effect_fingerprint(
        "gmail_send", SEND_ARGS
    )
    assert dispatch.run["current_state"] == "running"
    assert dispatch.lease is not None and dispatch.lease.version == started.lease.version + 1
    assert dispatch.checkpoint["kind"] == "tool_pending"
    assert dispatch.checkpoint["payload"]["attempt_id"] == effect_id

    settled = repo.record_effect_outcome(
        dispatch.lease, effect_id, ok=True, outcome_ref="gmail-msg-77", now=NOW
    )
    assert settled.effect["status"] == EffectStatus.SUCCEEDED
    assert settled.effect["outcome_ref"] == "gmail-msg-77"
    assert settled.checkpoint["kind"] == "tool_completed"
    assert repo.get_run(run_id)["current_state"] == "running"
    assert repo.list_effects(run_id) == [settled.effect]


def test_a_failure_the_process_actually_observed_is_recorded_as_failed(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )

    settled = repo.record_effect_outcome(
        dispatch.lease,
        dispatch.effect["effect_id"],
        ok=False,
        error_class="SMTPRecipientsRefused",
        error_summary="the recipient address was refused",
        now=NOW,
    )

    assert settled.effect["status"] == EffectStatus.FAILED
    assert settled.checkpoint["payload"]["error_class"] == "SMTPRecipientsRefused"
    # `failed` is a machine outcome, so it needed no person and names none.
    assert settled.effect["resolved_by"] is None


# --- the quarantine ------------------------------------------------------


def test_an_unreported_effect_is_quarantined_and_the_run_stops(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    run_id = started.run["run_id"]
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )

    quarantine = repo.quarantine_effect(
        dispatch.lease,
        dispatch.effect["effect_id"],
        reason="owner_process_dead",
        now=NOW,
    )

    assert quarantine.effect["status"] == EffectStatus.AMBIGUOUS
    assert quarantine.effect["reason"] == "owner_process_dead"
    assert quarantine.checkpoint["kind"] == "tool_outcome_unknown"
    assert quarantine.run["current_state"] == "interrupted"
    # Nobody owns a quarantined run. A person does.
    assert quarantine.lease is None
    assert quarantine.run["lease_owner"] is None
    assert repo.get_run(run_id)["lease_owner"] is None
    assert [item["run_id"] for item in repo.list_quarantined_effects()] == [run_id]


def test_a_live_owner_may_park_on_an_effect_that_never_answered(tmp_path):
    """The same ambiguity without a crash: the call timed out and we are alive."""
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )

    parked = repo.quarantine_effect(
        dispatch.lease,
        dispatch.effect["effect_id"],
        reason="send_never_answered",
        state="waiting_external",
        now=NOW,
    )

    assert parked.run["current_state"] == "waiting_external"
    assert parked.effect["status"] == EffectStatus.AMBIGUOUS
    assert parked.lease is None and parked.run["lease_owner"] is None
    assert parked.checkpoint["kind"] == "waiting_external"


def test_quarantine_refuses_a_state_the_run_machine_does_not_allow(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )

    with pytest.raises(ValueError):
        repo.quarantine_effect(
            dispatch.lease, dispatch.effect["effect_id"], reason="x", state="complete"
        )


# --- the fence proper: nothing automatic leaves `ambiguous` --------------


def test_a_retry_cannot_report_an_outcome_for_a_quarantined_effect(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    effect_id = dispatch.effect["effect_id"]
    repo.quarantine_effect(dispatch.lease, effect_id, reason="cut", now=NOW)

    # The dispatching owner comes back with a result. It is too late.
    retaken = repo.acquire_lease(started.run["run_id"], owner, 600, now=NOW)
    assert retaken is not None
    for ok in (True, False):
        with pytest.raises(AgentRunEffectFenced):
            repo.record_effect_outcome(retaken, effect_id, ok=ok, now=NOW)
    assert repo.list_effects(started.run["run_id"])[0]["status"] == (
        EffectStatus.AMBIGUOUS
    )


def test_a_second_owner_cannot_report_an_outcome_it_never_dispatched(tmp_path):
    """Only the process that made the call may say what the call did."""
    repo, owner = _repo(tmp_path)
    successor = OwnerRegistry(tmp_path).register()
    started = _start(repo, owner, lease_seconds=60)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    effect_id = dispatch.effect["effect_id"]

    later = NOW + timedelta(seconds=120)
    repo.reconcile_expired_leases(now=later)
    taken = repo.acquire_lease(started.run["run_id"], successor, 600, now=later)
    assert taken is not None

    with pytest.raises(AgentRunEffectFenced):
        repo.record_effect_outcome(taken, effect_id, ok=True, now=later)
    assert repo.list_effects(started.run["run_id"])[0]["status"] == (
        EffectStatus.DISPATCHED
    )
    # The successor may quarantine it, because that claims nothing about the world.
    quarantine = repo.quarantine_effect(taken, effect_id, reason="cut", now=later)
    assert quarantine.effect["status"] == EffectStatus.AMBIGUOUS


def test_a_superseded_lease_writes_no_effect_at_all(tmp_path):
    repo, owner = _repo(tmp_path)
    successor = OwnerRegistry(tmp_path).register()
    started = _start(repo, owner, lease_seconds=60)
    later = NOW + timedelta(seconds=120)
    repo.reconcile_expired_leases(now=later)
    assert repo.acquire_lease(started.run["run_id"], successor, 600, now=later)

    before = repo.get_run(started.run["run_id"])
    with pytest.raises(AgentRunLeaseLost):
        repo.dispatch_effect(
            started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=later
        )
    assert repo.get_run(started.run["run_id"]) == before
    assert repo.list_effects(started.run["run_id"]) == []


def test_raw_sql_cannot_launder_a_quarantined_effect_into_a_success(tmp_path):
    """The rule lives in the database, not only in the methods above it."""
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    effect_id = dispatch.effect["effect_id"]
    repo.quarantine_effect(dispatch.lease, effect_id, reason="cut", now=NOW)

    conn = _raw(tmp_path)
    try:
        for target in ("succeeded", "failed", "dispatched"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE agent_run_effects SET status = ? WHERE effect_id = ?",
                    (target, effect_id),
                )
        # Nor by deleting the evidence and writing a clean row in its place.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM agent_run_effects WHERE effect_id = ?", (effect_id,)
            )
        # Nor by opening a fresh record already claiming success.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO agent_run_effects (effect_id, run_id, tool_name, "
                "replay_class, arguments_fingerprint, status, dispatched_by, "
                "dispatched_at) VALUES ('e2', ?, 'gmail_send', 'consequential', "
                "'f', 'succeeded', 'o', ?)",
                (started.run["run_id"], NOW.isoformat()),
            )
        # Nor by claiming a person settled it without naming one.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE agent_run_effects SET status = 'resolved_succeeded' "
                "WHERE effect_id = ?",
                (effect_id,),
            )
        conn.commit()
    finally:
        conn.close()
    assert repo.list_effects(started.run["run_id"])[0]["status"] == (
        EffectStatus.AMBIGUOUS
    )


def test_a_settled_effect_never_changes_its_outcome(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    effect_id = dispatch.effect["effect_id"]
    settled = repo.record_effect_outcome(dispatch.lease, effect_id, ok=True, now=NOW)

    with pytest.raises(AgentRunEffectFenced):
        repo.record_effect_outcome(settled.lease, effect_id, ok=False, now=NOW)
    with pytest.raises(AgentRunEffectFenced):
        repo.quarantine_effect(
            settled.lease, effect_id, reason="second thoughts", now=NOW
        )
    with pytest.raises(AgentRunEffectFenced):
        repo.resolve_quarantined_effect(
            effect_id, decision="abandoned", operator="fisher"
        )
    conn = _raw(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE agent_run_effects SET status = 'failed' WHERE effect_id = ?",
                (effect_id,),
            )
    finally:
        conn.close()
    assert repo.list_effects(started.run["run_id"])[0]["status"] == (
        EffectStatus.SUCCEEDED
    )


# --- only a named person settles a quarantine ---------------------------


def test_a_person_settles_a_quarantine_and_is_recorded_as_having_done_so(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    effect_id = dispatch.effect["effect_id"]
    repo.quarantine_effect(dispatch.lease, effect_id, reason="cut", now=NOW)

    resolved = repo.resolve_quarantined_effect(
        effect_id,
        decision="resolved_succeeded",
        operator="fisher",
        note="Dana replied, so it went out.",
        outcome_ref="gmail-msg-77",
        now=NOW,
    )

    assert resolved["status"] == EffectStatus.RESOLVED_SUCCEEDED
    assert resolved["resolved_by"] == "fisher"
    assert resolved["resolved_note"] == "Dana replied, so it went out."
    assert resolved["outcome_ref"] == "gmail-msg-77"
    # A resolved effect is not a machine outcome, and never reads as one.
    assert resolved["status"] not in MACHINE_OUTCOMES
    assert repo.list_quarantined_effects() == []


def test_a_resolution_without_a_person_is_refused(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    effect_id = dispatch.effect["effect_id"]
    repo.quarantine_effect(dispatch.lease, effect_id, reason="cut", now=NOW)

    for operator in ("", "   ", None):
        with pytest.raises(ValueError):
            repo.resolve_quarantined_effect(
                effect_id, decision="abandoned", operator=operator
            )
    # A person cannot borrow the machine's words either.
    for decision in ("succeeded", "failed", "dispatched", "ambiguous", "nonsense"):
        with pytest.raises(ValueError):
            repo.resolve_quarantined_effect(
                effect_id, decision=decision, operator="fisher"
            )
    assert repo.list_effects(started.run["run_id"])[0]["status"] == (
        EffectStatus.AMBIGUOUS
    )


def test_only_a_quarantined_effect_can_be_resolved(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )

    with pytest.raises(AgentRunEffectFenced):
        repo.resolve_quarantined_effect(
            dispatch.effect["effect_id"], decision="abandoned", operator="fisher"
        )
    with pytest.raises(KeyError):
        repo.resolve_quarantined_effect(
            "no-such-effect", decision="abandoned", operator="fisher"
        )


# --- redaction -----------------------------------------------------------


def test_planted_secrets_never_reach_the_persisted_effect_record(tmp_path):
    """Non-vacuous: the effect row must exist and carry its fields first."""
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    token = "sk-" + "proj-" + "Z" * 28
    header = "Bearer " + "Q" * 24
    planted = {
        "to": "dana@ramp.test",
        "subject": "Following up on the Codeology intro",
        "body": "Dana, here is the private thing I wrote for you.",
        "api_key": token,
        "authorization": header,
    }

    dispatch = repo.dispatch_effect(
        started.lease,
        tool_name="gmail_send",
        arguments=planted,
        tool_call_id="call-9",
        approval_id="inbox-9",
        now=NOW,
    )
    effect_id = dispatch.effect["effect_id"]
    quarantined = repo.quarantine_effect(
        dispatch.lease,
        effect_id,
        reason=f"dispatch failed with api_key={token}",
        now=NOW,
    )
    repo.resolve_quarantined_effect(
        effect_id,
        decision="resolved_failed",
        operator="fisher",
        note=f"checked the mailbox, nothing sent, authorization: {header}",
        now=NOW,
    )

    stored = repo.list_effects(started.run["run_id"])[0]
    assert stored["effect_id"] == effect_id
    assert stored["tool_name"] == "gmail_send"
    assert stored["tool_call_id"] == "call-9"
    assert stored["approval_id"] == "inbox-9"
    assert stored["arguments_fingerprint"] == effect_fingerprint("gmail_send", planted)
    assert stored["status"] == EffectStatus.RESOLVED_FAILED
    assert stored["resolved_by"] == "fisher"
    assert stored["reason"].startswith("dispatch failed with api_key=")
    assert stored["resolved_note"].startswith("checked the mailbox, nothing sent")
    assert quarantined.checkpoint["payload"], "the checkpoint must not be empty"
    assert quarantined.checkpoint["payload"]["tool_name"] == "gmail_send"

    blob = _stored_bytes(tmp_path)
    assert b"gmail_send" in blob, "the effect record must really be on disk"
    assert stored["arguments_fingerprint"].encode() in blob
    for secret in (
        token,
        header,
        planted["body"],
        planted["subject"],
        planted["to"],
    ):
        assert secret.encode() not in blob, secret


def test_the_effect_fingerprint_separates_two_different_sends(tmp_path):
    other = {**SEND_ARGS, "to": "someone.else@ramp.test"}
    assert effect_fingerprint("gmail_send", SEND_ARGS) != effect_fingerprint(
        "gmail_send", other
    )
    assert effect_fingerprint("gmail_send", SEND_ARGS) == effect_fingerprint(
        "gmail_send", dict(reversed(list(SEND_ARGS.items())))
    )
    assert effect_fingerprint("gmail_send", SEND_ARGS) != effect_fingerprint(
        "gmail_draft", SEND_ARGS
    )


# --- the store's own rules still hold ------------------------------------


def test_an_effect_cannot_be_dispatched_from_a_parked_run(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    parked = repo.checkpoint(
        started.lease, kind="waiting_approval", state="waiting_approval", now=NOW
    )
    assert parked.lease is None

    with pytest.raises(AgentRunLeaseLost):
        repo.dispatch_effect(
            started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
        )


def test_an_effect_record_survives_checkpoint_retention(tmp_path):
    """Retention drops step detail. It never drops the record of a real effect."""
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    settled = repo.record_effect_outcome(
        dispatch.lease, dispatch.effect["effect_id"], ok=True, now=NOW
    )
    repo.checkpoint(
        settled.lease,
        kind="terminal",
        state="complete",
        terminal_result={"status": "ok"},
        now=NOW,
    )

    assert repo.prune_checkpoints(finished_before=NOW + timedelta(days=1)) == [
        started.run["run_id"]
    ]
    assert repo.list_checkpoints(started.run["run_id"]) == []
    assert repo.list_effects(started.run["run_id"])[0]["status"] == (
        EffectStatus.SUCCEEDED
    )


def test_a_quarantined_run_is_never_pruned_because_its_hole_is_marked(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    quarantine = repo.quarantine_effect(
        dispatch.lease, dispatch.effect["effect_id"], reason="cut", now=NOW
    )
    assert quarantine.run["current_state"] == "interrupted"

    # Force a finish time so retention would otherwise consider it.
    conn = _raw(tmp_path)
    try:
        conn.execute(
            "UPDATE agent_runs SET finished_at = ? WHERE run_id = ?",
            (NOW.isoformat(), started.run["run_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    assert repo.prune_checkpoints(finished_before=NOW + timedelta(days=1)) == []
    kinds = [item["kind"] for item in repo.list_checkpoints(started.run["run_id"])]
    assert "tool_outcome_unknown" in kinds


def test_dispatching_the_same_effect_id_twice_is_refused(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    first = repo.dispatch_effect(
        started.lease,
        tool_name="gmail_send",
        arguments=SEND_ARGS,
        effect_id="effect-fixed",
        now=NOW,
    )

    with pytest.raises(AgentRunEffectFenced):
        repo.dispatch_effect(
            first.lease,
            tool_name="gmail_send",
            arguments=SEND_ARGS,
            effect_id="effect-fixed",
            now=NOW,
        )
    assert len(repo.list_effects(started.run["run_id"])) == 1


def test_the_quarantine_checkpoint_is_a_legal_edge_of_the_run_machine(tmp_path):
    """Doctor walks stored checkpoints back through `validate_transition`."""
    from coworker.agent_run_state import validate_transition

    validate_transition("tool_pending", "running", "running")
    validate_transition("tool_completed", "running", "running")
    validate_transition("tool_outcome_unknown", "running", "interrupted")
    validate_transition("tool_outcome_unknown", "interrupted", "interrupted")
    validate_transition("waiting_external", "running", "waiting_external")
    with pytest.raises(AgentRunTransitionError):
        validate_transition("waiting_external", "interrupted", "waiting_external")
    # Defence in depth. Even if the effect fence were removed, a quarantined
    # run rests in `interrupted`, and no tool may report a result from there.
    with pytest.raises(AgentRunTransitionError):
        validate_transition("tool_completed", "interrupted", "running")


# --- the approval door ---------------------------------------------------


def _parked(repo, owner, *, approval_id="inbox-7"):
    started = _start(repo, owner)
    repo.checkpoint(
        started.lease,
        kind="waiting_approval",
        state="waiting_approval",
        payload={"approval_id": approval_id, "tool_name": "gmail_send"},
        approval_ids=[approval_id],
        now=NOW,
    )
    return started.run["run_id"]


def test_a_parked_run_comes_back_only_against_a_named_resolution(tmp_path):
    repo, owner = _repo(tmp_path)
    run_id = _parked(repo, owner)

    # The ordinary door stays shut: a parked run is a person's.
    assert repo.acquire_lease(run_id, owner, 600, now=NOW) is None

    commit = repo.resume_from_approval(
        run_id, owner, approval_id="inbox-7", decision="allow", now=NOW
    )

    assert commit is not None
    assert commit.run["current_state"] == "running"
    assert commit.run["lease_owner"] == owner.owner_id
    assert commit.checkpoint["kind"] == "approval_resolved"
    assert commit.checkpoint["payload"] == {
        "approval_id": "inbox-7",
        "status": "allow",
    }
    assert commit.lease is not None
    # The run is running again, so an effect may now be dispatched against it.
    assert repo.dispatch_effect(
        commit.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )


def test_the_lease_and_the_unparking_commit_together(tmp_path):
    """Doctor calls a lease in a waiting state a record contradicting itself.

    One version bump and one new checkpoint prove one write, so there is no
    committed instant in which the run is parked and leased at the same time.
    A two-step version -- take the lease, then move the state -- would leave
    that pair visible to any other reader between the two commits.
    """
    repo, owner = _repo(tmp_path)
    run_id = _parked(repo, owner)
    before = repo.get_run(run_id)

    commit = repo.resume_from_approval(
        run_id, owner, approval_id="inbox-7", decision="deny", now=NOW
    )

    assert commit.run["version"] == before["version"] + 1
    assert commit.run["checkpoint_sequence"] == before["checkpoint_sequence"] + 1
    assert commit.lease.version == commit.run["version"]
    assert commit.run["current_state"] == "running"
    assert commit.run["lease_owner"] == owner.owner_id
    conn = _raw(tmp_path)
    try:
        row = conn.execute(
            "SELECT current_state, lease_owner, version FROM agent_runs "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("running", owner.owner_id, before["version"] + 1)


def test_an_approval_the_run_never_raised_opens_nothing(tmp_path):
    repo, owner = _repo(tmp_path)
    run_id = _parked(repo, owner)

    with pytest.raises(ValueError):
        repo.resume_from_approval(
            run_id, owner, approval_id="inbox-somebody-elses", decision="allow",
            now=NOW,
        )
    with pytest.raises(ValueError):
        repo.resume_from_approval(
            run_id, owner, approval_id="inbox-7", decision="maybe", now=NOW
        )
    with pytest.raises(KeyError):
        repo.resume_from_approval(
            "no-such-run", owner, approval_id="inbox-7", decision="allow", now=NOW
        )
    run = repo.get_run(run_id)
    assert run["current_state"] == "waiting_approval"
    assert run["lease_owner"] is None


def test_a_running_run_is_not_reopened_by_the_approval_door(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)

    assert (
        repo.resume_from_approval(
            started.run["run_id"],
            owner,
            approval_id="inbox-7",
            decision="allow",
            now=NOW,
        )
        is None
    )
    assert repo.get_run(started.run["run_id"])["version"] == started.run["version"]


def test_an_approval_never_unparks_a_run_whose_effect_is_quarantined(tmp_path):
    """An Allow says nothing about whether the last send already went out."""
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    dispatch = repo.dispatch_effect(
        started.lease,
        tool_name="gmail_send",
        arguments=SEND_ARGS,
        approval_id="inbox-7",
        now=NOW,
    )
    parked = repo.quarantine_effect(
        dispatch.lease,
        dispatch.effect["effect_id"],
        reason="send_never_answered",
        state="waiting_external",
        now=NOW,
    )
    assert parked.run["current_state"] == "waiting_external"

    with pytest.raises(AgentRunEffectFenced):
        repo.resume_from_approval(
            started.run["run_id"],
            owner,
            approval_id="inbox-7",
            decision="allow",
            now=NOW,
        )
    run = repo.get_run(started.run["run_id"])
    assert run["current_state"] == "waiting_external"
    assert run["lease_owner"] is None
    assert repo.list_effects(run["run_id"])[0]["status"] == EffectStatus.AMBIGUOUS


# --- what the store looks like to a reader that did not write it ---------


def _version_one_store(tmp_path):
    """A production-shaped version 1 store: real rows in both v1 tables.

    Built by the real repository, then wound back to exactly what a build
    without the effect fence would have left on disk.
    """
    repo, owner = _repo(tmp_path)
    first = _start(repo, owner, session_id="sess-old-a")
    repo.checkpoint(first.lease, kind="model_pending", payload={"step": 1}, now=NOW)
    second = _start(repo, owner, session_id="sess-old-b")
    repo.checkpoint(
        second.lease,
        kind="terminal",
        state="complete",
        terminal_result={"status": "ok", "text_length": 12},
        source_refs=[{"id": "src-1", "title": "Ramp"}],
        now=NOW,
    )
    parked = _start(repo, owner, session_id="sess-old-c")
    repo.checkpoint(
        parked.lease,
        kind="waiting_approval",
        state="waiting_approval",
        approval_ids=["inbox-1"],
        now=NOW,
    )
    before = {
        run["run_id"]: run
        for run in (
            repo.get_run(first.run["run_id"]),
            repo.get_run(second.run["run_id"]),
            repo.get_run(parked.run["run_id"]),
        )
    }
    checkpoints = {
        run_id: repo.list_checkpoints(run_id) for run_id in before
    }
    repo.close()
    conn = _raw(tmp_path)
    try:
        conn.executescript(
            "DROP TRIGGER IF EXISTS agent_run_effects_open_as_dispatched;"
            "DROP TRIGGER IF EXISTS agent_run_effects_quarantine_is_operator_only;"
            "DROP TRIGGER IF EXISTS agent_run_effects_settled_is_final;"
            "DROP TRIGGER IF EXISTS agent_run_effects_are_never_deleted;"
            "DROP TABLE IF EXISTS agent_run_effects;"
            "PRAGMA user_version = 1;"
        )
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        conn.close()
    return before, checkpoints


def _objects(tmp_path):
    conn = _raw(tmp_path)
    try:
        return {
            (row[0], row[1])
            for row in conn.execute("SELECT type, name FROM sqlite_master")
        }
    finally:
        conn.close()


def test_the_effect_schema_only_creates_objects_that_did_not_exist(tmp_path):
    """The whole reason create-if-missing is enough here, asserted.

    `CREATE TABLE IF NOT EXISTS` silently does nothing on a table that already
    exists. That is safe only while the schema adds no column to an existing
    table. If someone later adds one to `agent_runs`, this fails and says so,
    rather than leaving a version 1 database stamped 2 with a missing column.
    """
    assert not re.search(r"\bALTER\s+TABLE\b", EFFECT_SCHEMA, re.I)
    # Every object it creates is new. It never names a version 1 table.
    created = set(
        re.findall(
            r"CREATE\s+(?:TABLE|INDEX|TRIGGER)\s+IF\s+NOT\s+EXISTS\s+(\w+)",
            EFFECT_SCHEMA,
            re.I,
        )
    )
    assert created, "the effect schema must create something"
    assert all(name.startswith("agent_run_effects") for name in created), created
    for legacy in ("agent_runs", "agent_run_checkpoints"):
        assert not re.search(
            rf"\b(?:ALTER|DROP)\b[^;]*\b{legacy}\b(?!_)", EFFECT_SCHEMA, re.I
        )
    # And the trigger bodies only ever touch the new table.
    assert "ON agent_run_effects" in EFFECT_SCHEMA
    assert " ON agent_runs " not in EFFECT_SCHEMA


def test_a_version_one_store_upgrades_without_losing_or_changing_a_row(tmp_path):
    """A production-shaped upgrade fixture: v1 on disk, opened by this build."""
    before, checkpoints = _version_one_store(tmp_path)
    objects_before = _objects(tmp_path)

    reopened = AgentRunRepository(tmp_path)

    conn = _raw(tmp_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    # Exactly the new objects appeared. Nothing existing was replaced.
    added = _objects(tmp_path) - objects_before
    assert objects_before - _objects(tmp_path) == set()
    assert {name for _, name in added} == {
        "agent_run_effects",
        # SQLite's own index for the TEXT PRIMARY KEY.
        "sqlite_autoindex_agent_run_effects_1",
        "agent_run_effects_by_run",
        "agent_run_effects_open",
        "agent_run_effects_open_as_dispatched",
        "agent_run_effects_quarantine_is_operator_only",
        "agent_run_effects_settled_is_final",
        "agent_run_effects_are_never_deleted",
    }
    # Every version 1 row survives byte for byte, including its lease and version.
    for run_id, run in before.items():
        assert reopened.get_run(run_id) == run
        assert reopened.list_checkpoints(run_id) == checkpoints[run_id]
        assert reopened.list_effects(run_id) == []
    # The upgraded store works: a fence write lands on a run created before it.
    live = next(
        run_id for run_id, run in before.items() if run["current_state"] == "running"
    )
    later = NOW + timedelta(seconds=601)
    lease = reopened.acquire_lease(live, reopened.registry.register(), 600, now=later)
    assert lease is not None
    assert reopened.dispatch_effect(
        lease, tool_name="gmail_send", arguments=SEND_ARGS, now=later
    ).effect["status"] == EffectStatus.DISPATCHED


def test_the_upgrade_is_idempotent_across_reruns_and_a_restart(tmp_path):
    before, checkpoints = _version_one_store(tmp_path)

    first = AgentRunRepository(tmp_path)
    settled = _objects(tmp_path)
    first.close()
    # Rerun: opening again must change nothing at all.
    second = AgentRunRepository(tmp_path)
    second.close()
    # Post-restart: a third process, no shared connection or in-memory state.
    third = AgentRunRepository(tmp_path)

    assert _objects(tmp_path) == settled
    conn = _raw(tmp_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()
    for run_id, run in before.items():
        assert third.get_run(run_id) == run
        assert third.list_checkpoints(run_id) == checkpoints[run_id]


def test_a_store_from_a_newer_build_fails_closed_without_touching_it(tmp_path):
    """Unknown future versions refuse to open and modify nothing."""
    before, checkpoints = _version_one_store(tmp_path)
    AgentRunRepository(tmp_path).close()
    conn = _raw(tmp_path)
    try:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
    finally:
        conn.close()
    objects_before = _objects(tmp_path)

    with pytest.raises(RuntimeError, match="newer than this build"):
        AgentRunRepository(tmp_path)

    conn = _raw(tmp_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 1
    finally:
        conn.close()
    assert _objects(tmp_path) == objects_before
    # Reading it back with a build that does know the version finds it intact.
    conn = _raw(tmp_path)
    try:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()
    recovered = AgentRunRepository(tmp_path)
    for run_id, run in before.items():
        assert recovered.get_run(run_id) == run
        assert recovered.list_checkpoints(run_id) == checkpoints[run_id]


def test_the_record_a_fenced_run_leaves_behind_passes_doctors_history_rules(tmp_path):
    """Doctor walks stored checkpoints back through `validate_transition`.

    This reproduces the walk PR #111 added, over a run driven through every
    path this slice introduces, so the new writes cannot quietly produce a
    history Doctor would call impossible.
    """
    from coworker.agent_run_state import is_leasable, is_terminal, validate_transition
    from coworker.run_evidence import analyze_record

    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    run_id = started.run["run_id"]
    # A completed send, then a parked approval, then a resolved one, then a
    # dispatch that never reported, quarantined.
    first = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    done = repo.record_effect_outcome(
        first.lease, first.effect["effect_id"], ok=True, now=NOW
    )
    parked = repo.checkpoint(
        done.lease,
        kind="waiting_approval",
        state="waiting_approval",
        approval_ids=["inbox-7"],
        payload={"approval_id": "inbox-7"},
        now=NOW,
    )
    assert parked.lease is None
    reopened = repo.resume_from_approval(
        run_id, owner, approval_id="inbox-7", decision="allow", now=NOW
    )
    second = repo.dispatch_effect(
        reopened.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    repo.quarantine_effect(
        second.lease, second.effect["effect_id"], reason="cut", now=NOW
    )

    run = repo.get_run(run_id)
    checkpoints = repo.list_checkpoints(run_id)
    record = analyze_record(run, checkpoints)

    # Doctor reads no further into a record it cannot express. It must be able
    # to express everything this slice writes.
    assert record["unsupported"] == ()
    assert not record["damaged"]
    assert [item["sequence"] for item in checkpoints] == list(
        range(1, record["expected"] + 1)
    )
    # No impossible transition, and nothing after a terminal state.
    previous = "running"
    for item in checkpoints:
        assert not is_terminal(previous), item
        if item["sequence"] == 1:
            assert item["kind"] == "run_started"
        else:
            validate_transition(item["kind"], previous, item["state"])
        previous = item["state"]
    # The row agrees with its own last checkpoint, and holds no illegal lease.
    assert checkpoints[-1]["state"] == run["current_state"]
    assert run["lease_owner"] is None
    assert not is_leasable(run["current_state"]) or run["lease_owner"] is None
    # The hole is marked, so retention and the receipt both know it is there.
    assert "tool_outcome_unknown" in [item["kind"] for item in checkpoints]


# --- trying to break it --------------------------------------------------


def test_two_owners_racing_to_quarantine_one_effect_quarantine_it_once(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner, now=None)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS
    )
    run_id = started.run["run_id"]
    effect_id = dispatch.effect["effect_id"]

    barrier = threading.Barrier(2)
    outcomes: list[object] = []
    guard = threading.Lock()

    def contend():
        worker = AgentRunRepository(tmp_path)
        barrier.wait()
        try:
            result = worker.quarantine_effect(
                dispatch.lease, effect_id, reason="racing cut"
            )
        except Exception as exc:
            result = exc
        with guard:
            outcomes.append(result)

    threads = [threading.Thread(target=contend) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    committed = [item for item in outcomes if not isinstance(item, Exception)]
    refused = [item for item in outcomes if isinstance(item, Exception)]
    assert len(committed) == 1 and len(refused) == 1
    assert repo.list_effects(run_id)[0]["status"] == EffectStatus.AMBIGUOUS
    kinds = [item["kind"] for item in repo.list_checkpoints(run_id)]
    assert kinds.count("tool_outcome_unknown") == 1


def test_the_dispatching_owner_waking_up_with_a_result_still_cannot_write_it(tmp_path):
    """The worst case for the fence: the zombie genuinely knows the answer.

    Its lease expired, a recovering process quarantined the send, and only then
    does the original process come back holding a real success. It still may not
    write it. A person may already have acted on the quarantine, and letting a
    process that lost its authority overwrite that is worse than losing what it
    saw. The attempt raises with a message a caller can log for the operator.
    """
    repo, owner = _repo(tmp_path)
    successor = OwnerRegistry(tmp_path).register()
    started = _start(repo, owner, lease_seconds=60)
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    effect_id = dispatch.effect["effect_id"]

    later = NOW + timedelta(seconds=120)
    repo.reconcile_expired_leases(now=later)
    taken = repo.acquire_lease(started.run["run_id"], successor, 600, now=later)
    repo.quarantine_effect(taken, effect_id, reason="owner_lease_expired", now=later)

    with pytest.raises(AgentRunEffectFenced) as caught:
        repo.record_effect_outcome(
            dispatch.lease, effect_id, ok=True, outcome_ref="gmail-msg-77", now=later
        )
    assert "settled by a person" in str(caught.value)
    effect = repo.list_effects(started.run["run_id"])[0]
    assert effect["status"] == EffectStatus.AMBIGUOUS
    # And the observation it carried did not leak onto the row either.
    assert effect["outcome_ref"] is None


def test_a_refused_dispatch_leaves_the_run_exactly_where_it_was(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    first = repo.dispatch_effect(
        started.lease,
        tool_name="gmail_send",
        arguments=SEND_ARGS,
        effect_id="effect-fixed",
        now=NOW,
    )
    before = repo.get_run(started.run["run_id"])

    with pytest.raises(AgentRunEffectFenced):
        repo.dispatch_effect(
            first.lease,
            tool_name="gmail_send",
            arguments=SEND_ARGS,
            effect_id="effect-fixed",
            now=NOW,
        )

    # The rolled-back transaction left no half-written checkpoint behind.
    assert repo.get_run(started.run["run_id"]) == before
    assert len(repo.list_checkpoints(started.run["run_id"])) == before[
        "checkpoint_sequence"
    ]
