import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

import coworker.agent_run_repository as agent_runs
from coworker.store import ConversationStore


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _start(store, run_id="run-lease"):
    return store.start_agent_run(
        run_id=run_id,
        session_id="thread-lease",
        trigger="chat",
        original_goal=f"Work on {run_id}",
        provider_model_id="fake",
    )


def _continuation(
    phase="model_in_flight",
    *,
    step_index=1,
    transcript_count=0,
    transcript_sha=None,
    pending_tool=None,
    receipts=None,
    budgets=None,
):
    return {
        "schema_version": 1,
        "identity": {"message_id": "message-1", "part_id": "part-1"},
        "cursor": {
            "phase": phase,
            "step_index": step_index,
            "next_tool_index": 0,
            "transcript_prefix_count": transcript_count,
            "transcript_prefix_sha256": transcript_sha,
            "event_prefix_count": 0,
            "event_prefix_sha256": None,
        },
        "visible_partial": {
            "message_id": "message-1",
            "text_length": 12,
            "truncated": False,
        },
        "pending_tool": pending_tool,
        "completed_tool_receipts": receipts or [],
        "remaining_budgets": budgets
        or {"work_steps": 4, "tool_calls": 3, "delivery_passes": 2},
    }


def _prefix_digest(messages):
    canonical = json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_agent_run_resume_schema_migration_is_idempotent(tmp_path):
    ConversationStore(tmp_path)
    ConversationStore(tmp_path)

    with sqlite3.connect(tmp_path / "club.db") as db:
        columns = {
            row[1]: row for row in db.execute("PRAGMA table_info(agent_runs)")
        }
        indexes = {
            row[1]: row for row in db.execute("PRAGMA index_list(agent_runs)")
        }
        markers = {
            row[0]
            for row in db.execute("SELECT name FROM schema_migrations").fetchall()
        }

    assert columns["version"][3:5] == (1, "0")
    assert "lease_owner" in columns
    assert "lease_expires_at" in columns
    assert columns["continuation"][3:5] == (1, "'{}'")
    assert "agent_runs_active_lease_expiry" in indexes
    assert indexes["agent_runs_active_lease_expiry"][4] == 1
    assert "agent_run_resume_v1" in markers


def test_agent_run_resume_migration_rolls_back_schema_and_marker_on_failure(
    tmp_path,
):
    db_path = tmp_path / "club.db"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE agent_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_run_id TEXT,
                trigger TEXT NOT NULL,
                original_goal TEXT NOT NULL,
                original_goal_fingerprint TEXT,
                original_goal_fingerprint_source TEXT,
                current_state TEXT NOT NULL,
                provider_model_id TEXT,
                checkpoint_sequence INTEGER NOT NULL DEFAULT 0,
                skills_loaded TEXT NOT NULL DEFAULT '[]',
                source_refs TEXT NOT NULL DEFAULT '[]',
                artifact_refs TEXT NOT NULL DEFAULT '[]',
                usage TEXT NOT NULL DEFAULT '{}',
                terminal_result TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE agent_run_checkpoints (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence)
            );
            INSERT INTO schema_migrations VALUES (
                'agent_run_privacy_v3', '2026-08-26T00:00:00+00:00'
            );
            INSERT INTO agent_runs VALUES (
                'run-legacy', 'thread-lease', NULL, 'chat', 'safe goal',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'raw', 'running', 'fake', 0, '[]', '[]', '[]', '{}', NULL,
                '2026-08-26T00:00:00+00:00',
                '2026-08-26T00:00:00+00:00',
                '2026-08-26T00:00:00+00:00', NULL
            );
            CREATE TRIGGER block_resume_migration
            BEFORE UPDATE ON agent_runs
            BEGIN
                SELECT RAISE(FAIL, 'resume migration blocked');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="resume migration blocked"):
        ConversationStore(tmp_path)

    with sqlite3.connect(db_path) as db:
        columns = {
            row[1] for row in db.execute("PRAGMA table_info(agent_runs)")
        }
        marker = db.execute(
            "SELECT 1 FROM schema_migrations WHERE name = 'agent_run_resume_v1'"
        ).fetchone()

    assert "version" not in columns
    assert "continuation" not in columns
    assert marker is None


