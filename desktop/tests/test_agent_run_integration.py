"""Slice 5: the Agent Run store wired into the paths that actually run.

Slices 1-4 built a fence and nothing entered it. Everything here is about
whether a production path now does, so almost every test drives a real turn,
a real HTTP approval, or a real scheduled job rather than calling the store.

Two habits, on purpose.

**Non-vacuity first.** A test that asserts "no second send happened" would pass
if the run never started. Each one first proves the path got where it claims to
have got -- a send attempt counted, an effect row present, an approval that
reached the gate -- and only then asserts the property.

**The ordering contract is observed, not narrated.** The strongest test in this
file reads `agent_run_effects` from a second database connection *while the
send is in flight*. A dispatch written after the call, or inside the same
transaction, is invisible from there and the test fails. Asserting that
`dispatch_effect` was called first would keep passing after the ordering was
reversed, so nothing below does that.
"""

from __future__ import annotations

import asyncio
import pathlib
import sqlite3
import subprocess
import sys
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from coworker.agent_run_approval import EffectStatus, replay_class, ReplayClass
from coworker.agent_run_dispatch import (
    AgentRunContext,
    AgentRunEffectQuarantined,
    AgentRunUnavailable,
    guarded_call,
    needs_fence,
)
from coworker.agent_run_owner import OwnerRegistry
from coworker.agent_run_reconcile import (
    contested_approvals,
    reconciled_status,
    review_queue,
    supersedes_inbox,
)
from coworker.agent_run_repository import AgentRunRepository
from coworker.agent_run_resume import ResumeAction, classify_resume, restart
from coworker.gmail import FakeGmail
from coworker.inbox import Inbox
from coworker.people import PersonStore
from coworker.permissions import ASK, AUTO, RETRY_SAFE
from coworker.provider import FakeProvider, StreamChunk, ToolCall
from coworker.server import TOKEN_HEADER, create_app
from coworker.store import ConversationStore
from coworker.tools import OPENAI_TOOLS
from coworker.turn import run_turn

TOKEN = "test-token-agent-run-integration"
HEADERS = {TOKEN_HEADER: TOKEN}
SID = "sess-agent-run"

BODY = "Hi Ada,\n\nWould Thursday work for a short call?\n\nFisher"
SUBJECT = "Thursday?"
ADA_EMAIL = "ada@analytic.example"
ACCOUNT = "director@sourcecado.test"


# --- harness ---------------------------------------------------------------


class OneToolProvider:
    """Asks for one named tool, then stops."""

    provider_id = "fake"
    model_id = "fake"

    def __init__(self, tool_name: str, arguments: Any = None) -> None:
        self.tool_name = tool_name
        self.arguments = arguments if arguments is not None else {}
        self.requests = 0
        self.calls: list[list[dict[str, Any]]] = []

    async def astream(self, *, messages, tools=None, context_id=None):
        self.calls.append(list(messages))
        index = self.requests
        self.requests += 1
        yield StreamChunk.started(provider=self.provider_id, model=self.model_id)
        if index > 0:
            yield StreamChunk(text_delta="Done.")
            yield StreamChunk(finish_reason="stop")
            return
        yield StreamChunk(text_delta="Working. ")
        yield StreamChunk(finish_reason="tool_calls")
        yield StreamChunk(
            tool_calls=[
                ToolCall(id="call_one", name=self.tool_name, arguments=self.arguments)
            ]
        )


def _runs(tmp_path) -> tuple[AgentRunRepository, Any]:
    repository = AgentRunRepository(tmp_path / "runs")
    return repository, repository.registry.register()


def _turn(
    tmp_path,
    *,
    provider,
    repository=None,
    owner=None,
    text="Do the thing.",
    sid=SID,
    store=None,
    people=None,
    wait=None,
    **kwargs,
):
    conv = store if store is not None else ConversationStore(tmp_path / "conv")
    person_store = people if people is not None else PersonStore(tmp_path / "people")

    async def _wait(_call_id: str) -> str:
        return wait or "allow"

    result = asyncio.run(
        run_turn(
            text=text,
            sid=sid,
            store=conv,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(conv),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={"people": person_store},
            wait_permission=_wait if wait is not None else None,
            agent_runs=repository,
            run_owner=owner,
            **kwargs,
        )
    )
    return result, conv


def _effects(repository: AgentRunRepository) -> list[dict[str, Any]]:
    return [
        effect
        for run in repository.list_runs(limit=100)
        for effect in repository.list_effects(run["run_id"])
    ]


