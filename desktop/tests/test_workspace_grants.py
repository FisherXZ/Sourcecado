import json

import pytest

from coworker.workspace import (
    GrantAccess,
    GrantUnavailable,
    WorkspaceGrantStore,
)


def test_workspace_grant_persists_canonical_identity_and_public_metadata(tmp_path):
    state = tmp_path / "state"
    selected = tmp_path / "selected"
    selected.mkdir()
    store = WorkspaceGrantStore(state)

    created = store.add(
        selected / ".",
        label="Candidate packets",
        access=GrantAccess.READ_WRITE,
        allow_shell=True,
    )

    assert created["path"] == str(selected.resolve())
    assert created["access"] == "read_write"
    assert created["allow_shell"] is True
    assert created["revoked_at"] is None
    assert created["filesystem_identity"] == {
        "device": selected.stat().st_dev,
        "inode": selected.stat().st_ino,
    }
    assert "created_at" in created
    assert "updated_at" in created

    reloaded = WorkspaceGrantStore(state).get(created["id"])
    assert reloaded == created
    on_disk = json.loads((state / "workspace_grants.json").read_text())
    assert on_disk["version"] == 1
    assert on_disk["grants"][0]["id"] == created["id"]


def test_workspace_grant_changes_access_and_revocation_invalidates_authority(tmp_path):
    selected = tmp_path / "selected"
    selected.mkdir()
    store = WorkspaceGrantStore(tmp_path / "state")
    grant = store.add(selected, label="Research", access="read_only")

    changed = store.update(
        grant["id"],
        label="Research archive",
        access="read_write",
        allow_shell=True,
    )
    assert changed["label"] == "Research archive"
    assert changed["access"] == "read_write"
    assert changed["allow_shell"] is True

    revoked = store.revoke(grant["id"])
    assert revoked["revoked_at"] is not None
    assert store.list_active() == []
    with pytest.raises(GrantUnavailable, match="revoked"):
        store.require(grant["id"])


def test_workspace_grant_rejects_missing_or_non_directory_roots(tmp_path):
    store = WorkspaceGrantStore(tmp_path / "state")
    file_path = tmp_path / "file.txt"
    file_path.write_text("not a folder")

    with pytest.raises(GrantUnavailable, match="does not exist"):
        store.add(tmp_path / "missing", label="Missing", access="read_only")
    with pytest.raises(GrantUnavailable, match="directory"):
        store.add(file_path, label="File", access="read_only")
    with pytest.raises(GrantUnavailable, match="required"):
        store.add("", label="Empty", access="read_only")


def test_shell_grant_cannot_mount_a_root_that_contains_sourcecado_state(tmp_path):
    state = tmp_path / "state"
    store = WorkspaceGrantStore(state)

    with pytest.raises(GrantUnavailable, match="Sourcecado state"):
        store.add(
            tmp_path,
            label="Unsafe broad shell root",
            access="read_write",
            allow_shell=True,
        )
