"""Slice 4: restart and resume, cut at five points with a real signal.

Every cut in this file is a real one. A second Python process opens the run
store, registers an owner and holds its `flock`, drives the run to a named
point, and reports. The test then sends it SIGKILL. The child runs no cleanup,
writes no farewell, and cannot: the kernel releases its lock and nothing else.
That is the only evidence the recovery path is allowed to act on.

The five points are the ones the issue names: mid-model, mid-read-tool,
mid-approval, mid-write, and after terminal generation but before delivery.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from coworker.agent_run_approval import AgentRunEffectFenced, EffectStatus
from coworker.agent_run_owner import Liveness, OwnerRegistry
from coworker.agent_run_repository import AgentRunRepository
from coworker.agent_run_resume import (
    ResumeAction,
    ResumeVerdict,
    classify_resume,
    plan_restart,
    quarantine_unreported_effect,
    restart,
)

DESKTOP = str(Path(__file__).resolve().parents[1])
NOW = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)

# A real second process. It takes a real lease, drives the run to `cut`, says so
# on stdout, and then blocks forever waiting to be killed.
CHILD = """
import json, sys, time
sys.path.insert(0, sys.argv[1])
from coworker.agent_run_repository import AgentRunRepository

store, cut = sys.argv[2], sys.argv[3]
repo = AgentRunRepository(store)
owner = repo.registry.register()
started = repo.create_run(
    session_id="sess-cut", trigger="chat", goal="reach out to Dana",
    owner=owner, lease_seconds=3600,
)
lease = started.lease
report = {"run_id": started.run["run_id"], "owner_id": owner.owner_id}

if cut == "model":
    lease = repo.checkpoint(
        lease, kind="model_pending", payload={"step": 1, "provider": "openai"}
    ).lease
elif cut == "read_tool":
    lease = repo.checkpoint(lease, kind="model_completed", payload={"step": 1}).lease
    lease = repo.checkpoint(
        lease,
        kind="tool_pending",
        payload={"step": 1, "tool_name": "drive_read", "tool_call_id": "call-r"},
    ).lease
elif cut == "approval":
    lease = repo.checkpoint(lease, kind="model_completed", payload={"step": 1}).lease
    repo.checkpoint(
        lease,
        kind="waiting_approval",
        state="waiting_approval",
        payload={"step": 1, "tool_name": "gmail_send", "approval_id": "inbox-7"},
        approval_ids=["inbox-7"],
    )
    lease = None
elif cut == "approval_resolved":
    # The other side of the approval boundary: a person said Allow, the run
    # recorded it, and the process died before dispatching anything.
    lease = repo.checkpoint(lease, kind="model_completed", payload={"step": 1}).lease
    parked = repo.checkpoint(
        lease,
        kind="waiting_approval",
        state="waiting_approval",
        payload={"step": 1, "tool_name": "gmail_send", "approval_id": "inbox-7"},
        approval_ids=["inbox-7"],
    )
    lease = repo.resume_from_approval(
        started.run["run_id"], owner, approval_id="inbox-7", decision="allow",
        lease_seconds=3600,
    ).lease
elif cut == "write":
    lease = repo.checkpoint(lease, kind="model_completed", payload={"step": 1}).lease
    dispatch = repo.dispatch_effect(
        lease,
        tool_name="gmail_send",
        arguments={"to": "dana@ramp.test", "body": "Hello Dana."},
        tool_call_id="call-w",
        approval_id="inbox-7",
        step=1,
    )
    lease = dispatch.lease
    report["effect_id"] = dispatch.effect["effect_id"]
elif cut == "delivery":
    lease = repo.checkpoint(
        lease,
        kind="model_completed",
        payload={"step": 2, "text_length": 420},
        terminal_result={"status": "ok", "message_id": "msg-1", "text_length": 420},
    ).lease
elif cut == "unfenced_write":
    # A caller that skipped the fence: a consequential tool with no effect row.
    lease = repo.checkpoint(lease, kind="model_completed", payload={"step": 1}).lease
    lease = repo.checkpoint(
        lease,
        kind="tool_pending",
        payload={"step": 1, "tool_name": "gmail_send", "tool_call_id": "call-u"},
    ).lease
elif cut != "idle":
    raise SystemExit(f"unknown cut {cut}")

print(json.dumps(report))
sys.stdout.flush()
while True:
    time.sleep(3600)
