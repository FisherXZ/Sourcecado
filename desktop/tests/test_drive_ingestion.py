import importlib.util
import asyncio
import threading

import pytest

from coworker.apollo import HttpError
from coworker.connectors.google_oauth import (
    DRIVE_SCOPE,
    TOKEN_URL,
    load_google,
    save_google,
)
from coworker.drive import FILES_URL, FOLDER_MIME, DriveApi
from coworker.drive_ingestion import (
    DriveIngestionCoordinator,
    DriveIngestionRunner,
    DriveIngestionStore,
)
from coworker.people import PersonStore
from coworker.permissions import decide
from coworker.secrets import SecretStore
from coworker.tools import OPENAI_TOOLS, execute


def _file(file_id, name, parent_id, *, modified="2026-08-27T10:00:00Z"):
    return {
        "id": file_id,
        "name": name,
        "mimeType": "text/plain",
        "modifiedTime": modified,
        "parents": [parent_id],
        "webViewLink": f"https://drive.example/{file_id}",
    }


class FakeDrive:
    def __init__(self, pages, reads):
        self.pages = pages
        self.reads = reads
        self.list_calls = []
        self.read_calls = []

    def list_folder(self, folder_id, max_results=1000, page_token=None):
        self.list_calls.append((folder_id, page_token))
        return self.pages[(folder_id, page_token)]

    def read(self, file_id, max_chars=20000):
        self.read_calls.append(file_id)
        return self.reads[file_id]


def test_drive_ingestion_module_exists():
    assert importlib.util.find_spec("coworker.drive_ingestion") is not None


def test_job_persists_the_selected_folder_identity_and_resolved_path(tmp_path):
    created = DriveIngestionStore(tmp_path).create_job(
        folder_id="fall-2026-folder-id",
        resolved_path="Sourcing/Fall 2026",
    )

    reopened = DriveIngestionStore(tmp_path).get_job(created["id"])
    assert reopened is not None
    assert reopened["folder_id"] == "fall-2026-folder-id"
    assert reopened["resolved_path"] == "Sourcing/Fall 2026"
    assert reopened["status"] == "pending"
    assert reopened["generation"] == 1
    assert reopened["progress"] == {
        "folders_discovered": 1,
        "files_discovered": 0,
        "read": 0,
        "metadata_only": 0,
        "skipped": 0,
        "failed": 0,
        "deleted": 0,
        "remaining": 1,
    }


@pytest.mark.parametrize(
    ("folder_id", "resolved_path", "message"),
    [
        ("", "Sourcing/Fall 2026", "folder_id is required"),
        ("fall-2026", "", "resolved_path is required"),
        ("fall-2026", "Sourcing/../Other", "resolved_path must be a resolved Drive path"),
    ],
)
def test_job_rejects_unresolved_folder_selection(
    tmp_path, folder_id, resolved_path, message
):
    with pytest.raises(ValueError, match=message):
        DriveIngestionStore(tmp_path).create_job(
            folder_id=folder_id,
            resolved_path=resolved_path,
        )