def _app(tmp_path, gmail=None, **kwargs):
    return create_app(
        token=TOKEN, provider=None, state=tmp_path, gmail=gmail, **kwargs
    )


def _gmail(cls=FakeGmail, **kwargs) -> FakeGmail:
    gmail = cls(**kwargs)
    gmail.account_email = ACCOUNT
    return gmail


def _bound_person(app, *, email: str = ADA_EMAIL):
    people = app.state.people
    person = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L.",
        title="Head of Data",
        company="Analytic",
    )
    people.apply_enrichment(person["person_id"], name="Ada Lovelace", email=email)
    person = people.get(person["person_id"])
    session_id = app.state.store.create_session()["session_id"]
    people.bind_session(
        session_id, person["person_id"], expected_person_version=int(person["version"])
    )
    return person["person_id"], session_id


def _park_send(client, person_id, session_id):
    draft = client.post(
        f"/v1/people/{person_id}/outreach/draft",
        headers=HEADERS,
        json={"session_id": session_id, "subject": SUBJECT, "body": BODY},
    )
    assert draft.status_code == 201, draft.text
    drafted = draft.json()["draft"]
    parked = client.post(
        f"/v1/people/{person_id}/outreach/send-approval",
        headers=HEADERS,
        json={
            "session_id": session_id,
            "draft_id": drafted["id"],
            "reviewed_body_digest": drafted["body_digest"],
        },
    )
    assert parked.status_code == 201, parked.text
    return parked.json()["item"]["id"]


# ==========================================================================
# 1 -- the ordering contract, observed from inside the call
# ==========================================================================


class WatchingGmail(FakeGmail):
    """Gmail that reads the run store from a second connection mid-send.

    This is the whole ordering contract in one fixture. `send` runs between
    `dispatch_effect` and `record_effect_outcome`, so whatever it can see
    committed is what a process dying right here would have left behind.
    """

    def __init__(self, db_path) -> None:
        super().__init__()
        self.db_path = db_path
        self.seen: list[tuple[str, str]] = []

    def send(self, *, draft_id: str):
        connection = sqlite3.connect(self.db_path)
        try:
            self.seen = [
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT tool_name, status FROM agent_run_effects"
                ).fetchall()
            ]
        finally:
            connection.close()
        return super().send(draft_id=draft_id)


def test_the_dispatch_is_committed_before_the_send_and_the_outcome_after(tmp_path):
    """Dispatch commits before the call. Outcome commits after it.

    Read from a separate connection while Gmail is mid-send, so a dispatch
    written after the call -- or in the same transaction as it -- is not
    visible and this fails. It is the one property the whole slice rests on.
    """
    app = _app(tmp_path)
    gmail = WatchingGmail(app.state.agent_runs.path)
    gmail.account_email = ACCOUNT
    app.state.gmail = gmail
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    item_id = _park_send(client, person_id, session_id)

    res = client.post(
        f"/v1/inbox/{item_id}",
        headers=HEADERS,
        json={"decision": "allow", "actor": "Fisher", "scope": "once"},
    )

    # Non-vacuity: the send really happened, so the window really was entered.
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert len(gmail.sends) == 1

    # What a process dying inside the window would have left on disk.
    assert gmail.seen == [("gmail_send", str(EffectStatus.DISPATCHED))]

    # And after: the same effect, settled by the process that dispatched it.
    settled = _effects(app.state.agent_runs)
    assert [effect["status"] for effect in settled] == [
        str(EffectStatus.SUCCEEDED)
    ]
    assert settled[0]["approval_id"] == item_id
    assert settled[0]["replay_class"] == str(ReplayClass.CONSEQUENTIAL)


def test_a_send_that_fails_is_failed_and_not_ambiguous(tmp_path):
    """A tool that returns said what happened. Only silence is ambiguous."""

    class RefusingGmail(FakeGmail):
        def send(self, *, draft_id: str):
            self.send_attempts.append(draft_id)
            raise RuntimeError("Gmail refused the submission.")

    app = _app(tmp_path, _gmail(RefusingGmail))
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    item_id = _park_send(client, person_id, session_id)

    res = client.post(
        f"/v1/inbox/{item_id}",
        headers=HEADERS,
        json={"decision": "allow", "actor": "Fisher", "scope": "once"},
    )

    assert res.status_code == 200, res.text
    # Non-vacuity: Gmail was really reached and really refused.
    assert len(app.state.gmail.send_attempts) == 1
    assert app.state.gmail.sends == []
    assert [effect["status"] for effect in _effects(app.state.agent_runs)] == [
        str(EffectStatus.FAILED)
    ]
    assert app.state.agent_runs.list_quarantined_effects() == []


