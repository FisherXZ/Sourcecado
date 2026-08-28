"""Slice 2: leases, compare-and-swap checkpoints, and startup reclaim.

The property under test is that exactly one process owns a run at a time, and
that a process which is still alive is never stolen from.
"""

import json
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from coworker.agent_run_owner import Liveness, OwnerRegistry
from coworker.agent_run_repository import (
    DB_NAME,
    MAX_LEASE_SECONDS,
    AgentRunLeaseLost,
    AgentRunRepository,
    AgentRunVersionConflict,
)

DESKTOP = str(Path(__file__).resolve().parents[1])
NOW = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)

CHILD = """
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from coworker.agent_run_repository import AgentRunRepository

repo = AgentRunRepository(sys.argv[2])
owner = repo.registry.register()
started = repo.create_run(
    session_id="sess-child", trigger="chat", goal="child goal",
    owner=owner, lease_seconds=3600,
)
print(json.dumps({"run_id": started.run["run_id"], "owner_id": owner.owner_id}))
sys.stdout.flush()
if sys.argv[3] == "hold":
    time.sleep(300)
os._exit(0)
"""


def _repo(tmp_path):
    repo = AgentRunRepository(tmp_path)
    return repo, repo.registry.register()


def _start(repo, owner, *, lease_seconds=600, now=NOW, session_id="sess-1"):
    return repo.create_run(
        session_id=session_id,
        trigger="chat",
        goal="find leads",
        owner=owner,
        lease_seconds=lease_seconds,
        now=now,
    )


def _spawn_child(tmp_path, mode):
    """Run a real second process that takes a lease, then dies or holds it."""
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, DESKTOP, str(tmp_path), mode],
        stdout=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline()
    assert line, "child never reported a lease"
    return proc, json.loads(line)


# --- one owner at a time -------------------------------------------------


def test_a_live_lease_blocks_every_other_owner(tmp_path):
    repo, owner = _repo(tmp_path)
    other = OwnerRegistry(tmp_path).register()
    started = _start(repo, owner)
    run_id = started.run["run_id"]

    assert started.lease.owner_id == owner.owner_id
    assert repo.acquire_lease(run_id, other, 600, now=NOW) is None
    # The holder may re-take its own lease; the fencing version still moves.
    again = repo.acquire_lease(run_id, owner, 600, now=NOW)
    assert again is not None and again.version == started.lease.version + 1
    with pytest.raises(ValueError):
        repo.acquire_lease(run_id, owner, MAX_LEASE_SECONDS + 1, now=NOW)
    with pytest.raises(ValueError):
        repo.acquire_lease(run_id, owner, 0, now=NOW)
    with pytest.raises(KeyError):
        repo.acquire_lease("missing", owner, 600, now=NOW)