def test_continuation_projection_drops_unsafe_unknown_and_oversize_input(tmp_path):
    store = ConversationStore(tmp_path)
    _start(store)
    lease = store.agent_runs.acquire_lease(
        "run-lease", "owner-a", 0, 30, now=NOW
    )
    assert lease is not None

    updated = store.agent_runs.update_continuation(
        lease,
        {
            **_continuation(),
            "identity": {
                "message_id": "m" * 500,
                "part_id": "part-1 token=must-not-survive",
                "raw_body": "secret",
            },
            "cursor": {
                **_continuation()["cursor"],
                "transcript_prefix_sha256": "not-a-digest",
                "free_form_model_output": "private model prose",
            },
            "visible_partial": {
                "message_id": "message-1",
                "text_length": -4,
                "truncated": True,
                "text": "the partial text must not persist",
            },
            "pending_interaction": {
                "kind": "approval",
                "id": "approval-1",
                "arguments": {"authorization": "Bearer secret"},
            },
            "raw_tool_args": {"password": "secret"},
            "unknown": "drop me",
        },
        now=NOW,
    )

    persisted = store.get_agent_run("run-lease")
    assert persisted is not None
    continuation = persisted["continuation"]
    assert updated.version == 2
    assert set(continuation) == {
        "schema_version",
        "identity",
        "cursor",
        "visible_partial",
        "pending_interaction",
        "completed_tool_receipts",
        "remaining_budgets",
    }
    assert continuation["identity"]["message_id"] == "m" * 256
    assert continuation["identity"]["part_id"] == "part-1 token=[REDACTED]"
    assert continuation["cursor"]["transcript_prefix_sha256"] is None
    assert continuation["visible_partial"] == {
        "message_id": "message-1",
        "text_length": 0,
        "truncated": True,
    }
    assert continuation["pending_interaction"] == {
        "kind": "approval",
        "id": "approval-1",
    }
    rendered = json.dumps(continuation)
    assert "partial text" not in rendered
    assert "private model prose" not in rendered
    assert "secret" not in rendered
    assert "unknown" not in rendered
    assert "lease_owner" not in persisted
    assert "lease_expires_at" not in persisted


def test_two_connections_racing_acquire_same_version_have_one_owner(tmp_path):
    first = ConversationStore(tmp_path)
    second = ConversationStore(tmp_path)
    _start(first)
    barrier = Barrier(2)

    def acquire(store, owner):
        barrier.wait()
        return store.agent_runs.acquire_lease(
            "run-lease", owner, 0, 30, now=NOW
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: acquire(*pair),
                ((first, "owner-a"), (second, "owner-b")),
            )
        )

    assert sum(result is not None for result in results) == 1
    winner = next(result for result in results if result is not None)
    assert winner.version == 1


def test_active_lease_blocks_then_expiry_reclaims_and_fences_old_owner(tmp_path):
    first = ConversationStore(tmp_path)
    second = ConversationStore(tmp_path)
    _start(first)
    old = first.agent_runs.acquire_lease(
        "run-lease", "owner-a", 0, 5, now=NOW
    )
    assert old is not None
    assert (
        second.agent_runs.acquire_lease(
            "run-lease", "owner-b", old.version, 5, now=NOW
        )
        is None
    )
    reclaimed = second.agent_runs.acquire_lease(
        "run-lease",
        "owner-b",
        old.version,
        5,
        now=NOW + timedelta(seconds=6),
    )
    assert reclaimed is not None

    fenced_calls = (
        lambda: first.agent_runs.renew_lease(old, 5, now=NOW + timedelta(seconds=6)),
        lambda: first.agent_runs.update_continuation(
            old, _continuation(), now=NOW + timedelta(seconds=6)
        ),
        lambda: first.agent_runs.checkpoint_leased(
            old,
            "model_completed",
            _continuation("model_ready"),
            now=NOW + timedelta(seconds=6),
        ),
        lambda: first.agent_runs.release_lease(
            old, now=NOW + timedelta(seconds=6)
        ),
    )
    for call in fenced_calls:
        with pytest.raises(agent_runs.AgentRunLeaseLost):
            call()


def test_stale_same_owner_version_raises_version_conflict(tmp_path):
    store = ConversationStore(tmp_path)
    _start(store)
    original = store.agent_runs.acquire_lease(
        "run-lease", "owner-a", 0, 30, now=NOW
    )
    assert original is not None
    renewed = store.agent_runs.renew_lease(original, 30, now=NOW)
    assert renewed.version == original.version + 1

    with pytest.raises(agent_runs.AgentRunVersionConflict):
        store.agent_runs.update_continuation(
            original, _continuation(), now=NOW
        )