# ==========================================================================
# 2 -- which tools are fenced, derived and not listed
# ==========================================================================


def test_the_fenced_set_is_read_from_retry_safe_and_not_written_down():
    """No hand-written list. `RETRY_SAFE` decides, and the rest is fenced."""
    names = {
        str((schema.get("function") or {}).get("name") or "")
        for schema in OPENAI_TOOLS
    }
    names.discard("")
    assert len(names) > 10, "the tool registry did not load"

    for name in sorted(names):
        assert needs_fence(name) is (name not in RETRY_SAFE), name

    # Every approval-gated tool is fenced, because none is retry safe.
    assert ASK, "the ASK set is empty"
    for name in sorted(ASK):
        assert needs_fence(name), name
    # A tool the permission module has never heard of fails closed.
    assert needs_fence("a_tool_nobody_declared")
    # And a read stays cheap.
    assert not needs_fence("gmail_search")
    assert replay_class("gmail_search") is ReplayClass.SAFE


@pytest.mark.parametrize(
    "tool_name", sorted((AUTO | ASK) - RETRY_SAFE)
)
def test_every_consequential_tool_in_a_turn_opens_an_effect_record(
    tmp_path, tool_name, monkeypatch
):
    """Not just `gmail_send`. The trap in `docs/agent-runs.md` is this one.

    Driven over the real turn loop with the real permission gate, one test per
    tool that `RETRY_SAFE` does not cover, so a tool moving between the sets
    cannot quietly slip past the fence.
    """
    import coworker.turn as turn_module

    seen: list[str] = []

    def _execute(name, arguments, **kwargs):
        seen.append(name)
        return True, {"ok": True}

    monkeypatch.setattr(turn_module, "execute", _execute)
    repository, owner = _runs(tmp_path)

    _turn(
        tmp_path,
        provider=OneToolProvider(tool_name),
        repository=repository,
        owner=owner,
        wait="allow",
    )

    # Non-vacuity: the tool really ran. A zero-effect count on a tool that was
    # never reached would prove nothing.
    assert seen == [tool_name], f"{tool_name} never executed"
    effects = _effects(repository)
    assert [effect["tool_name"] for effect in effects] == [tool_name]
    assert effects[0]["status"] == str(EffectStatus.SUCCEEDED)
    assert effects[0]["replay_class"] == str(ReplayClass.CONSEQUENTIAL)


@pytest.mark.parametrize("tool_name", sorted(RETRY_SAFE))
def test_a_retry_safe_tool_costs_no_effect_record(tmp_path, tool_name, monkeypatch):
    """The fence is not a tax on reading. A safe tool opens no effect row."""
    import coworker.turn as turn_module

    seen: list[str] = []

    def _execute(name, arguments, **kwargs):
        seen.append(name)
        return True, {"ok": True}

    monkeypatch.setattr(turn_module, "execute", _execute)
    repository, owner = _runs(tmp_path)

    _turn(
        tmp_path,
        provider=OneToolProvider(tool_name),
        repository=repository,
        owner=owner,
    )

    assert seen == [tool_name], f"{tool_name} never executed"
    assert _effects(repository) == []
    # It is still on the record as work: the receipt sees reading too.
    run = repository.list_runs(limit=10)[0]
    kinds = [item["kind"] for item in repository.list_checkpoints(run["run_id"])]
    assert "tool_pending" in kinds and "tool_completed" in kinds


# ==========================================================================
# 3 -- the three triggers, each from its own production path
# ==========================================================================


def test_a_chat_turn_over_the_socket_creates_a_durable_run(tmp_path):
    app = _app(tmp_path)
    app.state.provider = FakeProvider(deltas=("Hello ", "world"))
    client = TestClient(app)
    sid = app.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hi", "session_id": sid})
        while ws.receive_json()["type"] not in ("turn_end", "error"):
            pass

    runs = app.state.agent_runs.list_runs(session_id=sid, limit=10)
    assert [run["trigger"] for run in runs] == ["chat"]
    assert runs[0]["current_state"] == "complete"
    # The run id is the turn's own, so a receipt and an event point at one thing.
    assert runs[0]["run_id"].startswith("run_")