def test_runner_recurses_through_paginated_selected_tree(tmp_path):
    root_file = _file("root-file", "root.txt", "root")
    second_file = _file("second-file", "second.txt", "root")
    nested_file = _file("nested-file", "nested.txt", "nested-folder")
    nested_folder = {
        "id": "nested-folder",
        "name": "Nested",
        "mimeType": FOLDER_MIME,
        "modifiedTime": "2026-08-27T09:00:00Z",
        "parents": ["root"],
        "webViewLink": "https://drive.example/nested-folder",
    }
    pages = {
        ("root", None): {"files": [root_file, nested_folder], "nextPageToken": "page-2"},
        ("root", "page-2"): {"files": [second_file], "nextPageToken": None},
        ("nested-folder", None): {"files": [nested_file], "nextPageToken": None},
    }
    reads = {
        row["id"]: {
            **row,
            "content": f"Indexed {row['name']}",
            "status": "read",
            "truncated": False,
            "sources": [
                {
                    "id": row["id"],
                    "title": row["name"],
                    "url": row["webViewLink"],
                    "provider": "Google Drive",
                    "truncated": False,
                }
            ],
            "sensitive_content_redacted": False,
            "redaction_count": 0,
        }
        for row in (root_file, second_file, nested_file)
    }
    drive = FakeDrive(pages, reads)
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")

    completed = DriveIngestionRunner(store, drive).run(job["id"])

    assert completed["status"] == "completed"
    assert completed["progress"] == {
        "folders_discovered": 2,
        "files_discovered": 3,
        "read": 3,
        "metadata_only": 0,
        "skipped": 0,
        "failed": 0,
        "deleted": 0,
        "remaining": 0,
    }
    assert drive.list_calls == [
        ("root", None),
        ("root", "page-2"),
        ("nested-folder", None),
    ]
    assert set(drive.read_calls) == {"root-file", "second-file", "nested-file"}
    sources = store.list_sources(job["id"])
    assert [(row["drive_id"], row["path"]) for row in sources] == [
        ("nested-file", "Sourcing/Fall 2026/Nested/nested.txt"),
        ("root-file", "Sourcing/Fall 2026/root.txt"),
        ("second-file", "Sourcing/Fall 2026/second.txt"),
    ]
    assert sources[0]["parent_id"] == "nested-folder"
    assert sources[0]["mime_type"] == "text/plain"
    assert sources[0]["modified_time"] == "2026-08-27T10:00:00Z"
    assert sources[0]["sensitivity"] == "standard"
    assert sources[0]["extraction_status"] == "read"
    assert sources[0]["citations"][0]["id"] == "nested-file"
    assert sources[0]["citations"][0]["path"] == sources[0]["path"]


def test_cancel_finishes_current_source_and_resume_starts_at_next_source(tmp_path):
    first = _file("file-a", "a.txt", "root")
    second = _file("file-b", "b.txt", "root")
    pages = {
        ("root", None): {"files": [first, second], "nextPageToken": None},
    }
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")

    class CancellingDrive(FakeDrive):
        def read(self, file_id, max_chars=20000):
            result = super().read(file_id, max_chars=max_chars)
            if len(self.read_calls) == 1:
                store.request_cancel(job["id"])
            return result

    reads = {
        row["id"]: {
            **row,
            "content": row["name"],
            "status": "read",
            "truncated": False,
            "sources": [],
            "sensitive_content_redacted": False,
            "redaction_count": 0,
        }
        for row in (first, second)
    }
    drive = CancellingDrive(pages, reads)

    paused = DriveIngestionRunner(store, drive).run(job["id"])

    assert paused["status"] == "paused"
    assert drive.read_calls == ["file-a"]
    assert paused["progress"]["read"] == 1
    assert paused["progress"]["remaining"] == 1

    reopened = DriveIngestionStore(tmp_path)
    completed = DriveIngestionRunner(reopened, drive).run(job["id"])

    assert completed["status"] == "completed"
    assert drive.read_calls == ["file-a", "file-b"]
    assert completed["progress"]["read"] == 2
    assert completed["progress"]["remaining"] == 0


def test_process_restart_recovers_an_interrupted_running_job_as_paused(tmp_path):
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")

    class CrashingDrive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        DriveIngestionRunner(store, CrashingDrive()).run(job["id"])

    recovered = DriveIngestionStore(tmp_path).get_job(job["id"])
    assert recovered is not None
    assert recovered["status"] == "paused"
    assert recovered["progress"]["remaining"] == 1


