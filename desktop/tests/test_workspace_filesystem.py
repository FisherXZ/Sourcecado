import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from coworker.workspace import WorkspaceGrantStore
from coworker.workspace_files import (
    StaleWorkspaceWrite,
    WorkspaceApprovalRequired,
    WorkspaceFilesystem,
    WorkspacePathError,
)


def workspace(tmp_path, *, access="read_write"):
    state = tmp_path / "state"
    root = tmp_path / "workspace"
    root.mkdir()
    store = WorkspaceGrantStore(state)
    grant = store.add(root, label="Workspace", access=access)
    return root, grant, WorkspaceFilesystem(store, state_root=state)


def test_filesystem_resolves_only_grant_relative_contained_paths(tmp_path):
    root, grant, files = workspace(tmp_path)
    (root / "inside.txt").write_text("inside")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (root / "escape").symlink_to(outside)

    assert files.stat(grant["id"], "inside.txt")["type"] == "file"
    for unsafe in ("../outside.txt", str(outside.resolve()), "escape"):
        with pytest.raises(WorkspacePathError, match="outside|relative|symlink"):
            files.read(grant["id"], unsafe)


def test_filesystem_hard_denies_sourcecado_state_even_under_a_broad_grant(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "secrets.json").write_text("secret")
    store = WorkspaceGrantStore(state)
    grant = store.add(tmp_path, label="Broad", access="read_write")
    files = WorkspaceFilesystem(store, state_root=state)

    with pytest.raises(WorkspacePathError, match="Sourcecado state"):
        files.read(grant["id"], "state/secrets.json")
    listing = files.list(grant["id"], ".")
    assert "state" not in {entry["name"] for entry in listing["entries"]}


def test_filesystem_read_paginates_text_and_returns_only_binary_metadata(tmp_path):
    root, grant, files = workspace(tmp_path)
    (root / "long.txt").write_text("abcdefghij")
    (root / "binary.bin").write_bytes(b"\x00\x01\x02secret-bytes")

    first = files.read(grant["id"], "long.txt", offset=0, max_chars=4)
    second = files.read(
        grant["id"], "long.txt", offset=first["next_offset"], max_chars=4
    )

    assert first["content"] == "abcd"
    assert first["truncated"] is True
    assert first["next_offset"] == 4
    assert second["content"] == "efgh"
    binary = files.read(grant["id"], "binary.bin")
    assert binary["kind"] == "binary"
    assert binary["size"] == len(b"\x00\x01\x02secret-bytes")
    assert "content" not in binary
    assert "secret-bytes" not in str(binary)


def test_filesystem_read_paginates_unicode_without_misclassifying_split_codepoints(
    tmp_path,
):
    root, grant, files = workspace(tmp_path)
    (root / "unicode.txt").write_text("ééé")

    first = files.read(grant["id"], "unicode.txt", max_chars=2)
    second = files.read(
        grant["id"], "unicode.txt", offset=first["next_offset"], max_chars=2
    )

    assert first["kind"] == "text"
    assert first["content"] == "éé"
    assert second["content"] == "é"


def test_filesystem_search_is_bounded_and_ignores_dependencies(tmp_path):
    root, grant, files = workspace(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "one.txt").write_text("candidate alpha\n" + "x" * 200)
    (root / "src" / "two.txt").write_text("candidate beta")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "hidden.txt").write_text("candidate hidden")
    os.symlink(tmp_path, root / "src" / "loop")

    result = files.search(grant["id"], ".", query="candidate", max_results=1)

    assert len(result["matches"]) == 1
    assert result["truncated"] is True
    assert result["matches"][0]["path"].startswith("src/")
    assert "hidden" not in str(result)


def test_filesystem_write_is_atomic_and_requires_current_before_hash(tmp_path):
    root, grant, files = workspace(tmp_path)

    created = files.write(
        grant["id"], "notes/candidate.txt", "alpha", create_parents=True
    )
    assert created["receipt_type"] == "created"
    assert created["before_hash"] is None
    assert (root / "notes" / "candidate.txt").read_text() == "alpha"

    before = created["after_hash"]
    updated = files.write(
        grant["id"],
        "notes/candidate.txt",
        "beta",
        expected_before_hash=before,
    )
    assert updated["receipt_type"] == "updated"
    assert updated["before_hash"] == before
    assert (root / "notes" / "candidate.txt").read_text() == "beta"
    assert list((root / "notes").glob(".sourcecado-write-*")) == []

    with pytest.raises(StaleWorkspaceWrite, match="changed"):
        files.write(
            grant["id"],
            "notes/candidate.txt",
            "stale",
            expected_before_hash=before,
        )
    with pytest.raises(WorkspaceApprovalRequired, match="overwrite"):
        files.write(grant["id"], "notes/candidate.txt", "unreviewed")
    assert (root / "notes" / "candidate.txt").read_text() == "beta"