def test_a_scheduled_routine_creates_a_run_with_the_scheduled_trigger(tmp_path):
    app = _app(tmp_path)
    app.state.provider = FakeProvider(deltas=("Reviewed.",))
    job = app.state.store.add_job("0 9 * * 1", "Review the shortlist.")

    recorded = app.state.scheduler.run_job(int(job["id"]))

    # Non-vacuity: the routine really ran to a receipt.
    assert recorded["status"] == "success"
    runs = app.state.agent_runs.list_runs(trigger="scheduled", limit=10)
    assert len(runs) == 1
    assert runs[0]["session_id"] == f"sched-{job['id']}"
    assert runs[0]["current_state"] == "complete"


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_a_queued_message_creates_a_run_with_the_queued_trigger(tmp_path):
    """The third trigger, from the queue drain the socket actually uses."""
    app = _app(tmp_path)
    app.state.provider = FakeProvider(deltas=("Queued answer.",))
    client = TestClient(app)
    sid = app.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json(
            {
                "type": "queue_add",
                "session_id": sid,
                "command_id": "queued-1",
                "item_id": "queued-item-1",
                "text": "answer this later",
            }
        )
        assert ws.receive_json()["type"] == "queue_snapshot"
        # `queue_add` only enqueues. Resume is what drains it.
        ws.send_json(
            {
                "type": "queue_resume",
                "session_id": sid,
                "command_id": "queued-resume",
            }
        )
        while ws.receive_json()["type"] not in ("turn_end", "error"):
            pass

    assert _wait_until(
        lambda: any(
            run["trigger"] == "queued_chat"
            for run in app.state.agent_runs.list_runs(session_id=sid, limit=10)
        )
    ), [run["trigger"] for run in app.state.agent_runs.list_runs(session_id=sid, limit=10)]


# ==========================================================================
# 4 -- restart: an unreported effect is quarantined, never replayed
# ==========================================================================


def test_startup_registers_an_owner_and_runs_one_reconciliation_pass(tmp_path):
    app = _app(tmp_path)

    assert app.state.run_owner is not None
    # The marker is held for the life of the process, which is what proof of
    # death later reads.
    assert (
        app.state.agent_runs.registry.liveness_of(
            app.state.run_owner.owner_id, app.state.run_owner.host
        ).value
        == "alive"
    )
    assert app.state.run_restart is not None
    assert app.state.run_restart.quarantined == ()


# The other half of a process that dies mid-send: a real process, really
# exiting. Proof of death is a kernel fact -- the `flock` an owner holds is
# released when the process exits and never before -- so simulating it by
# unlinking the marker would test something else entirely. A missing marker is
# `unknown`, and unknown never authorises a reclaim.
_DYING_OWNER = """
import sys
from coworker.agent_run_owner import OwnerRegistry
from coworker.agent_run_repository import AgentRunRepository

base = sys.argv[1]
repository = AgentRunRepository(base)
owner = OwnerRegistry(base).register("owner-that-dies")
started = repository.create_run(
    session_id="sess-agent-run", trigger="chat", goal="send it", owner=owner
)
commit = repository.dispatch_effect(
    started.lease, tool_name="gmail_send", arguments={"draft_id": "d1"}
)
print(started.run["run_id"])
print(commit.effect["effect_id"])
# Exit here. The outcome is never recorded and the kernel drops the lock.
"""


def test_a_process_that_died_mid_send_leaves_an_effect_a_person_must_settle(
    tmp_path,
):
    """The window, opened by a real process that then really exits.

    Restart quarantines it. It never resumes it and never retries the send.
    """
    base = tmp_path / "runs"
    dead = subprocess.run(
        [sys.executable, "-c", _DYING_OWNER, str(base)],
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).resolve().parents[1]),
    )
    assert dead.returncode == 0, dead.stderr
    run_id, effect_id = dead.stdout.split()

    repository = AgentRunRepository(base)
    survivor = repository.registry.register("owner-that-restarts")
    # Non-vacuity: before the restart the row still reads as owned, and the
    # effect is open. A test that skipped this could not tell a working
    # quarantine from a store that was empty all along.
    assert repository.get_run(run_id)["lease_owner"] == "owner-that-dies"
    assert repository.list_effects(run_id)[0]["status"] == str(
        EffectStatus.DISPATCHED
    )

    outcome = restart(repository, survivor)

    verdicts = {verdict.run_id: verdict.action for verdict in outcome.verdicts}
    assert verdicts[run_id] is ResumeAction.QUARANTINE
    assert [effect["effect_id"] for effect in outcome.quarantined] == [effect_id]
    held = repository.list_quarantined_effects()
    assert [effect["status"] for effect in held] == [str(EffectStatus.AMBIGUOUS)]
    # The run comes to rest interrupted, holding no lease.
    settled_run = repository.get_run(run_id)
    assert settled_run["current_state"] == "interrupted"
    assert settled_run["lease_owner"] is None