def test_two_connection_checkpoint_race_inserts_exactly_one_checkpoint(tmp_path):
    first = ConversationStore(tmp_path)
    second = ConversationStore(tmp_path)
    _start(first)
    lease = first.agent_runs.acquire_lease(
        "run-lease", "shared-owner", 0, 30, now=NOW
    )
    assert lease is not None
    barrier = Barrier(2)

    def checkpoint(store):
        barrier.wait()
        try:
            return store.agent_runs.checkpoint_leased(
                lease,
                "model_completed",
                _continuation("model_ready"),
                payload={"text_length": 12},
                now=NOW,
            )
        except agent_runs.AgentRunVersionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(checkpoint, (first, second)))

    assert sum(result is not None for result in results) == 1
    assert [
        item["kind"] for item in first.list_agent_run_checkpoints("run-lease")
    ] == ["run_started", "model_completed"]


def test_checkpoint_insert_failure_rolls_back_run_and_sequence(tmp_path):
    store = ConversationStore(tmp_path)
    _start(store)
    lease = store.agent_runs.acquire_lease(
        "run-lease", "owner-a", 0, 30, now=NOW
    )
    assert lease is not None
    before = store.get_agent_run("run-lease")
    with store._lock:
        store._conn.execute(
            """
            CREATE TRIGGER block_leased_checkpoint
            BEFORE INSERT ON agent_run_checkpoints
            WHEN NEW.sequence > 1
            BEGIN
                SELECT RAISE(FAIL, 'checkpoint insert blocked');
            END
            """
        )
        store._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="checkpoint insert blocked"):
        store.agent_runs.checkpoint_leased(
            lease,
            "model_completed",
            _continuation("model_ready"),
            payload={"text_length": 12},
            usage_delta={"model_calls": 1},
            now=NOW,
        )

    assert store.get_agent_run("run-lease") == before
    assert len(store.list_agent_run_checkpoints("run-lease")) == 1


def test_release_clears_lease_and_fences_all_later_writes(tmp_path):
    store = ConversationStore(tmp_path)
    _start(store)
    lease = store.agent_runs.acquire_lease(
        "run-lease", "owner-a", 0, 30, now=NOW
    )
    assert lease is not None
    released = store.agent_runs.release_lease(lease, now=NOW)
    assert released["version"] == lease.version + 1

    for call in (
        lambda: store.agent_runs.renew_lease(lease, 30, now=NOW),
        lambda: store.agent_runs.update_continuation(
            lease, _continuation(), now=NOW
        ),
        lambda: store.agent_runs.checkpoint_leased(
            lease,
            "model_completed",
            _continuation("model_ready"),
            now=NOW,
        ),
        lambda: store.agent_runs.release_lease(lease, now=NOW),
    ):
        with pytest.raises(agent_runs.AgentRunLeaseLost):
            call()


def test_terminal_run_cannot_be_acquired(tmp_path):
    store = ConversationStore(tmp_path)
    _start(store)
    store.checkpoint_agent_run(
        "run-lease",
        kind="terminal",
        state="complete",
        terminal_result={"status": "ok", "text_length": 4},
    )

    assert (
        store.agent_runs.acquire_lease(
            "run-lease", "owner-a", 0, 30, now=NOW
        )
        is None
    )


def test_continuation_update_adds_no_checkpoint(tmp_path):
    store = ConversationStore(tmp_path)
    _start(store)
    lease = store.agent_runs.acquire_lease(
        "run-lease", "owner-a", 0, 30, now=NOW
    )
    assert lease is not None
    updated = store.agent_runs.update_continuation(
        lease, _continuation(), now=NOW
    )

    run = store.get_agent_run("run-lease")
    assert run is not None
    assert updated.version == lease.version + 1
    assert run["checkpoint_sequence"] == 1
    assert [
        item["kind"] for item in store.list_agent_run_checkpoints("run-lease")
    ] == ["run_started"]


