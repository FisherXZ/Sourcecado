from coworker.workspace_audit import WorkspaceAuditStore


def test_workspace_audit_persists_bounded_sanitized_receipts_without_bodies_or_environment(
    tmp_path,
):
    store = WorkspaceAuditStore(tmp_path / "state")

    receipt = store.record(
        receipt_type="updated",
        tool="fs_write",
        risk_class="reversible_write",
        decision="auto",
        execution_target="typed_filesystem",
        grant_id="grant-1",
        path="notes/candidate.txt",
        before_hash="before",
        after_hash="after",
        actor="assistant",
        session_id="session-1",
        run_id="run-1",
        status="succeeded",
        duration_ms=4,
        summary="Updated candidate note TOKEN=never-persist-this",
    )

    assert receipt["receipt_type"] == "updated"
    assert receipt["summary"] == "Updated candidate note TOKEN=[REDACTED]"
    assert WorkspaceAuditStore(tmp_path / "state").list(limit=10) == [receipt]
    serialized = (tmp_path / "state" / "workspace_receipts.jsonl").read_text()
    assert "never-persist-this" not in serialized
    assert "content" not in receipt
    assert "environment" not in receipt
    assert "output" not in receipt


def test_workspace_audit_bounds_summaries_and_lists_newest_first(tmp_path):
    store = WorkspaceAuditStore(tmp_path / "state")
    first = store.record(
        receipt_type="read",
        tool="fs_read",
        risk_class="read",
        decision="auto",
        execution_target="typed_filesystem",
        status="succeeded",
        summary="a" * 1000,
    )
    second = store.record(
        receipt_type="denied",
        tool="fs_trash",
        risk_class="destructive_write",
        decision="deny",
        execution_target="typed_filesystem",
        status="denied",
        summary="Trash denied",
    )

    assert len(first["summary"]) <= 300
    assert store.list(limit=1) == [second]