def test_a_restart_never_takes_a_lease_from_an_owner_that_is_still_alive(tmp_path):
    """The reason restart cannot just assume every previous owner is dead."""
    base = tmp_path / "runs"
    repository = AgentRunRepository(base)
    living = repository.registry.register("owner-still-working")
    started = repository.create_run(
        session_id=SID, trigger="chat", goal="send it", owner=living
    )
    repository.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments={"draft_id": "d1"}
    )

    second = AgentRunRepository(base)
    second.registry = repository.registry
    outcome = restart(second, second.registry.register("owner-second-sidecar"))

    verdicts = {verdict.run_id: verdict.action for verdict in outcome.verdicts}
    assert verdicts[started.run["run_id"]] is ResumeAction.NOTHING
    assert outcome.quarantined == ()
    assert second.list_quarantined_effects() == []


def test_a_quarantined_effect_is_never_resumed_and_never_retried(tmp_path):
    """`REVIEW`, not `RESUME`. Effects outrank the run's own state."""
    repository = AgentRunRepository(tmp_path / "runs")
    registry = OwnerRegistry(tmp_path / "runs")
    owner = registry.register("owner-quarantine")
    started = repository.create_run(
        session_id=SID, trigger="chat", goal="send it", owner=owner
    )
    commit = repository.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments={"draft_id": "d1"}
    )
    repository.quarantine_effect(
        commit.lease, commit.effect["effect_id"], reason="the process died"
    )

    run = repository.get_run(started.run["run_id"])
    verdict = classify_resume(
        run,
        repository.list_checkpoints(run["run_id"]),
        repository.list_effects(run["run_id"]),
    )

    assert verdict.action is ResumeAction.REVIEW
    assert verdict.effect_id == commit.effect["effect_id"]
    # A second restart does not quarantine it again or move it on.
    again = restart(repository, owner)
    assert again.quarantined == ()
    assert [item.action for item in again.verdicts] == [ResumeAction.REVIEW]


def test_a_cancelled_call_quarantines_rather_than_calling_it_failed(tmp_path):
    """Stop pressed mid-send. Nothing observed the outcome, so nothing claims one."""
    repository, owner = _runs(tmp_path)
    context = AgentRunContext.start(
        repository, owner, session_id=SID, trigger="chat", goal="send"
    )

    async def _cancelled() -> tuple[bool, dict[str, Any]]:
        raise asyncio.CancelledError()

    async def _drive() -> None:
        with pytest.raises(asyncio.CancelledError):
            await guarded_call(
                context,
                tool_name="gmail_send",
                arguments={"draft_id": "d1"},
                call=_cancelled,
            )

    asyncio.run(_drive())

    held = repository.list_quarantined_effects()
    assert [effect["tool_name"] for effect in held] == ["gmail_send"]
    assert held[0]["status"] == str(EffectStatus.AMBIGUOUS)


def test_a_raised_call_quarantines_and_the_turn_does_not_carry_on(tmp_path):
    repository, owner = _runs(tmp_path)
    context = AgentRunContext.start(
        repository, owner, session_id=SID, trigger="chat", goal="send"
    )

    async def _explodes() -> tuple[bool, dict[str, Any]]:
        raise RuntimeError("the socket died mid-request")

    async def _drive() -> None:
        with pytest.raises(AgentRunEffectQuarantined):
            await guarded_call(
                context,
                tool_name="gmail_send",
                arguments={"draft_id": "d1"},
                call=_explodes,
            )

    asyncio.run(_drive())
    assert len(repository.list_quarantined_effects()) == 1
    assert context.closed


# ==========================================================================
# 5 -- reconciliation: the run store is the fence of record
# ==========================================================================