def test_continuation_receipts_are_append_only_and_cursors_budgets_monotonic(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    _start(store)
    lease = store.agent_runs.acquire_lease(
        "run-lease", "owner-a", 0, 30, now=NOW
    )
    assert lease is not None
    receipt = {
        "attempt_id": "attempt-1",
        "call_id": "call-1",
        "name": "apollo_search",
        "ok": True,
        "transcript_index": 2,
        "result_sha256": "a" * 64,
    }
    lease = store.agent_runs.update_continuation(
        lease,
        _continuation(
            "tools_ready",
            step_index=2,
            receipts=[receipt, receipt],
            budgets={"work_steps": 4, "tool_calls": 2, "delivery_passes": 1},
        ),
        now=NOW,
    )
    persisted = store.get_agent_run("run-lease")["continuation"]
    assert persisted["completed_tool_receipts"] == [receipt]

    second = {
        **receipt,
        "attempt_id": "attempt-2",
        "call_id": "call-2",
        "transcript_index": 3,
    }
    lease = store.agent_runs.update_continuation(
        lease,
        _continuation(
            "tools_ready",
            step_index=3,
            receipts=[second],
            budgets={"work_steps": 3, "tool_calls": 1, "delivery_passes": 1},
        ),
        now=NOW,
    )
    persisted = store.get_agent_run("run-lease")["continuation"]
    assert persisted["completed_tool_receipts"] == [receipt, second]

    invalid_updates = (
        _continuation(
            "tools_ready",
            step_index=1,
            budgets={"work_steps": 3, "tool_calls": 1, "delivery_passes": 1},
        ),
        _continuation(
            "tools_ready",
            step_index=3,
            budgets={"work_steps": 4, "tool_calls": 1, "delivery_passes": 1},
        ),
        _continuation(
            "tools_ready",
            step_index=3,
            receipts=[{**receipt, "ok": False}],
            budgets={"work_steps": 3, "tool_calls": 1, "delivery_passes": 1},
        ),
    )
    for continuation in invalid_updates:
        with pytest.raises(ValueError):
            store.agent_runs.update_continuation(
                lease, continuation, now=NOW
            )


def test_reconcile_expired_leases_classifies_work_without_duplicate_checkpoints(
    tmp_path,
):
    first = ConversationStore(tmp_path)
    second = ConversationStore(tmp_path)
    run_ids = (
        "run-model",
        "run-safe-tool",
        "run-unsafe-tool",
        "run-waiting",
        "run-terminal",
        "run-unexpired",
    )
    leases = {}
    for run_id in run_ids:
        _start(first, run_id)
        leases[run_id] = first.agent_runs.acquire_lease(
            run_id, f"owner-{run_id}", 0, 5, now=NOW
        )
        assert leases[run_id] is not None

    leases["run-model"] = first.agent_runs.update_continuation(
        leases["run-model"], _continuation("model_in_flight"), now=NOW
    )
    leases["run-safe-tool"] = first.agent_runs.update_continuation(
        leases["run-safe-tool"],
        _continuation(
            "tool_in_flight",
            pending_tool={
                "attempt_id": "attempt-safe",
                "call_id": "call-safe",
                "name": "apollo_search",
                "retry_class": "safe",
                "status": "in_flight",
            },
        ),
        now=NOW,
    )
    leases["run-unsafe-tool"] = first.agent_runs.update_continuation(
        leases["run-unsafe-tool"],
        _continuation(
            "tool_in_flight",
            pending_tool={
                "attempt_id": "attempt-send",
                "call_id": "call-send",
                "name": "gmail_send",
                "retry_class": "consequential",
                "status": "in_flight",
            },
        ),
        now=NOW,
    )
    leases["run-waiting"], _ = first.agent_runs.checkpoint_leased(
        leases["run-waiting"],
        "waiting_approval",
        {
            **_continuation("waiting_approval"),
            "pending_interaction": {"kind": "approval", "id": "approval-1"},
        },
        state="waiting_approval",
        now=NOW,
    )
    leases["run-terminal"], _ = first.agent_runs.checkpoint_leased(
        leases["run-terminal"],
        "terminal",
        _continuation("complete"),
        state="complete",
        terminal_result={"status": "ok", "text_length": 4},
        now=NOW,
    )
    leases["run-unexpired"] = first.agent_runs.renew_lease(
        leases["run-unexpired"], 30, now=NOW
    )

    recovered = second.agent_runs.reconcile_expired_leases(
        now=NOW + timedelta(seconds=6)
    )
    recovered_ids = {row["run_id"] for row in recovered}
    assert recovered_ids == {
        "run-model",
        "run-safe-tool",
        "run-unsafe-tool",
        "run-waiting",
        "run-terminal",
    }

    model = first.get_agent_run("run-model")
    assert model["current_state"] == "interrupted"
    assert model["continuation"]["cursor"]["phase"] == "model_ready"
    assert model["continuation"]["remaining_budgets"] == {
        "work_steps": 4,
        "tool_calls": 3,
        "delivery_passes": 2,
    }
    safe = first.get_agent_run("run-safe-tool")
    assert safe["current_state"] == "interrupted"
    assert safe["continuation"]["cursor"]["phase"] == "tools_ready"
    assert safe["continuation"]["pending_tool"]["status"] == "not_started"
    unsafe = first.get_agent_run("run-unsafe-tool")
    assert unsafe["current_state"] == "interrupted"
    assert unsafe["continuation"]["cursor"]["phase"] == "review_required"
    assert unsafe["continuation"]["pending_tool"]["status"] == "outcome_unknown"
    assert first.get_agent_run("run-waiting")["current_state"] == "waiting_approval"
    assert first.get_agent_run("run-terminal")["current_state"] == "complete"
    assert first.get_agent_run("run-unexpired")["current_state"] == "running"

    resumable = second.agent_runs.list_resumable_runs(
        now=NOW + timedelta(seconds=6)
    )
    assert {row["run_id"] for row in resumable} == {
        "run-model",
        "run-safe-tool",
    }
    checkpoint_counts = {
        run_id: len(first.list_agent_run_checkpoints(run_id))
        for run_id in run_ids
    }
    assert second.agent_runs.reconcile_expired_leases(
        now=NOW + timedelta(seconds=6)
    ) == []
    assert {
        run_id: len(first.list_agent_run_checkpoints(run_id))
        for run_id in run_ids
    } == checkpoint_counts


def test_opening_another_store_preserves_an_unexpired_lease(tmp_path):
    first = ConversationStore(tmp_path)
    _start(first)
    lease = first.agent_runs.acquire_lease(
        "run-lease", "owner-a", 0, 3600, now=datetime.now(UTC)
    )
    assert lease is not None

    second = ConversationStore(tmp_path)

    assert second.get_agent_run("run-lease")["current_state"] == "running"
    renewed = second.agent_runs.renew_lease(
        lease, 3600, now=datetime.now(UTC)
    )
    assert renewed.version == lease.version + 1


def test_transcript_prefix_validation_distinguishes_exact_extra_and_mismatch(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    _start(store)
    messages = [
        {"role": "user", "content": "Find candidates"},
        {"role": "assistant", "content": "Working on it"},
    ]
    lease = store.agent_runs.acquire_lease(
        "run-lease", "owner-a", 0, 30, now=NOW
    )
    assert lease is not None
    store.agent_runs.update_continuation(
        lease,
        _continuation(
            "model_ready",
            transcript_count=2,
            transcript_sha=_prefix_digest(messages),
        ),
        now=NOW,
    )

    assert store.agent_runs.validate_transcript_prefix("run-lease", messages) == "exact"
    assert (
        store.agent_runs.validate_transcript_prefix(
            "run-lease", [*messages, {"role": "user", "content": "tail"}]
        )
        == "extra_tail"
    )
    assert (
        store.agent_runs.validate_transcript_prefix("run-lease", messages[:1])
        == "mismatch"
    )
    changed = [messages[0], {"role": "assistant", "content": "different"}]
    assert (
        store.agent_runs.validate_transcript_prefix("run-lease", changed)
        == "mismatch"
    )


def test_two_connection_inbox_decision_and_claim_races_are_first_wins(tmp_path):
    first = ConversationStore(tmp_path)
    second = ConversationStore(tmp_path)
    first.park_inbox("decision-race", "gmail_send", {"to": "a@example.com"})
    barrier = Barrier(2)

    def decide(store, decision, claimant):
        barrier.wait()
        return store.decide_and_claim_inbox_execution(
            "decision-race",
            decision,
            actor="Fisher",
            scope="once",
            claimant=claimant,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions = list(
            pool.map(
                lambda args: decide(*args),
                ((first, "allow", "claim-a"), (second, "deny", "claim-b")),
            )
        )
    item = first.get_inbox("decision-race")
    assert sum(outcome is not None for outcome in decisions) == 1
    assert item["decision"] in {"allow", "deny"}
    assert item["execution_status"] == (
        "executing" if item["decision"] == "allow" else "not_run"
    )

    first.park_inbox("claim-race", "gmail_send", {"to": "b@example.com"})
    barrier = Barrier(2)

    def claim(store, claimant):
        barrier.wait()
        return store.decide_and_claim_inbox_execution(
            "claim-race",
            "allow",
            actor="Fisher",
            scope="once",
            claimant=claimant,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda args: claim(*args),
                ((first, "claim-a"), (second, "claim-b")),
            )
        )

    assert sum(bool(outcome and outcome["claimed"]) for outcome in claims) == 1
    assert first.get_inbox("claim-race")["execution_claimant"] in {
        "claim-a",
        "claim-b",
    }