def test_filesystem_serializes_concurrent_version_checked_writes(tmp_path):
    root, grant, files = workspace(tmp_path)
    initial = files.write(grant["id"], "record.txt", "v1")

    def update(value):
        return files.write(
            grant["id"],
            "record.txt",
            value,
            expected_before_hash=initial["after_hash"],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = []
        for future in [pool.submit(update, "v2"), pool.submit(update, "v3")]:
            try:
                outcomes.append(future.result())
            except StaleWorkspaceWrite:
                outcomes.append("stale")

    assert sum(outcome == "stale" for outcome in outcomes) == 1
    assert (root / "record.txt").read_text() in {"v2", "v3"}


def test_filesystem_patch_copy_move_and_approved_trash_are_receipted(tmp_path):
    root, grant, files = workspace(tmp_path)
    created = files.write(grant["id"], "source.txt", "hello candidate")

    patched = files.patch(
        grant["id"],
        "source.txt",
        replacements=[{"old": "candidate", "new": "director"}],
        expected_before_hash=created["after_hash"],
    )
    assert patched["receipt_type"] == "updated"
    assert (root / "source.txt").read_text() == "hello director"

    copied = files.copy(grant["id"], "source.txt", grant["id"], "copy.txt")
    assert copied["receipt_type"] == "created"
    moved = files.move(
        grant["id"],
        "copy.txt",
        grant["id"],
        "archive/copy.txt",
        expected_source_hash=copied["after_hash"],
        create_parents=True,
    )
    assert moved["receipt_type"] == "moved"
    assert not (root / "copy.txt").exists()
    assert (root / "archive" / "copy.txt").read_text() == "hello director"

    with pytest.raises(WorkspaceApprovalRequired, match="trash"):
        files.trash(grant["id"], "archive/copy.txt")
    trashed = files.trash(grant["id"], "archive/copy.txt", approved=True)
    assert trashed["receipt_type"] == "trashed"
    assert trashed["recoverable"] is True
    assert not (root / "archive" / "copy.txt").exists()
    assert "hello director" not in str(trashed)


def test_filesystem_enforces_read_only_and_cross_root_approval(tmp_path):
    root, grant, files = workspace(tmp_path, access="read_only")
    (root / "read.txt").write_text("read")
    with pytest.raises(WorkspacePathError, match="read-only"):
        files.write(grant["id"], "blocked.txt", "blocked")

    state = tmp_path / "other-state"
    source_root = tmp_path / "source-root"
    destination_root = tmp_path / "destination-root"
    source_root.mkdir()
    destination_root.mkdir()
    store = WorkspaceGrantStore(state)
    source = store.add(source_root, label="Source", access="read_write")
    destination = store.add(destination_root, label="Destination", access="read_write")
    runtime = WorkspaceFilesystem(store, state_root=state)
    receipt = runtime.write(source["id"], "move.txt", "move me")

    with pytest.raises(WorkspaceApprovalRequired, match="cross-root"):
        runtime.move(
            source["id"],
            "move.txt",
            destination["id"],
            "move.txt",
            expected_source_hash=receipt["after_hash"],
        )


def test_atomic_write_failure_preserves_original_and_removes_temporary_file(
    tmp_path, monkeypatch
):
    root, grant, files = workspace(tmp_path)
    created = files.write(grant["id"], "stable.txt", "original")
    real_replace = os.replace

    def fail_temp_replace(source, target, *args, **kwargs):
        if str(source).startswith(".sourcecado-write-"):
            raise OSError("simulated rename failure")
        return real_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_temp_replace)

    with pytest.raises(OSError, match="simulated"):
        files.write(
            grant["id"],
            "stable.txt",
            "replacement",
            expected_before_hash=created["after_hash"],
        )
    assert (root / "stable.txt").read_text() == "original"
    assert list(root.glob(".sourcecado-write-*")) == []


def test_atomic_updates_and_copies_preserve_executable_mode(tmp_path):
    root, grant, files = workspace(tmp_path)
    created = files.write(grant["id"], "script.sh", "#!/bin/sh\nprintf first\n")
    (root / "script.sh").chmod(0o755)

    files.write(
        grant["id"],
        "script.sh",
        "#!/bin/sh\nprintf second\n",
        expected_before_hash=created["after_hash"],
    )
    copied = files.copy(grant["id"], "script.sh", grant["id"], "copy.sh")

    assert (root / "script.sh").stat().st_mode & 0o777 == 0o755
    assert (root / "copy.sh").stat().st_mode & 0o777 == 0o755
    assert copied["after_hash"] == files.stat(grant["id"], "copy.sh")["sha256"]


def test_atomic_write_fails_if_parent_binding_is_swapped_after_validation(
    tmp_path, monkeypatch
):
    root, grant, files = workspace(tmp_path)
    parent = root / "nested"
    parent.mkdir()
    target = parent / "target.txt"
    target.write_text("original")
    outside = tmp_path / "outside"
    outside.mkdir()
    original_hash = files.stat(grant["id"], "nested/target.txt")["sha256"]
    real_assert = files._assert_parent_binding
    swapped = False

    def swap_parent(grant_record, parent_parts, parent_fd):
        nonlocal swapped
        if not swapped:
            swapped = True
            parent.rename(root / "nested-original")
            parent.symlink_to(outside, target_is_directory=True)
        return real_assert(grant_record, parent_parts, parent_fd)

    monkeypatch.setattr(files, "_assert_parent_binding", swap_parent)

    with pytest.raises(StaleWorkspaceWrite, match="binding|changed"):
        files.write(
            grant["id"],
            "nested/target.txt",
            "replacement",
            expected_before_hash=original_hash,
        )
    assert not (outside / "target.txt").exists()