def test_the_run_store_overrides_an_inbox_that_calls_an_unknown_outcome_interrupted():
    """The documented rule, as a rule.

    The inbox knows a claimant vanished. Only the run store knows a call was
    dispatched, because only its write ordering brackets that call.
    """
    effect = {
        "effect_id": "effect-1",
        "run_id": "run-1",
        "tool_name": "gmail_send",
        "approval_id": "send_1",
        "status": str(EffectStatus.AMBIGUOUS),
    }
    approval = {"id": "send_1", "execution_status": "interrupted"}

    assert reconciled_status(effect, approval) == str(EffectStatus.AMBIGUOUS)
    assert supersedes_inbox(effect, approval) is True
    # And the other direction: a send the run store saw succeed is not an
    # interruption, whatever the inbox concluded about its claimant.
    succeeded = {**effect, "status": str(EffectStatus.SUCCEEDED)}
    assert reconciled_status(succeeded, approval) == str(EffectStatus.SUCCEEDED)
    assert supersedes_inbox(succeeded, approval) is True
    # With no effect record there is nothing to override.
    assert reconciled_status(None, approval) == "interrupted"
    assert supersedes_inbox(None, approval) is False


def test_an_interrupted_approval_with_a_quarantined_effect_is_reported_as_ambiguous(
    tmp_path,
):
    """End to end over both real stores, not two dicts."""
    conversations = ConversationStore(tmp_path / "conv")
    repository = AgentRunRepository(tmp_path / "runs")
    owner = repository.registry.register()
    parked = conversations.park_inbox(
        "send_1", "gmail_send", {"draft_id": "d1"}, session_id=SID
    )
    conversations.decide_and_claim_inbox_execution(
        parked["id"], "allow", actor="Fisher", scope="once", claimant="http:1"
    )
    started = repository.create_run(
        session_id=SID, trigger="chat", goal="send", owner=owner
    )
    commit = repository.dispatch_effect(
        started.lease,
        tool_name="gmail_send",
        arguments={"draft_id": "d1"},
        approval_id=parked["id"],
    )
    # The process dies. Each store reconciles independently, and they disagree.
    ConversationStore(tmp_path / "conv")
    repository.quarantine_effect(
        commit.lease, commit.effect["effect_id"], reason="owner died"
    )

    reopened = ConversationStore(tmp_path / "conv")
    item = reopened.get_inbox(parked["id"])
    assert item["execution_status"] == "interrupted", "the inbox never disagreed"

    rows = review_queue(repository, reopened)
    assert len(rows) == 1
    assert rows[0]["status"] == str(EffectStatus.AMBIGUOUS)
    assert rows[0]["inbox_claim"] == "interrupted"
    assert rows[0]["supersedes_inbox"] is True
    assert rows[0]["fence_of_record"] == "agent_run_store"
    assert rows[0]["needs_a_person"] is True
    assert rows[0]["evidence"] == "ambiguous"
    assert rows[0]["approval"]["id"] == parked["id"]

    contested = contested_approvals(repository, reopened.list_inbox(pending_only=False))
    assert [row["effect_id"] for row in contested] == [commit.effect["effect_id"]]


# ==========================================================================
# 6 -- the review queue an operator actually uses
# ==========================================================================


def _quarantined_send(app, client):
    """One approved send whose executor died inside the window."""
    person_id, session_id = _bound_person(app)
    item_id = _park_send(client, person_id, session_id)
    claim = app.state.store.decide_and_claim_inbox_execution(
        item_id, "allow", actor="Fisher", scope="once", claimant="http:dead"
    )
    assert claim["owned"] is True, "the claim never landed"
    started = app.state.agent_runs.create_run(
        session_id=session_id,
        trigger="chat",
        goal="send",
        owner=app.state.run_owner,
    )
    commit = app.state.agent_runs.dispatch_effect(
        started.lease,
        tool_name="gmail_send",
        arguments={"draft_id": "d1"},
        approval_id=item_id,
    )
    app.state.agent_runs.quarantine_effect(
        commit.lease, commit.effect["effect_id"], reason="owner died mid-send"
    )
    return item_id, commit.effect["effect_id"]