def test_eight_racing_owners_produce_exactly_one_lease(tmp_path):
    seed_repo, seed_owner = _repo(tmp_path)
    started = _start(seed_repo, seed_owner, now=None)
    run_id = started.run["run_id"]
    seed_repo.release_lease(started.lease)

    barrier = threading.Barrier(8)
    results: list[object] = []
    guard = threading.Lock()

    def contend():
        repo = AgentRunRepository(tmp_path)
        owner = repo.registry.register()
        barrier.wait()
        try:
            outcome = repo.acquire_lease(run_id, owner, 600)
        except Exception as exc:  # recorded, then asserted away below
            outcome = exc
        with guard:
            results.append(outcome)

    threads = [threading.Thread(target=contend) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    leases = [item for item in results if item is not None and not isinstance(item, Exception)]
    assert [item for item in results if isinstance(item, Exception)] == []
    assert len(leases) == 1
    run = seed_repo.get_run(run_id)
    assert run["lease_owner"] == leases[0].owner_id
    assert run["version"] == leases[0].version


def test_releasing_a_lease_lets_the_next_owner_in(tmp_path):
    repo, owner = _repo(tmp_path)
    other = OwnerRegistry(tmp_path).register()
    started = _start(repo, owner)
    repo.release_lease(started.lease)

    assert repo.get_run(started.run["run_id"])["lease_owner"] is None
    taken = repo.acquire_lease(started.run["run_id"], other, 600, now=NOW)
    assert taken is not None and taken.owner_id == other.owner_id
    # A released lease is spent: its holder cannot release or commit again.
    with pytest.raises(AgentRunLeaseLost):
        repo.release_lease(started.lease)


def test_renewal_extends_a_live_lease_and_dies_with_it(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner, lease_seconds=60)

    renewed = repo.renew_lease(started.lease, 60, now=NOW + timedelta(seconds=30))
    assert renewed.version == started.lease.version + 1
    assert renewed.expires_at > started.lease.expires_at
    with pytest.raises(AgentRunVersionConflict):
        repo.renew_lease(started.lease, 60, now=NOW + timedelta(seconds=31))
    with pytest.raises(AgentRunLeaseLost):
        repo.renew_lease(renewed, 60, now=NOW + timedelta(seconds=600))


# --- compare-and-swap checkpoints ----------------------------------------


def test_a_superseded_owner_cannot_commit_a_checkpoint(tmp_path):
    repo, owner = _repo(tmp_path)
    successor = OwnerRegistry(tmp_path).register()
    started = _start(repo, owner, lease_seconds=60)
    run_id = started.run["run_id"]

    later = NOW + timedelta(seconds=120)
    repo.reconcile_expired_leases(now=later)
    taken = repo.acquire_lease(run_id, successor, 60, now=later)
    assert taken is not None

    before = repo.get_run(run_id)
    with pytest.raises(AgentRunLeaseLost):
        repo.checkpoint(
            started.lease, kind="model_completed", payload={"step": 9}, now=later
        )
    assert repo.get_run(run_id) == before
    assert 9 not in [
        item["payload"].get("step") for item in repo.list_checkpoints(run_id)
    ]


def test_a_stale_lease_from_the_same_owner_is_refused(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    commit = repo.checkpoint(started.lease, kind="model_pending", now=NOW)

    with pytest.raises(AgentRunVersionConflict):
        repo.checkpoint(started.lease, kind="model_completed", now=NOW)
    assert repo.checkpoint(commit.lease, kind="model_completed", now=NOW) is not None


def test_two_duplicate_owners_committing_one_version_commit_once(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner, now=None)
    run_id = started.run["run_id"]

    barrier = threading.Barrier(2)
    outcomes: list[object] = []
    guard = threading.Lock()

    def commit(step):
        worker = AgentRunRepository(tmp_path)
        barrier.wait()
        try:
            result = worker.checkpoint(
                started.lease, kind="tool_completed", payload={"step": step}
            )
        except Exception as exc:
            result = exc
        with guard:
            outcomes.append(result)

    threads = [threading.Thread(target=commit, args=(step,)) for step in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    committed = [item for item in outcomes if not isinstance(item, Exception)]
    refused = [item for item in outcomes if isinstance(item, Exception)]
    assert len(committed) == 1
    assert len(refused) == 1
    assert isinstance(refused[0], AgentRunVersionConflict)
    checkpoints = repo.list_checkpoints(run_id)
    assert [item["sequence"] for item in checkpoints] == [1, 2]
    assert repo.get_run(run_id)["checkpoint_sequence"] == 2


# --- expiry reconciliation -----------------------------------------------


def test_expired_running_work_is_interrupted_for_review(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner, lease_seconds=60)
    run_id = started.run["run_id"]

    assert repo.reconcile_expired_leases(now=NOW + timedelta(seconds=30)) == []
    recovered = repo.reconcile_expired_leases(now=NOW + timedelta(seconds=61))

    assert [run["run_id"] for run in recovered] == [run_id]
    run = repo.get_run(run_id)
    assert run["current_state"] == "interrupted"
    assert run["lease_owner"] is None
    last = repo.list_checkpoints(run_id)[-1]
    assert last["kind"] == "process_interrupted"
    assert last["payload"]["reason"] == "lease_expired"
    # Reconciling twice does not append a second interruption.
    assert repo.reconcile_expired_leases(now=NOW + timedelta(seconds=62)) == []
    assert len(repo.list_checkpoints(run_id)) == 2


def test_a_waiting_run_keeps_its_state_when_a_stale_lease_is_cleared(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner, lease_seconds=60)
    commit = repo.checkpoint(
        started.lease, kind="waiting_approval", state="waiting_approval", now=NOW
    )
    assert commit.lease is None
    run_id = started.run["run_id"]
    with sqlite3.connect(tmp_path / DB_NAME) as db:
        db.execute(
            "UPDATE agent_runs SET lease_owner = ?, lease_owner_host = ?, "
            "lease_owner_pid = ?, lease_expires_at = ? WHERE run_id = ?",
            (owner.owner_id, owner.host, owner.pid, "2026-08-27T09:00:00.000000+00:00", run_id),
        )

    recovered = repo.reconcile_expired_leases(now=NOW + timedelta(seconds=61))

    assert [run["run_id"] for run in recovered] == [run_id]
    run = repo.get_run(run_id)
    assert run["current_state"] == "waiting_approval"
    assert run["lease_owner"] is None
    assert [item["kind"] for item in repo.list_checkpoints(run_id)] == [
        "run_started",
        "waiting_approval",
    ]


def test_lease_timestamps_are_fixed_width_so_text_order_is_time_order(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner, lease_seconds=1, now=NOW)

    assert len(started.lease.expires_at) == len(NOW.isoformat()) + 7
    assert started.lease.expires_at == "2026-08-27T10:00:01.000000+00:00"
    assert repo.reconcile_expired_leases(now=NOW + timedelta(milliseconds=999)) == []
    assert repo.reconcile_expired_leases(now=NOW + timedelta(seconds=1)) != []


# --- startup reclaim: only a proven-dead owner is taken ------------------


def test_startup_reclaims_a_lease_whose_owning_process_is_dead(tmp_path):
    proc, info = _spawn_child(tmp_path, "die")
    assert proc.wait(timeout=30) == 0

    repo = AgentRunRepository(tmp_path)
    run_id = info["run_id"]
    assert repo.get_run(run_id)["lease_owner"] == info["owner_id"]
    # The lease is still unexpired: only the owner's death makes it reclaimable.
    assert repo.reconcile_expired_leases() == []

    recovered = repo.reclaim_dead_owner_leases()

    assert [run["run_id"] for run in recovered] == [run_id]
    run = repo.get_run(run_id)
    assert run["current_state"] == "interrupted"
    assert run["lease_owner"] is None
    last = repo.list_checkpoints(run_id)[-1]
    assert last["kind"] == "process_interrupted"
    assert last["payload"]["reason"] == "owner_process_dead"


def test_startup_never_steals_a_lease_whose_owning_process_is_alive(tmp_path):
    proc, info = _spawn_child(tmp_path, "hold")
    run_id = info["run_id"]
    try:
        repo = AgentRunRepository(tmp_path)
        assert (
            repo.registry.liveness_of(info["owner_id"], repo.registry.host)
            is Liveness.ALIVE
        )

        assert repo.reclaim_dead_owner_leases() == []

        run = repo.get_run(run_id)
        assert run["current_state"] == "running"
        assert run["lease_owner"] == info["owner_id"]
        assert [item["kind"] for item in repo.list_checkpoints(run_id)] == [
            "run_started"
        ]
    finally:
        proc.kill()
        proc.wait(timeout=30)

    # The same call reclaims once the owner is genuinely gone: not vacuous.
    assert [run["run_id"] for run in repo.reclaim_dead_owner_leases()] == [run_id]


def test_startup_leaves_a_lease_owned_by_another_host_alone(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner, lease_seconds=3600)
    run_id = started.run["run_id"]
    with sqlite3.connect(tmp_path / DB_NAME) as db:
        db.execute(
            "UPDATE agent_runs SET lease_owner_host = 'someone-elses-mac' "
            "WHERE run_id = ?",
            (run_id,),
        )

    assert repo.reclaim_dead_owner_leases(now=NOW) == []
    assert repo.get_run(run_id)["current_state"] == "running"
    # An expired lease is still reclaimable: expiry, not liveness, fences it.
    assert [
        run["run_id"]
        for run in repo.reclaim_dead_owner_leases(now=NOW + timedelta(seconds=3601))
    ] == [run_id]


def test_startup_reclaims_running_work_that_never_recorded_an_owner(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    run_id = started.run["run_id"]
    with sqlite3.connect(tmp_path / DB_NAME) as db:
        db.execute(
            "UPDATE agent_runs SET lease_owner = NULL, lease_owner_host = NULL, "
            "lease_owner_pid = NULL, lease_expires_at = NULL WHERE run_id = ?",
            (run_id,),
        )

    assert [run["run_id"] for run in repo.reclaim_dead_owner_leases(now=NOW)] == [run_id]
    assert repo.get_run(run_id)["current_state"] == "interrupted"


def test_owner_liveness_is_unknown_without_a_marker_and_never_reclaims(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner, lease_seconds=3600)
    (tmp_path / "agent_run_owners" / f"{owner.owner_id}.owner").unlink()

    # A recovering process holds no record of that owner and finds no marker.
    recovering = AgentRunRepository(tmp_path)
    assert (
        recovering.registry.liveness_of(owner.owner_id, owner.host) is Liveness.UNKNOWN
    )
    assert recovering.reclaim_dead_owner_leases(now=NOW) == []
    assert recovering.get_run(started.run["run_id"])["current_state"] == "running"
    # This process still knows it owns that lease.
    assert repo.registry.liveness_of(owner.owner_id, owner.host) is Liveness.ALIVE