def test_rerun_skips_unchanged_updates_modified_and_marks_deleted(tmp_path):
    unchanged = _file("unchanged", "unchanged.txt", "root")
    modified = _file("modified", "modified.txt", "root")
    deleted = _file("deleted", "deleted.txt", "root")
    pages = {
        ("root", None): {
            "files": [unchanged, modified, deleted],
            "nextPageToken": None,
        }
    }

    def result(row, content):
        return {
            **row,
            "content": content,
            "status": "read",
            "truncated": False,
            "sources": [],
            "sensitive_content_redacted": False,
            "redaction_count": 0,
        }

    reads = {
        "unchanged": result(unchanged, "same"),
        "modified": result(modified, "before"),
        "deleted": result(deleted, "remove me"),
    }
    drive = FakeDrive(pages, reads)
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    DriveIngestionRunner(store, drive).run(job["id"])
    assert set(drive.read_calls) == {"unchanged", "modified", "deleted"}

    modified_v2 = _file(
        "modified",
        "modified.txt",
        "root",
        modified="2026-08-28T12:00:00Z",
    )
    drive.pages[("root", None)] = {
        "files": [unchanged, modified_v2],
        "nextPageToken": None,
    }
    drive.reads["modified"] = result(modified_v2, "after")
    drive.read_calls.clear()

    rerun = store.prepare_rerun(job["id"])
    completed = DriveIngestionRunner(store, drive).run(rerun["id"])

    assert completed["generation"] == 2
    assert completed["status"] == "completed"
    assert completed["progress"] == {
        "folders_discovered": 1,
        "files_discovered": 2,
        "read": 1,
        "metadata_only": 0,
        "skipped": 1,
        "failed": 0,
        "deleted": 1,
        "remaining": 0,
    }
    assert drive.read_calls == ["modified"]
    by_id = {row["drive_id"]: row for row in store.list_sources(job["id"])}
    assert by_id["unchanged"]["last_action"] == "skipped"
    assert by_id["modified"]["content"] == "after"
    assert by_id["modified"]["last_action"] == "read"
    assert by_id["deleted"]["deleted"] is True
    assert by_id["deleted"]["extraction_status"] == "deleted"
    assert by_id["deleted"]["content"] is None


def test_partial_source_failure_keeps_safe_receipt_and_other_results(tmp_path):
    metadata = _file("metadata", "form", "root")
    failed = _file("failed", "broken.docx", "root")
    pages = {
        ("root", None): {"files": [metadata, failed], "nextPageToken": None},
    }

    class PartialDrive(FakeDrive):
        def read(self, file_id, max_chars=20000):
            self.read_calls.append(file_id)
            if file_id == "failed":
                raise RuntimeError("access_token=PLANTED-SECRET")
            return self.reads[file_id]

    drive = PartialDrive(
        pages,
        {
            "metadata": {
                **metadata,
                "status": "metadata_only",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }
        },
    )
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")

    completed = DriveIngestionRunner(store, drive).run(job["id"])

    assert completed["status"] == "completed_with_errors"
    assert completed["progress"]["metadata_only"] == 1
    assert completed["progress"]["failed"] == 1
    failed_source = next(
        row for row in store.list_sources(job["id"]) if row["drive_id"] == "failed"
    )
    assert failed_source["error_kind"] == "source_read_failed"
    assert failed_source["citations"] == [
        {
            "id": "failed",
            "path": "Sourcing/Fall 2026/broken.docx",
            "provider": "Google Drive",
            "title": "broken.docx",
            "truncated": False,
            "url": "https://drive.example/failed",
        }
    ]
    assert "PLANTED-SECRET" not in repr(completed)
    assert "PLANTED-SECRET" not in repr(store.list_sources(job["id"]))