def test_the_review_queue_shows_what_was_authorized_and_what_can_be_decided(
    tmp_path,
):
    app = _app(tmp_path, _gmail())
    client = TestClient(app)
    item_id, effect_id = _quarantined_send(app, client)

    res = client.get("/v1/agent-run-effects/quarantine", headers=HEADERS)

    assert res.status_code == 200, res.text
    body = res.json()
    assert [row["effect_id"] for row in body["effects"]] == [effect_id]
    row = body["effects"][0]
    assert row["tool_name"] == "gmail_send"
    assert row["approval_id"] == item_id
    assert row["needs_a_person"] is True
    # The operator sees the binding they approved: recipient, subject, account.
    resource = row["approval"]["resource"]
    assert resource["to"] == ADA_EMAIL
    assert resource["subject"] == SUBJECT
    # And never the body.
    assert "body" not in resource
    assert body["decisions"] == [
        "abandoned",
        "resolved_failed",
        "resolved_succeeded",
    ]


def test_an_operator_settles_a_quarantined_effect_and_it_leaves_the_queue(tmp_path):
    app = _app(tmp_path, _gmail())
    client = TestClient(app)
    _item_id, effect_id = _quarantined_send(app, client)

    res = client.post(
        f"/v1/agent-run-effects/quarantine/{effect_id}",
        headers=HEADERS,
        json={
            "decision": "resolved_succeeded",
            "operator": "Fisher",
            "note": "Checked the Sent folder; it went out.",
        },
    )

    assert res.status_code == 200, res.text
    settled = res.json()["effect"]
    assert settled["status"] == "resolved_succeeded"
    assert settled["resolved_by"] == "Fisher"
    assert client.get("/v1/agent-run-effects/quarantine", headers=HEADERS).json()[
        "effects"
    ] == []
    # Settled is final: a second decision is refused, not silently applied.
    again = client.post(
        f"/v1/agent-run-effects/quarantine/{effect_id}",
        headers=HEADERS,
        json={"decision": "abandoned", "operator": "Fisher"},
    )
    assert again.status_code == 409, again.text


def test_a_machine_outcome_is_not_a_decision_a_person_may_make(tmp_path):
    app = _app(tmp_path, _gmail())
    client = TestClient(app)
    _item_id, effect_id = _quarantined_send(app, client)

    res = client.post(
        f"/v1/agent-run-effects/quarantine/{effect_id}",
        headers=HEADERS,
        json={"decision": "succeeded", "operator": "Fisher"},
    )

    assert res.status_code == 400, res.text
    assert "machine outcome" in res.json()["error"]
    # It is still quarantined, so nothing was half-applied.
    assert len(app.state.agent_runs.list_quarantined_effects()) == 1


def test_settling_an_effect_needs_a_named_person(tmp_path):
    app = _app(tmp_path, _gmail())
    client = TestClient(app)
    _item_id, effect_id = _quarantined_send(app, client)

    res = client.post(
        f"/v1/agent-run-effects/quarantine/{effect_id}",
        headers=HEADERS,
        json={"decision": "abandoned", "operator": ""},
    )

    assert res.status_code == 400, res.text
    assert len(app.state.agent_runs.list_quarantined_effects()) == 1


# ==========================================================================
# 7 -- the two halves of at-most-once, composed and not duplicated
# ==========================================================================


def test_the_claim_stops_a_second_attempt_and_the_fence_records_the_first(tmp_path):
    """One claim, one send, one effect record. Neither half repeats the other.

    The inbox claim is what makes this the only executor. The run store never
    decides whether the call may happen; it records that it did.
    """
    app = _app(tmp_path, _gmail())
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    item_id = _park_send(client, person_id, session_id)

    first = client.post(
        f"/v1/inbox/{item_id}",
        headers=HEADERS,
        json={"decision": "allow", "actor": "Fisher", "scope": "once"},
    )
    second = client.post(
        f"/v1/inbox/{item_id}",
        headers=HEADERS,
        json={"decision": "allow", "actor": "Fisher", "scope": "once"},
    )

    assert first.status_code == 200 and first.json()["ok"] is True
    assert second.status_code == 200
    # One send, whatever the second request thought it was doing.
    assert len(app.state.gmail.sends) == 1
    assert len(app.state.gmail.send_attempts) == 1
    # And exactly one effect record: the fence did not open a second window.
    effects = _effects(app.state.agent_runs)
    assert len(effects) == 1
    assert effects[0]["status"] == str(EffectStatus.SUCCEEDED)