"""


def _cut(tmp_path, cut):
    """Drive a real process to `cut`, then SIGKILL it mid-flight."""
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, DESKTOP, str(tmp_path), cut],
        stdout=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline()
    assert line, f"the child never reached the {cut} cut point"
    report = json.loads(line)
    proc.kill()
    assert proc.wait(timeout=30) != 0, "the child must be killed, not exit cleanly"
    return report


def _hold(tmp_path, cut):
    """Drive a real process to `cut` and leave it alive, holding its lock."""
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, DESKTOP, str(tmp_path), cut],
        stdout=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline()
    assert line, f"the child never reached the {cut} cut point"
    return proc, json.loads(line)


def _recovering(tmp_path):
    repo = AgentRunRepository(tmp_path)
    return repo, repo.registry.register()


def _only(outcome, run_id):
    matching = [item for item in outcome.verdicts if item.run_id == run_id]
    assert len(matching) == 1, matching
    return matching[0]


# --- cut 1: mid-model ----------------------------------------------------


def test_a_cut_mid_model_resumes_the_same_run(tmp_path):
    report = _cut(tmp_path, "model")
    repo, owner = _recovering(tmp_path)
    assert repo.registry.liveness_of(report["owner_id"], repo.registry.host) is (
        Liveness.DEAD
    )

    outcome = restart(repo, owner)

    verdict = _only(outcome, report["run_id"])
    assert verdict.action is ResumeAction.RESUME
    assert verdict.reason == "model_never_completed"
    assert verdict.cut_point == "model_pending"
    assert verdict.step == 1
    # The identity is the same run, not a new one.
    run = repo.get_run(report["run_id"])
    assert run["current_state"] == "interrupted"
    assert run["lease_owner"] is None
    assert repo.list_runs(session_id="sess-cut") == [run]
    assert [item["kind"] for item in repo.list_checkpoints(run["run_id"])] == [
        "run_started",
        "model_pending",
        "process_interrupted",
    ]
    # Nothing external was in flight, so there is nothing to quarantine.
    assert outcome.quarantined == ()
    assert repo.list_effects(run["run_id"]) == []


# --- cut 2: mid-read-tool ------------------------------------------------


def test_a_cut_mid_read_tool_resumes_because_the_tool_is_retry_safe(tmp_path):
    report = _cut(tmp_path, "read_tool")
    repo, owner = _recovering(tmp_path)

    outcome = restart(repo, owner)

    verdict = _only(outcome, report["run_id"])
    assert verdict.action is ResumeAction.RESUME
    assert verdict.reason == "tool_never_completed"
    assert verdict.tool_name == "drive_read"
    assert outcome.quarantined == ()
    assert repo.list_effects(report["run_id"]) == []


def test_the_same_cut_on_a_consequential_tool_refuses_to_resume(tmp_path):
    """Only the retry allowlist made the read-tool case safe. Change the tool."""
    report = _cut(tmp_path, "unfenced_write")
    repo, owner = _recovering(tmp_path)

    verdict = _only(restart(repo, owner), report["run_id"])

    assert verdict.action is ResumeAction.REVIEW
    assert verdict.tool_name == "gmail_send"
    assert "no effect record" in verdict.reason


# --- cut 3: mid-approval -------------------------------------------------


def test_a_cut_mid_approval_leaves_the_operator_owning_the_run(tmp_path):
    report = _cut(tmp_path, "approval")
    repo, owner = _recovering(tmp_path)

    outcome = restart(repo, owner)

    verdict = _only(outcome, report["run_id"])
    assert verdict.action is ResumeAction.NOTHING
    assert verdict.reason == "the run is parked on a person"
    run = repo.get_run(report["run_id"])
    # The approval is intact, unowned, and not reclassified by the restart.
    assert run["current_state"] == "waiting_approval"
    assert run["lease_owner"] is None
    assert run["approval_ids"] == ["inbox-7"]
    assert [item["kind"] for item in repo.list_checkpoints(run["run_id"])] == [
        "run_started",
        "model_completed",
        "waiting_approval",
    ]
    # A restart invents no interruption for work that had already parked.
    assert outcome.quarantined == ()


def test_a_cut_after_allow_but_before_dispatch_resumes_because_nothing_left_yet(
    tmp_path,
):
    """The dispatch commits before the call, so no dispatch means no call."""
    report = _cut(tmp_path, "approval_resolved")
    repo, owner = _recovering(tmp_path)

    verdict = _only(restart(repo, owner), report["run_id"])

    assert verdict.action is ResumeAction.RESUME
    assert verdict.reason == "approval_resolved_before_work_resumed"
    assert verdict.cut_point == "approval_resolved"
    # Nothing external is on record, which is the fact that makes this safe.
    assert repo.list_effects(report["run_id"]) == []
    assert repo.get_run(report["run_id"])["approval_ids"] == ["inbox-7"]


# --- cut 4: mid-write, the case with money on the other side -------------


def test_a_cut_mid_write_quarantines_the_send_and_never_retries_it(tmp_path):
    report = _cut(tmp_path, "write")
    repo, owner = _recovering(tmp_path)
    run_id, effect_id = report["run_id"], report["effect_id"]
    # Before recovery the dispatch is on record and no outcome ever was.
    assert repo.list_effects(run_id)[0]["status"] == EffectStatus.DISPATCHED

    outcome = restart(repo, owner)

    verdict = _only(outcome, run_id)
    assert verdict.action is ResumeAction.QUARANTINE
    assert verdict.effect_id == effect_id
    assert verdict.tool_name == "gmail_send"
    assert [item["effect_id"] for item in outcome.quarantined] == [effect_id]

    effect = repo.list_effects(run_id)[0]
    assert effect["status"] == EffectStatus.AMBIGUOUS
    assert effect["replay_class"] == "consequential"
    run = repo.get_run(run_id)
    assert run["current_state"] == "interrupted"
    assert run["lease_owner"] is None
    assert "tool_outcome_unknown" in [
        item["kind"] for item in repo.list_checkpoints(run_id)
    ]

    # A second restart finds it already quarantined and changes nothing.
    again = restart(repo, owner)
    assert _only(again, run_id).action is ResumeAction.REVIEW
    assert again.quarantined == ()
    assert repo.list_effects(run_id)[0] == effect

    # And no lease, old or new, can turn the unknown into an outcome.
    lease = repo.acquire_lease(run_id, owner, 600)
    assert lease is not None
    for ok in (True, False):
        with pytest.raises(AgentRunEffectFenced):
            repo.record_effect_outcome(lease, effect_id, ok=ok)
    assert repo.list_effects(run_id)[0]["status"] == EffectStatus.AMBIGUOUS


def test_a_quarantined_run_becomes_ordinary_work_again_only_after_a_person(tmp_path):
    report = _cut(tmp_path, "write")
    repo, owner = _recovering(tmp_path)
    run_id, effect_id = report["run_id"], report["effect_id"]
    restart(repo, owner)
    assert _only(restart(repo, owner), run_id).action is ResumeAction.REVIEW

    repo.resolve_quarantined_effect(
        effect_id,
        decision="resolved_failed",
        operator="fisher",
        note="checked the mailbox: nothing went out",
    )

    verdict = _only(restart(repo, owner), run_id)
    assert verdict.action is ResumeAction.RESUME
    assert repo.list_effects(run_id)[0]["resolved_by"] == "fisher"


# --- cut 5: after terminal generation, before delivery -------------------


def test_a_cut_after_terminal_generation_delivers_instead_of_regenerating(tmp_path):
    report = _cut(tmp_path, "delivery")
    repo, owner = _recovering(tmp_path)

    verdict = _only(restart(repo, owner), report["run_id"])

    assert verdict.action is ResumeAction.DELIVER
    assert verdict.reason == "the final result is on record and its delivery is not"
    assert verdict.cut_point == "model_completed"
    run = repo.get_run(report["run_id"])
    # The answer itself is not here, and must not be: only its shape.
    assert run["terminal_result"] == {
        "status": "ok",
        "message_id": "msg-1",
        "text_length": 420,
    }
    assert run["current_state"] == "interrupted"
    # DELIVER is distinguishable from RESUME, which is what stops a second
    # model call from being made and charged for.
    assert verdict.action is not ResumeAction.RESUME


# --- a live owner survives restart ---------------------------------------


def test_a_restart_never_touches_a_run_whose_owner_is_still_working_it(tmp_path):
    proc, report = _hold(tmp_path, "write")
    run_id = report["run_id"]
    try:
        repo, owner = _recovering(tmp_path)
        assert repo.registry.liveness_of(report["owner_id"], repo.registry.host) is (
            Liveness.ALIVE
        )

        outcome = restart(repo, owner)

        verdict = _only(outcome, run_id)
        assert verdict.action is ResumeAction.NOTHING
        assert verdict.reason == "another process holds the lease"
        run = repo.get_run(run_id)
        assert run["current_state"] == "running"
        assert run["lease_owner"] == report["owner_id"]
        # The dispatched send is left exactly as the live owner left it. It is
        # in flight, not abandoned, and quarantining it would be a lie.
        assert repo.list_effects(run_id)[0]["status"] == EffectStatus.DISPATCHED
        assert outcome.quarantined == ()
        assert repo.acquire_lease(run_id, owner, 600) is None
    finally:
        proc.kill()
        proc.wait(timeout=30)

    # Not vacuous: the same call acts the moment the owner is genuinely gone.
    outcome = restart(repo, owner)
    assert _only(outcome, run_id).action is ResumeAction.QUARANTINE
    assert [item["effect_id"] for item in outcome.quarantined] == [report["effect_id"]]


def test_a_restart_leaves_a_run_it_could_not_lease_for_a_later_pass(tmp_path):
    """Losing the race to quarantine is not a failure; it is someone else's turn."""
    repo, owner = _recovering(tmp_path)
    started = repo.create_run(
        session_id="sess-race",
        trigger="chat",
        goal="reach out to Dana",
        owner=owner,
        lease_seconds=3600,
        now=NOW,
    )
    dispatch = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments={"to": "d@t.test"}, now=NOW
    )
    verdicts = plan_restart(repo, now=NOW)
    verdict = [item for item in verdicts if item.run_id == started.run["run_id"]]
    # The live owner still holds it, so the plan says so and stops there.
    assert verdict[0].action is ResumeAction.NOTHING

    # Force the quarantine attempt anyway: it must decline, not force its way in.
    forced = quarantine_unreported_effect(
        repo,
        OwnerRegistry(tmp_path).register(),
        ResumeVerdict(
            run_id=started.run["run_id"],
            action=ResumeAction.QUARANTINE,
            reason="forced",
            effect_id=dispatch.effect["effect_id"],
        ),
        now=NOW,
    )
    assert forced is None
    assert repo.list_effects(started.run["run_id"])[0]["status"] == (
        EffectStatus.DISPATCHED
    )