def test_completed_index_is_queryable_without_rereading_drive(tmp_path):
    source = _file("brief", "Research brief.txt", "root")
    drive = FakeDrive(
        {("root", None): {"files": [source], "nextPageToken": None}},
        {
            "brief": {
                **source,
                "content": "Alyssa is preparing the fall research dinner.",
                "status": "read",
                "truncated": False,
                "sources": [
                    {
                        "id": "brief",
                        "title": "Research brief.txt",
                        "url": source["webViewLink"],
                        "provider": "Google Drive",
                        "truncated": False,
                    }
                ],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }
        },
    )
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    DriveIngestionRunner(store, drive).run(job["id"])
    drive.read_calls.clear()

    result = store.query(job["id"], "research dinner")

    assert drive.read_calls == []
    assert result["job_id"] == job["id"]
    assert result["query"] == "research dinner"
    assert result["matches"] == [
        {
            "drive_id": "brief",
            "path": "Sourcing/Fall 2026/Research brief.txt",
            "mime_type": "text/plain",
            "modified_time": "2026-08-27T10:00:00Z",
            "sensitivity": "standard",
            "extraction_status": "read",
            "scope": "tree",
            "out_of_scope": False,
            "snippet": "Alyssa is preparing the fall research dinner.",
            "citations": [
                {
                    "id": "brief",
                    "path": "Sourcing/Fall 2026/Research brief.txt",
                    "provider": "Google Drive",
                    "title": "Research brief.txt",
                    "truncated": False,
                    "url": "https://drive.example/brief",
                }
            ],
        }
    ]