def test_an_interrupted_claim_is_never_re_executed(tmp_path):
    """The claim's half, stated. Only `pending` is claimable."""
    app = _app(tmp_path, _gmail())
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    item_id = _park_send(client, person_id, session_id)
    claim = app.state.store.decide_and_claim_inbox_execution(
        item_id, "allow", actor="Fisher", scope="once", claimant="http:dead"
    )
    assert claim["owned"] is True

    # The claimant dies; a restart closes the claim without replaying it.
    ConversationStore(tmp_path)
    reopened = app.state.store.get_inbox(item_id)
    assert reopened["execution_status"] == "interrupted"

    again = app.state.store.decide_and_claim_inbox_execution(
        item_id, "allow", actor="Fisher", scope="once", claimant="http:new"
    )
    assert again["claimed"] is False
    assert again["owned"] is False
    assert app.state.gmail.sends == []


# ==========================================================================
# 8 -- approvals move the run through the door, not around it
# ==========================================================================


def test_an_approved_turn_parks_its_run_and_comes_back_through_the_door(
    tmp_path, monkeypatch
):
    import coworker.turn as turn_module

    monkeypatch.setattr(
        turn_module, "execute", lambda name, arguments, **kwargs: (True, {"ok": True})
    )
    repository, owner = _runs(tmp_path)

    _turn(
        tmp_path,
        provider=OneToolProvider("gmail_send"),
        repository=repository,
        owner=owner,
        wait="allow",
    )

    run = repository.list_runs(limit=10)[0]
    kinds = [item["kind"] for item in repository.list_checkpoints(run["run_id"])]
    assert "waiting_approval" in kinds, "the run never parked on the person"
    assert "approval_resolved" in kinds, "the run never came back through the door"
    # The approval is on the run row, which is what the door checks against.
    assert run["approval_ids"] == ["call_one"]
    assert run["current_state"] == "complete"


def test_a_denied_approval_still_returns_the_run_to_its_owner(
    tmp_path, monkeypatch
):
    import coworker.turn as turn_module

    monkeypatch.setattr(
        turn_module, "execute", lambda name, arguments, **kwargs: (True, {"ok": True})
    )
    repository, owner = _runs(tmp_path)

    _turn(
        tmp_path,
        provider=OneToolProvider("gmail_send"),
        repository=repository,
        owner=owner,
        wait="deny",
    )

    run = repository.list_runs(limit=10)[0]
    # A denial is not a parked run left behind: it reaches a terminal state.
    assert run["current_state"] in {"complete", "partial"}
    assert _effects(repository) == [], "a denied call must open no effect record"


# ==========================================================================
# 9 -- failing closed
# ==========================================================================


def test_a_caller_that_supplied_a_run_store_and_got_no_run_refuses_to_send(tmp_path):
    """The distinction the fence turns on.

    A caller that never armed the fence is not fenced. A caller that armed it
    and got nothing must not send, and this is that case.
    """
    repository, owner = _runs(tmp_path)
    disarmed = AgentRunContext.disarmed(repository, owner, reason="disk is full")
    calls: list[str] = []

    def _call(name: str):
        async def _run() -> tuple[bool, dict[str, Any]]:
            calls.append(name)
            return True, {"ok": True}

        return _run

    async def _drive() -> None:
        with pytest.raises(AgentRunUnavailable):
            await guarded_call(
                disarmed,
                tool_name="gmail_send",
                arguments={},
                call=_call("gmail_send"),
            )
        # A read still runs: the fence stops sends, not the product.
        ok, _result = await guarded_call(
            disarmed,
            tool_name="gmail_search",
            arguments={},
            call=_call("gmail_search"),
        )
        assert ok is True

    asyncio.run(_drive())
    assert calls == ["gmail_search"]
    assert _effects(repository) == []


def test_a_turn_whose_run_store_will_not_open_a_run_refuses_consequential_work(
    tmp_path, monkeypatch
):
    import coworker.turn as turn_module

    executed: list[str] = []

    def _execute(name, arguments, **kwargs):
        executed.append(name)
        return True, {"ok": True}

    monkeypatch.setattr(turn_module, "execute", _execute)
    repository, owner = _runs(tmp_path)
    monkeypatch.setattr(
        repository,
        "create_run",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("disk is full")),
    )

    result, conv = _turn(
        tmp_path,
        provider=OneToolProvider("gmail_send"),
        repository=repository,
        owner=owner,
        wait="allow",
    )

    assert executed == [], "the send was attempted with no way to record it"
    # It is reported, not silently dropped.
    tool_results = [
        message
        for message in conv.load(SID)
        if message.get("role") == "tool"
    ]
    assert tool_results, "the turn never reached the tool step"
    assert "could not record this action durably" in tool_results[-1]["content"]
    assert result["status"] in {"partial", "ok"}
