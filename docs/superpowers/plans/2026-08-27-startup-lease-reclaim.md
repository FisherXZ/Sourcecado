# Startup Lease Reclaim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a sidecar crash-restart, resume safe in-flight Agent Runs even when the dead process still holds an unexpired lease.

**Architecture:** Reconstruction is the FastAPI lifespan, not `ConversationStore()` construction. At `AgentRunRecoveryService.start()`, reclaim every prior owner, classify work with the existing expired-lease rules, then resume `model_ready` and `tools_ready` runs. In-process fencing stays: opening a second store, listing resumable runs, and heartbeat reconcile still refuse to steal an unexpired live lease.

**Tech Stack:** Python, SQLite Agent Run repository, FastAPI lifespan, pytest, FastAPI TestClient.

**Spec:** `docs/superpowers/plans/2026-08-26-priority-0-durable-agent-run-implementation.md` Slice B — "On restart, resume safe incomplete work under the same `run_id`." Review evidence: PR #53 blocking finding.

## Global Constraints

- Never auto-replay a consequential tool whose outcome is unknown.
- `waiting_approval`, `waiting_question`, and `waiting_external` stay parked; they are not startup-resumed.
- `ConversationStore()` construction must still preserve an unexpired lease so WebSocket, HTTP approval, and scheduler cannot steal each other.
- Do not add a periodic reaper, a boot-id column, or a new public HTTP resume endpoint.
- Do not grow `turn.py`. Classification stays in `AgentRunRepository.reconcile_expired_leases`.

## File map

- `desktop/tests/test_agent_run_startup_recovery.py` — app reconstruction with a live unexpired lease.
- `desktop/tests/test_agent_run_leases.py` — repository reclaim vs store-open fencing.
- `desktop/coworker/agent_run_repository.py` — `reclaim_active` on the existing reconciler.
- `desktop/coworker/agent_run_recovery.py` — call reclaim once at process start.
- `docs/superpowers/plans/2026-08-26-priority-0-durable-agent-run-implementation.md` — record the reconstruction seam.

---

### Task 1: Prove crash-restart of an unexpired lease

**Files:**
- Modify: `desktop/tests/test_agent_run_startup_recovery.py`
- Modify: `desktop/tests/test_agent_run_leases.py`

**Interfaces:**
- Consumes: `AgentRunExecution.start`, `model_pending`, `create_app`, `ConversationStore.agent_runs.reconcile_expired_leases`
- Produces: failing tests that require `reclaim_unattended_leases` and recovery.start to call it

- [x] **Step 1: Write the failing app reconstruction test**

In `desktop/tests/test_agent_run_startup_recovery.py`, extract the seed helper so it can leave the lease live, then add:

```python
def _seed_leased_model(
    state_dir, run_id: str, *, session_id: str | None = None, expire: bool = True
) -> TurnIdentity:
    # current _seed_interrupted_model body, with the expiry UPDATE +
    # reconcile_expired_leases wrapped in `if expire:`
    ...


def _seed_interrupted_model(state_dir, run_id: str, *, session_id: str | None = None):
    return _seed_leased_model(state_dir, run_id, session_id=session_id, expire=True)


def test_app_startup_resumes_unexpired_leased_model_under_same_identity(tmp_path):
    import sqlite3
    from datetime import UTC, datetime

    identity = _seed_leased_model(tmp_path, "run-startup-live-lease", expire=False)
    with sqlite3.connect(tmp_path / "club.db") as db:
        owner, expires = db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone()
    assert owner == "crashed-owner"
    assert expires > datetime.now(UTC).isoformat()

    reopened = ConversationStore(tmp_path)
    with sqlite3.connect(tmp_path / "club.db") as db:
        state, owner = db.execute(
            "SELECT current_state, lease_owner FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone()
    assert state == "running"
    assert owner == "crashed-owner"

    provider = FakeProvider(deltas=("Recovered live lease",))
    app = create_app(token=TOKEN, provider=provider, state=tmp_path)
    with TestClient(app):
        run = _wait_for_terminal(app.state.store, identity.run_id)

    assert run["current_state"] == "complete"
    assert provider.i == 1
    assert json.loads(json.dumps(run["continuation"]))["identity"] == {
        "message_id": identity.message_id,
        "part_id": identity.part_id,
    }
```

Use the real SQLite path the store uses (`ConversationStore(tmp_path).db_path`) if `tmp_path / "club.db"` is wrong.

- [x] **Step 2: Write the failing repository reclaim test**

In `desktop/tests/test_agent_run_leases.py`, after `test_opening_another_store_preserves_an_unexpired_lease`:

```python
def test_reclaim_unattended_leases_classifies_unexpired_running_work(tmp_path):
    store = ConversationStore(tmp_path)
    _start(store, "run-live")
    lease = store.agent_runs.acquire_lease(
        "run-live", "crashed-owner", 0, 3600, now=NOW
    )
    assert lease is not None
    store.agent_runs.update_continuation(
        lease,
        _continuation(
            "model_in_flight",
            pending_model={
                "attempt_id": "run-live:1:model",
                "status": "in_flight",
            },
        ),
        now=NOW,
    )

    second = ConversationStore(tmp_path)
    assert second.get_agent_run("run-live")["current_state"] == "running"
    assert second.agent_runs.reconcile_expired_leases(now=NOW) == []
    assert second.get_agent_run("run-live")["current_state"] == "running"

    recovered = second.agent_runs.reclaim_unattended_leases(now=NOW)
    assert [row["run_id"] for row in recovered] == ["run-live"]
    run = second.get_agent_run("run-live")
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["cursor"]["phase"] == "model_ready"
    assert run["continuation"]["pending_model"]["status"] == "retry_ready"
    kinds = [item["kind"] for item in second.list_agent_run_checkpoints("run-live")]
    assert kinds[-1] == "process_interrupted"
    payload = second.list_agent_run_checkpoints("run-live")[-1]["payload"]
    assert payload["reason"] == "process_reconstructed"
```

- [x] **Step 3: Run tests to verify they fail**

Run:

```bash
cd desktop && ../.venv/bin/pytest -q \
  tests/test_agent_run_startup_recovery.py::test_app_startup_resumes_unexpired_leased_model_under_same_identity \
  tests/test_agent_run_leases.py::test_reclaim_unattended_leases_classifies_unexpired_running_work
```

Expected: FAIL. The app test stays `running` / provider `0`. The repository test raises `AttributeError: reclaim_unattended_leases`.

- [x] **Step 4: Implement reclaim on the existing reconciler**

In `desktop/coworker/agent_run_repository.py`, extend `reconcile_expired_leases`:

```python
def reconcile_expired_leases(
    self, now: datetime | None = None, *, reclaim_active: bool = False
) -> list[dict[str, Any]]:
```

When `reclaim_active` is true:

- Select every row with `lease_owner IS NOT NULL`, plus unleased `running` rows.
- For `running` rows, interrupt and classify even if `lease_expires_at` is still in the future.
- For `waiting_*`, `interrupted`, and terminal rows, clear leftover leases even if unexpired.
- Keep the same phase classification already in this method (`model_in_flight` → `model_ready` if budget is reserved, safe `tool_in_flight` → `tools_ready`, consequential `tool_in_flight` → `review_required`).
- Checkpoint reason is `process_reconstructed` when the lease was still valid at `now`, `lease_expired` when it had expired, `legacy_unleased_run` when there was no owner.

Add the wrapper:

```python
def reclaim_unattended_leases(
    self, now: datetime | None = None
) -> list[dict[str, Any]]:
    """App reconstruction: every prior owner is dead. Classify and release."""
    return self.reconcile_expired_leases(now=now, reclaim_active=True)
```

Do not call this from `AgentRunRepository.__init__` or `list_resumable_runs`.

- [x] **Step 5: Call reclaim at recovery start**

In `desktop/coworker/agent_run_recovery.py` `start()`:

```python
async def start(self) -> int:
    self._store.agent_runs.reclaim_unattended_leases()
    launched = 0
    for run in self._store.agent_runs.list_resumable_runs():
        ...
```

Leave the phase filter as `model_ready` / `tools_ready`.

- [x] **Step 6: Run the new tests and the fencing tests**

Run:

```bash
cd desktop && .venv/bin/pytest -q \
  tests/test_agent_run_startup_recovery.py \
  tests/test_agent_run_leases.py::test_opening_another_store_preserves_an_unexpired_lease \
  tests/test_agent_run_leases.py::test_reconcile_expired_leases_classifies_work_without_duplicate_checkpoints \
  tests/test_agent_run_leases.py::test_reclaim_unattended_leases_classifies_unexpired_running_work
```

Expected: PASS. Store reopen still leaves `run-unexpired` running. App startup completes the live-lease run.

- [x] **Step 7: Record the seam and run the Slice B Python files**

Add one bullet under Slice B verification in `docs/superpowers/plans/2026-08-26-priority-0-durable-agent-run-implementation.md`:

```markdown
- App reconstruction at FastAPI lifespan reclaims dead-process leases, then
  resumes `model_ready` / `tools_ready` work. `ConversationStore()` open still
  preserves an unexpired live lease.
```

Run:

```bash
cd desktop && .venv/bin/pytest -q \
  tests/test_agent_run_startup_recovery.py \
  tests/test_agent_run_leases.py \
  tests/test_agent_run_resume.py \
  tests/test_agent_run_execution.py \
  tests/test_agent_run_execution_context.py \
  tests/test_agent_run_continuation.py \
  tests/test_agent_run_state.py \
  tests/test_agent_runs.py \
  tests/test_schedule.py \
  tests/test_bound_turn.py
```

Expected: all pass.

- [x] **Step 8: Commit**

```bash
git add desktop/coworker/agent_run_repository.py \
  desktop/coworker/agent_run_recovery.py \
  desktop/tests/test_agent_run_startup_recovery.py \
  desktop/tests/test_agent_run_leases.py \
  docs/superpowers/plans/2026-08-27-startup-lease-reclaim.md \
  docs/superpowers/plans/2026-08-26-priority-0-durable-agent-run-implementation.md
git commit -m "$(cat <<'EOF'
fix(agent-runs): reclaim dead-process leases on startup

App reconstruction now classifies unexpired leases from a crashed sidecar
and resumes safe model/tool work under the same run id. Opening another
store still refuses to steal a live unexpired lease.
EOF
)"
```
