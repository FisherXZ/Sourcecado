"""S5: one unified run receipt — operational evidence, never a second transcript."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from coworker.agent_run_repository import AgentRunRepository
from coworker.run_ledger import (
    CHECKPOINT_RETENTION_DAYS,
    MAX_QUERY_LIMIT,
    RunLedger,
)
from coworker.run_evidence import Evidence
from coworker.run_receipt import RECEIPT_SECTIONS
from coworker.store import ConversationStore

GOAL = "Find three Codeology leads at Ramp"
EPOCH = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def _ledger(tmp_path, *, approvals=None):
    repo = AgentRunRepository(tmp_path / "runs")
    return RunLedger(repo, approvals=approvals), repo, repo.registry.register()


def _start(repo, owner, **kwargs):
    return repo.create_run(
        session_id=kwargs.pop("session_id", "sess-1"),
        trigger=kwargs.pop("trigger", "chat"),
        goal=kwargs.pop("goal", GOAL),
        owner=owner,
        **kwargs,
    )


def _complete(repo, started, *, at=None):
    return repo.checkpoint(
        started.lease,
        kind="terminal",
        state="complete",
        terminal_result={"status": "complete", "text": "the answer the operator reads"},
        now=at,
    )


# --- criterion 1: one shape for every kind of run -------------------------


def test_every_trigger_and_ending_shares_one_receipt_shape(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    shapes = []

    chat = _start(repo, owner, session_id="sess-chat", trigger="chat")
    _complete(repo, chat)
    shapes.append(chat.run["run_id"])

    queued = _start(repo, owner, session_id="sess-queue", trigger="queued_chat")
    shapes.append(queued.run["run_id"])

    scheduled = _start(repo, owner, session_id="sched-7", trigger="scheduled")
    repo.checkpoint(scheduled.lease, kind="terminal", state="stopped")
    shapes.append(scheduled.run["run_id"])

    broken = _start(repo, owner, session_id="sess-fail")
    repo.checkpoint(
        broken.lease,
        kind="terminal",
        state="failed",
        terminal_result={"status": "failed", "error_class": "ProviderError"},
    )
    shapes.append(broken.run["run_id"])

    resumed = _start(repo, owner, session_id="sess-resume")
    repo.checkpoint(
        resumed.lease, kind="process_interrupted", state="interrupted",
        payload={"reason": "owner_process_dead"},
    )
    lease = repo.acquire_lease(resumed.run["run_id"], owner, 120)
    commit = repo.checkpoint(lease, kind="model_pending", state="running")
    repo.checkpoint(commit.lease, kind="terminal", state="complete")
    shapes.append(resumed.run["run_id"])

    receipts = [ledger.receipt(run_id) for run_id in shapes]
    assert all(receipt is not None for receipt in receipts)
    first = set(receipts[0])
    assert RECEIPT_SECTIONS <= first
    for receipt in receipts[1:]:
        assert set(receipt) == first
        for section in RECEIPT_SECTIONS:
            assert set(receipt[section]) == set(receipts[0][section]), section
    assert [receipt["run"]["trigger"] for receipt in receipts] == [
        "chat", "queued_chat", "scheduled", "chat", "chat",
    ]
    assert [receipt["run"]["state"] for receipt in receipts] == [
        "complete", "running", "stopped", "failed", "complete",
    ]


# --- criterion 2: the receipt is the whole operational record --------------


def test_a_successful_run_receipt_carries_the_whole_operational_record(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    started = _start(
        repo, owner, person_id="person-7", provider_model_id="fake-model", now=EPOCH
    )
    first = repo.checkpoint(
        started.lease,
        kind="model_pending",
        payload={
            "step": 1,
            "attempt_id": "att-1",
            "provider": "openai",
            "model_id": "fake-model",
            "persona_id": "sourcing",
            "prompt_version": "2026-08-25",
        },
        now=EPOCH,
    )
    second = repo.checkpoint(
        first.lease,
        kind="model_completed",
        payload={"attempt_id": "att-1", "status": "ok", "duration_ms": 1200},
        usage={"input_tokens": 900, "output_tokens": 120},
        now=EPOCH + timedelta(seconds=2),
    )
    third = repo.checkpoint(
        second.lease,
        kind="tool_pending",
        payload={"tool_call_id": "call-1", "tool_name": "apollo_search_people"},
        now=EPOCH + timedelta(seconds=3),
    )
    fourth = repo.checkpoint(
        third.lease,
        kind="tool_completed",
        payload={
            "tool_call_id": "call-1",
            "tool_name": "apollo_search_people",
            "status": "ok",
            "item_count": 3,
            "reason": "shortlist within credit budget",
        },
        source_refs=[
            {
                "id": "src-1",
                "title": "Ramp careers",
                "url": "https://ramp.test/jobs",
                "provider": "web",
            }
        ],
        artifact_refs=[{"id": "art-1", "artifact_type": "brief", "title": "Ramp brief"}],
        now=EPOCH + timedelta(seconds=4),
    )
    repo.checkpoint(
        fourth.lease,
        kind="terminal",
        state="complete",
        terminal_result={"status": "complete", "text": "three leads found"},
        now=EPOCH + timedelta(seconds=6),
    )

    receipt = ledger.receipt(started.run["run_id"])
    run = receipt["run"]
    assert run["run_id"] == started.run["run_id"]
    assert run["session_id"] == "sess-1"
    assert run["person_id"] == "person-7"
    assert run["trigger"] == "chat"
    assert run["goal_fingerprint"] == started.run["goal_fingerprint"]
    assert run["duration_ms"] == 6000
    assert run["owned"] is False

    assert receipt["prompt"] == {
        "evidence": Evidence.PRESENT,
        "persona_id": "sourcing",
        "prompt_version": "2026-08-25",
    }
    assert receipt["model_attempts"]["evidence"] == Evidence.PRESENT
    assert receipt["model_attempts"]["attempt_count"] == 1
    assert receipt["model_attempts"]["attempts"][0]["model_id"] == "fake-model"
    assert receipt["model_attempts"]["attempts"][0]["outcome"] == "completed"
    assert receipt["usage"] == {
        "evidence": Evidence.PRESENT,
        "totals": {"input_tokens": 900, "output_tokens": 120},
    }
    assert receipt["tools"]["evidence"] == Evidence.PRESENT
    assert receipt["tools"]["calls"][0]["tool_name"] == "apollo_search_people"
    assert receipt["tools"]["calls"][0]["lifecycle"] == "completed"
    assert receipt["sources"]["refs"][0]["url"] == "https://ramp.test/jobs"
    assert receipt["artifacts"]["refs"][0]["id"] == "art-1"
    assert receipt["outcome"]["evidence"] == Evidence.PRESENT
    assert receipt["outcome"]["state"] == "complete"
    assert receipt["outcome"]["open"] is False
    assert receipt["outcome"]["result"] == {
        "status": "complete",
        "text_length": len("three leads found"),
    }
    assert receipt["record"]["complete"] is True
    assert receipt["record"]["checkpoints_stored"] == 6
    rationale = receipt["rationale"]["notes"]
    assert {note["reason"] for note in rationale} == {"shortlist within credit budget"}
    assert receipt["recovery"]["evidence"] == Evidence.ABSENT
    assert receipt["approvals"]["evidence"] == Evidence.ABSENT


# --- criterion 3: evidence, never content ---------------------------------


PLANTED = {
    "api_key": "sk-proj-QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ",
    "bearer": "Bearer QQQQQQQQQQQQQQQQQQQQQQQQ",
    "message": "the director wrote something private here",
    "reasoning": "step one I secretly suspect the candidate is lying",
    "command_output": "drwxr-xr-x 5 fisher staff 160 payroll.csv",
    "draft_body": "Hi Dana, I wanted to reach out about Codeology",
}


def _run_full_of_planted_content(repo, owner, *, approval_id="ap-leak"):
    started = _start(repo, owner, person_id="person-leak")
    first = repo.checkpoint(
        started.lease,
        kind="tool_pending",
        payload={
            "tool_call_id": "call-leak",
            "tool_name": "gmail_draft",
            # Every one of these is either off the allowlist or redacted.
            "arguments": {"body": PLANTED["draft_body"], "api_key": PLANTED["api_key"]},
            "message": PLANTED["message"],
            "reasoning": PLANTED["reasoning"],
            "stdout": PLANTED["command_output"],
            "error_summary": (
                f"authorization: {PLANTED['bearer']} failed with {PLANTED['api_key']}"
            ),
        },
    )
    second = repo.checkpoint(
        first.lease,
        kind="tool_completed",
        payload={"tool_call_id": "call-leak", "tool_name": "gmail_draft", "status": "ok"},
        source_refs=[
            {
                "id": "src-leak",
                "title": f"Ramp brief {PLANTED['api_key']}",
                "url": f"https://ramp.test/doc?access_token={PLANTED['api_key']}",
            }
        ],
        artifact_refs=[{"id": "art-leak", "artifact_type": "draft", "title": "Outreach draft"}],
        approval_ids=[approval_id],
    )
    repo.checkpoint(
        second.lease,
        kind="terminal",
        state="complete",
        terminal_result={"status": "complete", "text": PLANTED["draft_body"]},
    )
    return started.run["run_id"]


def _blob(receipt) -> str:
    return json.dumps(receipt, sort_keys=True, default=str)


def test_the_receipt_is_operational_evidence_and_never_content(tmp_path):
    """Non-vacuous: the receipt must first prove it retained the real fields."""
    store = ConversationStore(tmp_path / "club")
    store.park_inbox(
        "ap-leak",
        "gmail_draft",
        # An owner-native approval carries the raw tool arguments. The ledger
        # joins against it for authority and must copy none of them.
        {"body": PLANTED["draft_body"], "api_key": PLANTED["api_key"]},
        reason=PLANTED["message"],
        session_id="sess-1",
    )
    store.resolve_inbox("ap-leak", "allow", actor="director")
    ledger, repo, owner = _ledger(tmp_path, approvals=store)
    run_id = _run_full_of_planted_content(repo, owner)

    receipt = ledger.receipt(run_id)
    assert receipt is not None
    assert receipt["run"]["run_id"] == run_id
    assert receipt["run"]["person_id"] == "person-leak"
    assert receipt["tools"]["calls"][0]["tool_name"] == "gmail_draft"
    assert receipt["tools"]["calls"][0]["lifecycle"] == "completed"
    assert receipt["sources"]["refs"][0]["url"] == "https://ramp.test/doc"
    assert receipt["artifacts"]["refs"][0]["artifact_type"] == "draft"
    assert receipt["approvals"]["decisions"][0]["decision"] == "allow"
    assert receipt["outcome"]["result"]["text_length"] == len(PLANTED["draft_body"])
    assert receipt["rationale"]["notes"], "a rationale note must survive"

    rendered = _blob(receipt)
    assert "gmail_draft" in rendered, "the receipt must really be populated"
    for secret in PLANTED.values():
        assert secret not in rendered, secret
    assert "access_token" not in rendered


def test_the_receipt_projection_is_its_own_allowlist(tmp_path):
    """The read side must hold even when the write side's allowlist did not.

    Without this the projection tests are vacuous: `checkpoint_payload` strips
    the planted keys before they are ever stored, so a receipt that echoed
    whole payloads would still look clean.
    """
    ledger, repo, owner = _ledger(tmp_path)
    started = _start(repo, owner)
    commit = repo.checkpoint(
        started.lease,
        kind="tool_completed",
        payload={"tool_call_id": "call-1", "tool_name": "web_search", "status": "ok"},
    )
    repo.checkpoint(commit.lease, kind="terminal", state="complete")
    smuggled = json.dumps(
        {
            "tool_call_id": "call-1",
            "tool_name": "web_search",
            "status": "ok",
            "arguments": {"body": PLANTED["draft_body"]},
            "message": PLANTED["message"],
            "reasoning": PLANTED["reasoning"],
            "stdout": PLANTED["command_output"],
        }
    )
    with sqlite3.connect(repo.path) as db:
        db.execute(
            "UPDATE agent_run_checkpoints SET payload = ? "
            "WHERE run_id = ? AND sequence = 2",
            (smuggled, started.run["run_id"]),
        )
        db.execute(
            "UPDATE agent_runs SET usage = ? WHERE run_id = ?",
            (json.dumps({"input_tokens": 12, "note": PLANTED["message"]}),
             started.run["run_id"]),
        )

    receipt = ledger.receipt(started.run["run_id"])
    call = receipt["tools"]["calls"][0]
    assert call["tool_name"] == "web_search"
    assert call["status"] == "ok"
    assert receipt["usage"]["totals"] == {"input_tokens": 12}
    summary = ledger.query(run_id=started.run["run_id"])["runs"][0]
    assert summary["usage"] == {"input_tokens": 12}

    rendered = _blob(receipt) + _blob(summary)
    assert "web_search" in rendered, "the receipt must really be populated"
    for secret in PLANTED.values():
        assert secret not in rendered, secret
    assert "arguments" not in rendered


def test_query_results_carry_pointers_and_never_content(tmp_path):
    store = ConversationStore(tmp_path / "club")
    ledger, repo, owner = _ledger(tmp_path, approvals=store)
    run_id = _run_full_of_planted_content(repo, owner)

    page = ledger.query(session_id="sess-1")
    assert [row["run_id"] for row in page["runs"]] == [run_id]
    assert page["runs"][0]["person_id"] == "person-leak"
    assert page["runs"][0]["source_count"] == 1
    assert page["runs"][0]["outcome_status"] == "complete"

    rendered = _blob(page)
    assert run_id in rendered
    for secret in PLANTED.values():
        assert secret not in rendered, secret


# --- criterion 6: distinguished, never inferred ---------------------------


def test_no_approval_requested_is_not_the_same_as_not_knowing(tmp_path):
    ledger, repo, owner = _ledger(tmp_path, approvals=_FakeApprovals({}))
    settled = _start(repo, owner, session_id="sess-settled")
    _complete(repo, settled)

    holed = _start(repo, owner, session_id="sess-holed")
    repo.checkpoint(
        holed.lease, kind="process_interrupted", state="interrupted",
        payload={"reason": "owner_process_dead"},
    )
    lease = repo.acquire_lease(holed.run["run_id"], owner, 120)
    commit = repo.checkpoint(lease, kind="model_pending", state="running")
    repo.checkpoint(commit.lease, kind="terminal", state="complete")

    open_run = _start(repo, owner, session_id="sess-open")

    assert ledger.receipt(settled.run["run_id"])["approvals"]["evidence"] == Evidence.ABSENT
    assert ledger.receipt(holed.run["run_id"])["approvals"]["evidence"] == Evidence.MISSING
    assert ledger.receipt(open_run.run["run_id"])["approvals"]["evidence"] == Evidence.MISSING
    # The same rule must hold for tools, or absence would be inferred there.
    assert ledger.receipt(settled.run["run_id"])["tools"]["evidence"] == Evidence.ABSENT
    assert ledger.receipt(holed.run["run_id"])["tools"]["evidence"] == Evidence.MISSING


def test_partial_evidence_is_partial_not_absent(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    started = _start(repo, owner)
    first = repo.checkpoint(
        started.lease,
        kind="model_pending",
        payload={"attempt_id": "att-1", "model_id": "fake-model"},
    )
    second = repo.checkpoint(
        first.lease,
        kind="tool_pending",
        payload={"tool_call_id": "call-1", "tool_name": "web_search"},
    )
    repo.checkpoint(second.lease, kind="terminal", state="partial")

    receipt = ledger.receipt(started.run["run_id"])
    assert receipt["model_attempts"]["evidence"] == Evidence.PARTIAL
    assert receipt["model_attempts"]["attempts"][0]["outcome"] == "pending"
    assert receipt["tools"]["evidence"] == Evidence.PARTIAL
    assert receipt["tools"]["calls"][0]["lifecycle"] == "pending"
    assert receipt["tools"]["pending_count"] == 1


def test_an_ambiguous_external_outcome_is_its_own_value(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    started = _start(repo, owner)
    first = repo.checkpoint(
        started.lease,
        kind="tool_pending",
        payload={"tool_call_id": "call-send", "tool_name": "gmail_send"},
    )
    repo.checkpoint(
        first.lease,
        kind="tool_outcome_unknown",
        state="partial",
        payload={"tool_call_id": "call-send", "tool_name": "gmail_send",
                 "reason": "no result reported"},
    )

    receipt = ledger.receipt(started.run["run_id"])
    assert receipt["tools"]["evidence"] == Evidence.AMBIGUOUS
    assert receipt["tools"]["calls"][0]["lifecycle"] == "unknown"
    assert receipt["tools"]["unknown_count"] == 1
    # An unknown external outcome is a hole, so silence elsewhere stays unknown.
    assert receipt["approvals"]["evidence"] == Evidence.MISSING
    assert receipt["recovery"]["evidence"] == Evidence.PRESENT


def test_a_kind_this_build_cannot_read_is_unsupported_not_ignored(tmp_path):
    ledger, repo, owner = _ledger(tmp_path, approvals=_FakeApprovals({}))
    started = _start(repo, owner)
    first = repo.checkpoint(
        started.lease,
        kind="tool_completed",
        payload={"tool_call_id": "call-1", "tool_name": "web_search", "status": "ok",
                 "reason": "one shortlist page"},
    )
    commit = repo.checkpoint(first.lease, kind="terminal", state="complete")
    run_id = started.run["run_id"]
    # A newer build wrote a checkpoint kind this build has never heard of.
    with sqlite3.connect(repo.path) as db:
        db.execute(
            "UPDATE agent_runs SET checkpoint_sequence = 4 WHERE run_id = ?", (run_id,)
        )
        db.execute(
            "INSERT INTO agent_run_checkpoints "
            "(run_id, sequence, kind, state, payload, created_at) "
            "VALUES (?, 4, 'quantum_leap', 'complete', '{}', ?)",
            (run_id, commit.run["updated_at"]),
        )

    receipt = ledger.receipt(run_id)
    assert receipt["record"]["unsupported"] == ["quantum_leap"]
    assert receipt["record"]["complete"] is False
    # A facet with no entries cannot claim absence...
    assert receipt["approvals"]["evidence"] == Evidence.UNSUPPORTED
    assert receipt["model_attempts"]["evidence"] == Evidence.UNSUPPORTED
    # ...and a facet that does have entries cannot claim they are the whole
    # story either. The entries stay visible; only the conclusion is withheld.
    assert receipt["tools"]["calls"][0]["tool_name"] == "web_search"
    assert receipt["tools"]["evidence"] == Evidence.UNSUPPORTED
    assert receipt["rationale"]["notes"][0]["reason"] == "one shortlist page"
    assert receipt["rationale"]["evidence"] == Evidence.UNSUPPORTED


def test_an_expired_approval_is_neither_a_denial_nor_a_silence(tmp_path):
    store = ConversationStore(tmp_path / "club", approval_ttl_seconds=0.01)
    store.park_inbox("ap-expired", "gmail_send", {"draft_id": "d1"}, session_id="sess-1")
    ledger, repo, owner = _ledger(tmp_path, approvals=store)
    started = _start(repo, owner)
    repo.checkpoint(
        started.lease,
        kind="waiting_approval",
        state="waiting_approval",
        payload={"approval_id": "ap-expired", "tool_name": "gmail_send"},
        approval_ids=["ap-expired"],
    )
    import time

    time.sleep(0.05)

    decision = ledger.receipt(started.run["run_id"])["approvals"]["decisions"][0]
    assert decision["approval_id"] == "ap-expired"
    assert decision["evidence"] == Evidence.EXPIRED
    assert decision["decision"] is None
    assert decision["requested"] is True


def test_pruned_detail_reads_as_expired_evidence_not_as_absence(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    old = EPOCH - timedelta(days=CHECKPOINT_RETENTION_DAYS + 5)
    started = _start(repo, owner, person_id="person-9", now=old)
    first = repo.checkpoint(
        started.lease,
        kind="tool_completed",
        payload={"tool_call_id": "call-1", "tool_name": "web_search", "status": "ok"},
        source_refs=[{"id": "src-1", "title": "Ramp", "url": "https://ramp.test/a"}],
        artifact_refs=[{"id": "art-1", "artifact_type": "brief", "title": "Brief"}],
        now=old + timedelta(seconds=1),
    )
    repo.checkpoint(
        first.lease, kind="terminal", state="complete",
        terminal_result={"status": "complete", "text_length": 12},
        now=old + timedelta(seconds=2),
    )
    before = ledger.receipt(started.run["run_id"])
    assert before["tools"]["evidence"] == Evidence.PRESENT

    swept = ledger.enforce_retention(now=EPOCH)
    assert swept["pruned_runs"] == 1

    after = ledger.receipt(started.run["run_id"])
    assert after["record"]["checkpoints_stored"] == 0
    assert after["record"]["pruned_through_sequence"] == 3
    # Detail aged out on purpose; identity and outcome are still evidence.
    assert after["tools"]["evidence"] == Evidence.EXPIRED
    assert after["model_attempts"]["evidence"] == Evidence.EXPIRED
    assert after["rationale"]["evidence"] == Evidence.EXPIRED
    assert after["run"]["person_id"] == "person-9"
    assert after["sources"] == before["sources"]
    assert after["artifacts"] == before["artifacts"]
    assert after["outcome"]["result"] == {"status": "complete", "text_length": 12}
    assert after["approvals"]["evidence"] == Evidence.ABSENT

    # Retention is idempotent and never touches a run that is still open.
    live = _start(repo, owner, session_id="sess-live", now=old)
    assert ledger.enforce_retention(now=EPOCH)["pruned_runs"] == 0
    assert ledger.receipt(live.run["run_id"])["record"]["checkpoints_stored"] == 1


def test_retention_never_deletes_the_marker_that_evidence_is_missing(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    stale = EPOCH - timedelta(days=CHECKPOINT_RETENTION_DAYS + 5)
    holed = _start(repo, owner, session_id="sess-holed", now=stale)
    repo.checkpoint(
        holed.lease,
        kind="tool_outcome_unknown",
        state="partial",
        payload={"tool_call_id": "call-send", "tool_name": "gmail_send"},
        now=stale + timedelta(seconds=1),
    )
    foreign = _start(repo, owner, session_id="sess-foreign", now=stale)
    repo.checkpoint(
        foreign.lease, kind="terminal", state="complete", now=stale + timedelta(seconds=1)
    )
    with sqlite3.connect(repo.path) as db:
        db.execute(
            "UPDATE agent_run_checkpoints SET kind = 'quantum_leap' "
            "WHERE run_id = ? AND sequence = 2",
            (foreign.run["run_id"],),
        )

    assert ledger.enforce_retention(now=EPOCH)["pruned_runs"] == 0
    still_ambiguous = ledger.receipt(holed.run["run_id"])
    assert still_ambiguous["record"]["checkpoints_stored"] == 2
    assert still_ambiguous["tools"]["evidence"] == Evidence.AMBIGUOUS
    assert ledger.receipt(foreign.run["run_id"])["record"]["unsupported"] == [
        "quantum_leap"
    ]


# --- criterion 7: a ledger row is not authority ---------------------------


class _FakeApprovals:
    def __init__(self, items):
        self.items = items
        self.asked = []

    def get_inbox(self, item_id):
        self.asked.append(item_id)
        return self.items.get(item_id)


def test_a_run_row_cannot_assert_an_approval_the_owner_never_gave(tmp_path):
    approvals = _FakeApprovals({})
    ledger, repo, owner = _ledger(tmp_path, approvals=approvals)
    started = _start(repo, owner)
    repo.checkpoint(
        started.lease,
        kind="waiting_approval",
        state="waiting_approval",
        payload={"approval_id": "ap-forged", "tool_name": "gmail_send", "status": "allow"},
        approval_ids=["ap-forged"],
    )

    decisions = ledger.receipt(started.run["run_id"])["approvals"]["decisions"]
    assert [item["approval_id"] for item in decisions] == ["ap-forged"]
    assert decisions[0]["evidence"] == Evidence.MISSING
    assert decisions[0]["decision"] is None
    assert approvals.asked == ["ap-forged"]
    # The run store's own "status" field must never become the decision.
    assert "allow" not in str(decisions[0].values())


def test_a_pending_owner_decision_never_reads_as_allowed(tmp_path):
    store = ConversationStore(tmp_path / "club")
    store.park_inbox("ap-pending", "gmail_send", {"draft_id": "d1"}, session_id="sess-1")
    ledger, repo, owner = _ledger(tmp_path, approvals=store)
    started = _start(repo, owner)
    repo.checkpoint(
        started.lease,
        kind="waiting_approval",
        state="waiting_approval",
        payload={"approval_id": "ap-pending"},
        approval_ids=["ap-pending"],
    )

    decision = ledger.receipt(started.run["run_id"])["approvals"]["decisions"][0]
    assert decision["evidence"] == Evidence.PARTIAL
    assert decision["decision"] is None
    assert decision["state"] == "pending"


def test_a_denied_approval_is_reported_as_denied(tmp_path):
    store = ConversationStore(tmp_path / "club")
    store.park_inbox("ap-deny", "gmail_send", {"draft_id": "d1"}, session_id="sess-1")
    store.resolve_inbox("ap-deny", "deny", actor="director")
    ledger, repo, owner = _ledger(tmp_path, approvals=store)
    started = _start(repo, owner)
    repo.checkpoint(
        started.lease,
        kind="waiting_approval",
        state="waiting_approval",
        payload={"approval_id": "ap-deny", "tool_name": "gmail_send"},
        approval_ids=["ap-deny"],
    )

    receipt = ledger.receipt(started.run["run_id"])
    decision = receipt["approvals"]["decisions"][0]
    assert decision["decision"] == "deny"
    assert decision["evidence"] == Evidence.PRESENT
    assert decision["tool_name"] == "gmail_send"
    assert receipt["run"]["state"] == "waiting_approval"


def test_an_authorized_send_with_no_reported_outcome_is_ambiguous(tmp_path):
    store = ConversationStore(tmp_path / "club", approval_ttl_seconds=0.01)
    store.park_inbox("ap-send", "gmail_send", {"draft_id": "d1"}, session_id="sess-1")
    store.decide_and_claim_inbox_execution(
        "ap-send", "allow", actor="director", scope="once", claimant="turn-1"
    )
    import time

    time.sleep(0.05)
    store.reap_overdue_inbox()
    ledger, repo, owner = _ledger(tmp_path, approvals=store)
    started = _start(repo, owner)
    repo.checkpoint(
        started.lease,
        kind="waiting_external",
        state="waiting_external",
        payload={"approval_id": "ap-send", "tool_name": "gmail_send"},
        approval_ids=["ap-send"],
    )

    decision = ledger.receipt(started.run["run_id"])["approvals"]["decisions"][0]
    assert decision["decision"] == "allow"
    assert decision["execution_status"] == "interrupted"
    assert decision["evidence"] == Evidence.AMBIGUOUS


def test_without_an_owner_native_source_approvals_are_unsupported(tmp_path):
    ledger, repo, owner = _ledger(tmp_path, approvals=None)
    started = _start(repo, owner)
    repo.checkpoint(
        started.lease,
        kind="waiting_approval",
        state="waiting_approval",
        payload={"approval_id": "ap-1"},
        approval_ids=["ap-1"],
    )
    receipt = ledger.receipt(started.run["run_id"])
    assert receipt["approvals"]["evidence"] == Evidence.UNSUPPORTED
    assert receipt["approvals"]["decisions"][0]["decision"] is None


def test_the_ledger_never_writes_to_the_run_itself(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    started = _start(repo, owner)
    before = repo.get_run(started.run["run_id"])
    ledger.receipt(started.run["run_id"])
    ledger.query(session_id="sess-1")
    ledger.enforce_retention(now=EPOCH)
    assert repo.get_run(started.run["run_id"]) == before


# --- criteria 4 and 5: opening a run from anywhere, bounded ---------------


def test_query_supports_exact_id_and_bounded_filters(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    chat = _start(repo, owner, session_id="sess-chat", person_id="person-a", now=EPOCH)
    _complete(repo, chat, at=EPOCH + timedelta(seconds=1))
    sched = _start(
        repo, owner, session_id="sched-3", trigger="scheduled",
        person_id="person-b", now=EPOCH + timedelta(hours=1),
    )
    older = _start(repo, owner, session_id="sess-chat", now=EPOCH - timedelta(days=2))

    # exact id — the diagnostic and scheduled-receipt entry points
    assert ledger.query(run_id=sched.run["run_id"])["runs"][0]["run_id"] == sched.run["run_id"]
    assert ledger.query(run_id="nope")["runs"] == []
    # chat activity
    assert {row["run_id"] for row in ledger.query(session_id="sess-chat")["runs"]} == {
        chat.run["run_id"], older.run["run_id"],
    }
    # a person timeline
    assert [row["run_id"] for row in ledger.query(person_id="person-b")["runs"]] == [
        sched.run["run_id"]
    ]
    # status
    assert [row["run_id"] for row in ledger.query(statuses=["complete"])["runs"]] == [
        chat.run["run_id"]
    ]
    # time
    windowed = ledger.query(since=EPOCH - timedelta(hours=1), until=EPOCH + timedelta(minutes=5))
    assert [row["run_id"] for row in windowed["runs"]] == [chat.run["run_id"]]
    # trigger
    assert [row["run_id"] for row in ledger.query(trigger="scheduled")["runs"]] == [
        sched.run["run_id"]
    ]
    # bounded
    page = ledger.query(limit=2)
    assert len(page["runs"]) == 2 and page["truncated"] is True
    assert ledger.query(limit=10_000)["limit"] == MAX_QUERY_LIMIT
    with pytest.raises(ValueError):
        ledger.query(statuses=["vibing"])
    with pytest.raises(ValueError):
        ledger.query(trigger="telepathy")


def test_one_person_never_sees_another_persons_runs(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    mine = _start(repo, owner, session_id="sess-a", person_id="person-a")
    theirs = _start(repo, owner, session_id="sess-b", person_id="person-b")
    unbound = _start(repo, owner, session_id="sess-c")

    rows = ledger.query(person_id="person-a")["runs"]
    assert [row["run_id"] for row in rows] == [mine.run["run_id"]]
    assert theirs.run["run_id"] not in str(rows)
    assert unbound.run["run_id"] not in str(rows)
    assert ledger.query(person_id="person-a", session_id="sess-b")["runs"] == []
    assert ledger.receipt(theirs.run["run_id"])["run"]["person_id"] == "person-b"


def test_a_cancelled_run_is_not_a_failed_one(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    stopped = _start(repo, owner)
    repo.checkpoint(
        stopped.lease, kind="terminal", state="stopped",
        terminal_result={"status": "stopped", "error_class": "OperatorCancelled"},
    )
    failed = _start(repo, owner)
    repo.checkpoint(
        failed.lease, kind="terminal", state="failed",
        terminal_result={"status": "failed", "error_class": "ProviderError"},
    )

    assert ledger.receipt(stopped.run["run_id"])["outcome"]["state"] == "stopped"
    assert ledger.receipt(failed.run["run_id"])["outcome"]["state"] == "failed"
    assert ledger.receipt(stopped.run["run_id"])["outcome"]["result"]["error_class"] == (
        "OperatorCancelled"
    )


def test_a_failed_over_run_lists_every_model_attempt(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    started = _start(repo, owner)
    first = repo.checkpoint(
        started.lease,
        kind="model_pending",
        payload={"attempt_id": "att-1", "provider": "anthropic", "model_id": "model-a"},
    )
    second = repo.checkpoint(
        first.lease,
        kind="model_completed",
        payload={"attempt_id": "att-1", "status": "error", "error_class": "BillingError"},
    )
    third = repo.checkpoint(
        second.lease,
        kind="model_pending",
        payload={"attempt_id": "att-2", "provider": "openai", "model_id": "model-b"},
    )
    fourth = repo.checkpoint(
        third.lease,
        kind="model_completed",
        payload={"attempt_id": "att-2", "status": "ok"},
    )
    repo.checkpoint(fourth.lease, kind="terminal", state="complete")

    attempts = ledger.receipt(started.run["run_id"])["model_attempts"]
    assert [item["attempt_id"] for item in attempts["attempts"]] == ["att-1", "att-2"]
    assert [item["model_id"] for item in attempts["attempts"]] == ["model-a", "model-b"]
    assert attempts["attempts"][0]["error_class"] == "BillingError"
    assert attempts["distinct_models"] == 2
    assert attempts["evidence"] == Evidence.PRESENT


def test_an_interrupted_run_shows_its_recovery_decision(tmp_path):
    ledger, repo, owner = _ledger(tmp_path)
    started = _start(repo, owner)
    repo.checkpoint(
        started.lease, kind="process_interrupted", state="interrupted",
        payload={"reason": "owner_process_dead"},
    )

    receipt = ledger.receipt(started.run["run_id"])
    assert receipt["run"]["state"] == "interrupted"
    assert receipt["recovery"]["evidence"] == Evidence.PRESENT
    assert receipt["recovery"]["events"][0]["reason"] == "owner_process_dead"
    assert receipt["outcome"]["open"] is True
    assert receipt["outcome"]["evidence"] == Evidence.MISSING


def test_a_missing_run_has_no_receipt(tmp_path):
    ledger, _repo, _owner = _ledger(tmp_path)
    assert ledger.receipt("run-that-never-was") is None