def test_global_match_is_excluded_unless_operator_explicitly_adds_it(tmp_path):
    tree_source = _file("tree", "Tree note.txt", "root")
    external = _file("external", "Global note.txt", "somewhere-else")
    drive = FakeDrive(
        {("root", None): {"files": [tree_source], "nextPageToken": None}},
        {
            "tree": {
                **tree_source,
                "content": "Tree evidence only.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            },
            "external": {
                **external,
                "content": "Global evidence explicitly added.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            },
        },
    )
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    DriveIngestionRunner(store, drive).run(job["id"])
    store.add_explicit_source(
        job["id"],
        drive_id="external",
        name="Global note.txt",
        parent_id="somewhere-else",
        display_path="External/Global note.txt",
        mime_type="text/plain",
        modified_time="2026-08-27T10:00:00Z",
        web_view_link="https://drive.example/external",
    )
    DriveIngestionRunner(store, drive).run(job["id"])

    assert store.query(job["id"], "global")["matches"] == []
    included = store.query(job["id"], "global", include_external=True)["matches"]
    assert len(included) == 1
    assert included[0]["drive_id"] == "external"
    assert included[0]["scope"] == "explicit_global"
    assert included[0]["out_of_scope"] is True


def test_board_proposal_is_reviewable_and_never_writes_before_explicit_apply(tmp_path):
    source = _file("brief", "Brief.txt", "root")
    drive = FakeDrive(
        {("root", None): {"files": [source], "nextPageToken": None}},
        {
            "brief": {
                **source,
                "content": "Relevant evidence for Ada.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }
        },
    )
    store = DriveIngestionStore(tmp_path / "ingestion")
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    DriveIngestionRunner(store, drive).run(job["id"])
    people = PersonStore(tmp_path / "people")
    person = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L***e",
        title="Founder",
        company="Analytic",
    )

    proposal = store.propose_board_attachment(
        job["id"],
        source_drive_id="brief",
        person_id=person["person_id"],
        record_type="source_ref",
        fields={"title": "Fall research brief", "excerpt": "Relevant evidence for Ada."},
    )

    assert proposal["status"] == "pending_review"
    assert proposal["diff"]["operation"] == "board_upsert"
    assert proposal["diff"]["after"]["drive_id"] == "brief"
    assert proposal["source_refs"][0]["id"] == "brief"
    before = people.get(person["person_id"], expand_sources=True)
    assert before is not None
    assert before["sources"] == []

    applied = store.apply_board_proposal(
        proposal["id"],
        people=people,
        actor="director",
    )

    assert applied["status"] == "applied"
    after = people.get(person["person_id"], expand_sources=True)
    assert after is not None
    assert after["sources"][0]["fields"]["drive_id"] == "brief"
    assert after["sources"][0]["fields"]["path"] == "Sourcing/Fall 2026/Brief.txt"
    assert after["sources"][0]["fields"]["sensitivity"] == "standard"
    assert people.timeline(person["person_id"])[-1]["run_id"] == job["id"]


def test_coordinator_runs_ingestion_outside_the_requesting_coroutine(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingDrive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            started.set()
            assert release.wait(2)
            return {"files": [], "nextPageToken": None}

    async def scenario():
        store = DriveIngestionStore(tmp_path)
        job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
        coordinator = DriveIngestionCoordinator(store, lambda: BlockingDrive())

        task = await coordinator.start(job["id"])

        assert await asyncio.to_thread(started.wait, 1)
        assert task.done() is False
        assert store.get_job(job["id"])["status"] == "running"
        release.set()
        completed = await coordinator.wait(job["id"])
        assert completed["status"] == "completed"

    asyncio.run(scenario())


def test_chat_tool_queries_completed_local_index_without_drive_client(tmp_path):
    source = _file("brief", "Brief.txt", "root")
    drive = FakeDrive(
        {("root", None): {"files": [source], "nextPageToken": None}},
        {
            "brief": {
                **source,
                "content": "Fall research dinner evidence.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }
        },
    )
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    DriveIngestionRunner(store, drive).run(job["id"])
    drive.read_calls.clear()

    ok, result = execute(
        "drive_index_query",
        {"job_id": job["id"], "query": "research dinner"},
        drive_ingestions=store,
    )

    names = {schema["function"]["name"] for schema in OPENAI_TOOLS}
    assert "drive_index_query" in names
    assert decide("drive_index_query").allowed is True
    assert ok is True
    assert result["matches"][0]["drive_id"] == "brief"
    assert drive.read_calls == []


def test_duplicate_folder_names_remain_distinct_by_stable_parent_id(tmp_path):
    first_file = _file("file-a", "note.txt", "folder-a")
    second_file = _file("file-b", "note.txt", "folder-b")
    folders = [
        {
            "id": folder_id,
            "name": "Research",
            "mimeType": FOLDER_MIME,
            "modifiedTime": "2026-08-27T10:00:00Z",
            "parents": ["root"],
            "webViewLink": f"https://drive.example/{folder_id}",
        }
        for folder_id in ("folder-a", "folder-b")
    ]
    drive = FakeDrive(
        {
            ("root", None): {"files": folders, "nextPageToken": None},
            ("folder-a", None): {"files": [first_file], "nextPageToken": None},
            ("folder-b", None): {"files": [second_file], "nextPageToken": None},
        },
        {
            row["id"]: {
                **row,
                "content": row["id"],
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }
            for row in (first_file, second_file)
        },
    )
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")

    DriveIngestionRunner(store, drive).run(job["id"])

    sources = store.list_sources(job["id"])
    assert {row["drive_id"] for row in sources} == {"file-a", "file-b"}
    assert {row["parent_id"] for row in sources} == {"folder-a", "folder-b"}
    assert {row["path"] for row in sources} == {
        "Sourcing/Fall 2026/Research/note.txt"
    }


def test_real_drive_client_refreshes_token_and_redacts_before_index_storage(tmp_path):
    raw_secret = "drive_ingestion_secret_1234567890"
    file_row = {
        "id": "brief",
        "name": "Sourcing SOP",
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-08-27T10:00:00Z",
        "parents": ["root"],
        "webViewLink": "https://drive.example/brief",
    }

    class RefreshingHttp:
        def __init__(self):
            self.calls = []

        def get(self, url, *, headers=None, params=None):
            self.calls.append(("GET", url, dict(headers or {}), dict(params or {})))
            if url == FILES_URL:
                if headers.get("Authorization") == "Bearer stale":
                    raise HttpError(401, url, "PRIVATE_PROVIDER_BODY")
                return {"files": [file_row], "nextPageToken": None}
            if url == f"{FILES_URL}/brief":
                return file_row
            if url == f"{FILES_URL}/brief/export":
                return f"APOLLO_API_KEY={raw_secret}"
            raise AssertionError(url)

        def post(self, url, *, headers=None, json=None, data=None, params=None):
            self.calls.append(("POST", url, dict(headers or {}), dict(data or {})))
            assert url == TOKEN_URL
            return {"access_token": "fresh"}

    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(
        secrets,
        {
            "refresh_token": "refresh",
            "access_token": "stale",
            "scopes": [DRIVE_SCOPE],
        },
    )
    http = RefreshingHttp()
    drive = DriveApi(secrets, http=http, client_id="cid", client_secret="secret")
    store = DriveIngestionStore(tmp_path / "state")
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")

    completed = DriveIngestionRunner(store, drive).run(job["id"])

    assert completed["status"] == "completed"
    assert load_google(secrets)["access_token"] == "fresh"
    assert [call[0:2] for call in http.calls].count(("POST", TOKEN_URL)) == 1
    source = store.list_sources(job["id"])[0]
    assert source["sensitivity"] == "restricted"
    assert source["redaction_count"] == 1
    assert source["content"] == "APOLLO_API_KEY=[REDACTED]"
    assert raw_secret not in repr(source)
    assert store.query(job["id"], "APOLLO")["matches"] == []


def test_nested_folder_failure_is_counted_without_discarding_good_sources(tmp_path):
    good = _file("good", "good.txt", "root")
    broken_folder = {
        "id": "broken-folder",
        "name": "Broken",
        "mimeType": FOLDER_MIME,
        "modifiedTime": "2026-08-27T10:00:00Z",
        "parents": ["root"],
        "webViewLink": "https://drive.example/broken-folder",
    }

    class Drive(FakeDrive):
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            if folder_id == "broken-folder":
                raise RuntimeError("PRIVATE_PROVIDER_BODY")
            return super().list_folder(folder_id, max_results, page_token)

    drive = Drive(
        {("root", None): {"files": [good, broken_folder], "nextPageToken": None}},
        {
            "good": {
                **good,
                "content": "kept",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }
        },
    )
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")

    completed = DriveIngestionRunner(store, drive).run(job["id"])

    assert completed["status"] == "completed_with_errors"
    assert completed["progress"]["read"] == 1
    assert completed["progress"]["failed"] == 1
    assert store.list_sources(job["id"])[0]["content"] == "kept"
    assert "PRIVATE_PROVIDER_BODY" not in repr(completed)


def test_failed_rerun_never_tombstones_sources_from_incomplete_traversal(tmp_path):
    source = _file("kept", "kept.txt", "root")
    drive = FakeDrive(
        {("root", None): {"files": [source], "nextPageToken": None}},
        {
            "kept": {
                **source,
                "content": "durable evidence",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }
        },
    )
    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    DriveIngestionRunner(store, drive).run(job["id"])

    class FailedRerunDrive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            raise RuntimeError("transient folder failure")

    store.prepare_rerun(job["id"])
    completed = DriveIngestionRunner(store, FailedRerunDrive()).run(job["id"])

    assert completed["status"] == "completed_with_errors"
    retained = store.list_sources(job["id"])[0]
    assert retained["drive_id"] == "kept"
    assert retained["deleted"] is False
    assert retained["content"] == "durable evidence"


def test_cancel_requested_before_worker_start_pauses_without_touching_drive(tmp_path):
    class UntouchedDrive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            raise AssertionError("Drive must not be touched after pre-start cancel")

    store = DriveIngestionStore(tmp_path)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    store.request_cancel(job["id"])

    paused = DriveIngestionRunner(store, UntouchedDrive()).run(job["id"])

    assert paused["status"] == "paused"
    assert paused["progress"]["remaining"] == 1


def test_ingestion_store_keeps_database_and_sidecars_private(tmp_path):
    state = tmp_path / "state"
    DriveIngestionStore(state).create_job(
        folder_id="root",
        resolved_path="Sourcing/Fall 2026",
    )

    assert state.stat().st_mode & 0o777 == 0o700
    for path in state.iterdir():
        if path.is_file():
            assert path.stat().st_mode & 0o077 == 0
    assert not any(path.name.endswith(("-wal", "-shm")) for path in state.iterdir())