# --- the classifier on its own -------------------------------------------


def test_a_terminal_run_is_never_resumed(tmp_path):
    repo, owner = _recovering(tmp_path)
    started = repo.create_run(
        session_id="sess-done",
        trigger="chat",
        goal="done",
        owner=owner,
        now=NOW,
    )
    repo.checkpoint(
        started.lease,
        kind="terminal",
        state="complete",
        terminal_result={"status": "ok", "text_length": 12},
        now=NOW,
    )

    run = repo.get_run(started.run["run_id"])
    verdict = classify_resume(run, repo.list_checkpoints(run["run_id"]), [])
    assert verdict.action is ResumeAction.NOTHING
    assert verdict.reason == "the run already finished"
    # A finished run is not even a candidate for the plan.
    assert [item.run_id for item in plan_restart(repo, now=NOW)] == []


def test_quarantine_is_refused_for_a_verdict_that_is_not_one(tmp_path):
    repo, owner = _recovering(tmp_path)
    started = repo.create_run(
        session_id="sess-x", trigger="chat", goal="x", owner=owner, now=NOW
    )
    verdict = classify_resume(repo.get_run(started.run["run_id"]), [], [])

    with pytest.raises(ValueError):
        quarantine_unreported_effect(repo, owner, verdict, now=NOW)


def test_an_expired_lease_is_reclaimed_before_anything_is_classified(tmp_path):
    repo, owner = _recovering(tmp_path)
    started = repo.create_run(
        session_id="sess-expiry",
        trigger="chat",
        goal="slow work",
        owner=owner,
        lease_seconds=60,
        now=NOW,
    )
    repo.checkpoint(started.lease, kind="model_pending", payload={"step": 1}, now=NOW)

    # Still inside the lease: the owner is working, so nothing is picked up.
    early = plan_restart(repo, now=NOW + timedelta(seconds=30))
    assert [item.action for item in early] == [ResumeAction.NOTHING]

    late = plan_restart(repo, now=NOW + timedelta(seconds=61))
    assert [item.action for item in late] == [ResumeAction.RESUME]
    assert repo.get_run(started.run["run_id"])["current_state"] == "interrupted"
