import importlib.util
import json
import time
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from coworker.drive_ingestion import (
    DriveIngestionCoordinator,
    DriveIngestionRunner,
    DriveIngestionStore,
)
from coworker.drive_ingestion_api import drive_ingestion_router
from coworker.people import PersonStore
from coworker.provider import FakeProvider, ToolCall
from coworker.server import TOKEN_HEADER, create_app


TOKEN = "drive-ingestion-token"


def test_drive_ingestion_api_module_exists():
    assert importlib.util.find_spec("coworker.drive_ingestion_api") is not None


def test_api_starts_background_job_and_queries_completed_index(tmp_path):
    source = {
        "id": "brief",
        "name": "Brief.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-08-27T10:00:00Z",
        "parents": ["root"],
        "webViewLink": "https://drive.example/brief",
    }

    class Drive:
        def __init__(self):
            self.read_calls = []

        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [source], "nextPageToken": None}

        def read(self, file_id, max_chars=20000):
            self.read_calls.append(file_id)
            return {
                **source,
                "content": "Fall research dinner evidence.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }

    drive = Drive()
    store = DriveIngestionStore(tmp_path / "state")
    coordinator = DriveIngestionCoordinator(store, lambda: drive)
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=coordinator,
            people=PersonStore(tmp_path / "people"),
        )
    )

    with TestClient(app) as client:
        started = client.post(
            "/v1/drive-ingestions",
            json={"folder_id": "root", "resolved_path": "Sourcing/Fall 2026"},
        )
        assert started.status_code == 202
        job_id = started.json()["job"]["id"]
        for _ in range(100):
            status = client.get(f"/v1/drive-ingestions/{job_id}")
            assert status.status_code == 200
            if status.json()["job"]["status"] == "completed":
                break
            time.sleep(0.01)
        assert status.json()["job"]["status"] == "completed"

        receipts = client.get(f"/v1/drive-ingestions/{job_id}/sources")
        assert receipts.status_code == 200
        assert receipts.json()["sources"] == [
            {
                "drive_id": "brief",
                "scope": "tree",
                "parent_id": "root",
                "path": "Sourcing/Fall 2026/Brief.txt",
                "name": "Brief.txt",
                "mime_type": "text/plain",
                "modified_time": "2026-08-27T10:00:00Z",
                "sensitivity": "standard",
                "extraction_status": "read",
                "citations": [
                    {
                        "id": "brief",
                        "path": "Sourcing/Fall 2026/Brief.txt",
                        "provider": "Google Drive",
                        "title": "Brief.txt",
                        "truncated": False,
                        "url": "https://drive.example/brief",
                    }
                ],
                "redaction_count": 0,
                "deleted": False,
                "last_action": "read",
                "error_kind": None,
            }
        ]

        drive.read_calls.clear()
        queried = client.get(
            f"/v1/drive-ingestions/{job_id}/query",
            params={"q": "research dinner"},
        )
        assert queried.status_code == 200
        assert queried.json()["matches"][0]["drive_id"] == "brief"
        assert drive.read_calls == []


def test_api_cancels_at_checkpoint_and_resumes_same_job(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class Drive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            entered.set()
            assert release.wait(2)
            return {"files": [], "nextPageToken": None}

    store = DriveIngestionStore(tmp_path / "state")
    coordinator = DriveIngestionCoordinator(store, Drive)
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=coordinator,
            people=PersonStore(tmp_path / "people"),
        )
    )

    with TestClient(app) as client:
        started = client.post(
            "/v1/drive-ingestions",
            json={"folder_id": "root", "resolved_path": "Sourcing/Fall 2026"},
        )
        job_id = started.json()["job"]["id"]
        assert entered.wait(1)

        cancelling = client.post(f"/v1/drive-ingestions/{job_id}/cancel")
        assert cancelling.status_code == 202
        assert cancelling.json()["job"]["status"] == "cancel_requested"
        release.set()
        for _ in range(100):
            paused = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if paused["status"] == "paused":
                break
            time.sleep(0.01)
        assert paused["status"] == "paused"

        resumed = client.post(f"/v1/drive-ingestions/{job_id}/resume")
        assert resumed.status_code == 202
        for _ in range(100):
            completed = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if completed["status"] == "completed":
                break
            time.sleep(0.01)
        assert completed["status"] == "completed"
        assert completed["id"] == job_id


def test_disconnected_resume_keeps_paused_job_resumable(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class Drive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            entered.set()
            assert release.wait(2)
            return {"files": [], "nextPageToken": None}

    connected = {"drive": Drive()}
    state = tmp_path / "state"
    store = DriveIngestionStore(state)
    coordinator = DriveIngestionCoordinator(store, lambda: connected["drive"])
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=coordinator,
            people=PersonStore(tmp_path / "people"),
        )
    )

    with TestClient(app) as client:
        started = client.post(
            "/v1/drive-ingestions",
            json={"folder_id": "root", "resolved_path": "Sourcing/Fall 2026"},
        )
        job_id = started.json()["job"]["id"]
        assert entered.wait(1)

        cancelling = client.post(f"/v1/drive-ingestions/{job_id}/cancel")
        assert cancelling.status_code == 202
        release.set()
        for _ in range(100):
            paused = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if paused["status"] == "paused":
                break
            time.sleep(0.01)
        assert paused["status"] == "paused"

        connected["drive"] = None
        resume = client.post(f"/v1/drive-ingestions/{job_id}/resume")
        assert resume.status_code == 409
        assert resume.json() == {"error": "Drive is not connected"}

        after_resume = client.get(f"/v1/drive-ingestions/{job_id}")
        assert after_resume.status_code == 200
        assert after_resume.json()["job"]["status"] == "paused"

        persisted = DriveIngestionStore(state)
        reopened_app = FastAPI()
        reopened_app.include_router(
            drive_ingestion_router(
                store=persisted,
                coordinator=DriveIngestionCoordinator(persisted, lambda: None),
                people=PersonStore(tmp_path / "people-reopen"),
            )
        )
        restarted = TestClient(reopened_app).get(f"/v1/drive-ingestions/{job_id}")
        assert restarted.status_code == 200
        assert restarted.json()["job"]["status"] == "paused"

        connected["drive"] = Drive()
        recovered = client.post(f"/v1/drive-ingestions/{job_id}/resume")
        assert recovered.status_code == 202
        for _ in range(100):
            completed = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if completed["status"] == "completed":
                break
            time.sleep(0.01)
        assert completed["status"] == "completed"
        assert completed["id"] == job_id


def test_api_requires_separate_review_action_before_board_write(tmp_path):
    source = {
        "id": "brief",
        "name": "Brief.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-08-27T10:00:00Z",
        "parents": ["root"],
        "webViewLink": "https://drive.example/brief",
    }

    class Drive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [source], "nextPageToken": None}

        def read(self, file_id, max_chars=20000):
            return {
                **source,
                "content": "Evidence for Ada.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }

    store = DriveIngestionStore(tmp_path / "state")
    coordinator = DriveIngestionCoordinator(store, Drive)
    people = PersonStore(tmp_path / "people")
    person = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L***e",
        title="Founder",
        company="Analytic",
    )
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(store=store, coordinator=coordinator, people=people)
    )

    with TestClient(app) as client:
        started = client.post(
            "/v1/drive-ingestions",
            json={"folder_id": "root", "resolved_path": "Sourcing/Fall 2026"},
        )
        job_id = started.json()["job"]["id"]
        for _ in range(100):
            job = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.01)
        proposed = client.post(
            f"/v1/drive-ingestions/{job_id}/proposals",
            json={
                "source_drive_id": "brief",
                "person_id": person["person_id"],
                "record_type": "source_ref",
                "fields": {"title": "Reviewed brief"},
            },
        )
        assert proposed.status_code == 201
        proposal = proposed.json()["proposal"]
        assert proposal["status"] == "pending_review"
        assert people.get(person["person_id"], expand_sources=True)["sources"] == []
        review_queue = client.get(f"/v1/drive-ingestions/{job_id}/proposals")
        assert review_queue.status_code == 200
        assert [row["id"] for row in review_queue.json()["proposals"]] == [
            proposal["id"]
        ]

        applied = client.post(
            f"/v1/drive-ingestion-proposals/{proposal['id']}/apply"
        )
        assert applied.status_code == 200
        assert applied.json()["proposal"]["status"] == "applied"
        assert people.get(person["person_id"], expand_sources=True)["sources"][0][
            "fields"
        ]["drive_id"] == "brief"


def test_api_reruns_completed_job_from_new_generation(tmp_path):
    source = {
        "id": "brief",
        "name": "Brief.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-08-27T10:00:00Z",
        "parents": ["root"],
        "webViewLink": "https://drive.example/brief",
    }

    class Drive:
        def __init__(self):
            self.source = dict(source)
            self.read_calls = []

        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [dict(self.source)], "nextPageToken": None}

        def read(self, file_id, max_chars=20000):
            self.read_calls.append(file_id)
            return {
                **self.source,
                "content": self.source["modifiedTime"],
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }

    drive = Drive()
    store = DriveIngestionStore(tmp_path / "state")
    coordinator = DriveIngestionCoordinator(store, lambda: drive)
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=coordinator,
            people=PersonStore(tmp_path / "people"),
        )
    )

    with TestClient(app) as client:
        started = client.post(
            "/v1/drive-ingestions",
            json={"folder_id": "root", "resolved_path": "Sourcing/Fall 2026"},
        )
        job_id = started.json()["job"]["id"]
        for _ in range(100):
            job = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.01)
        drive.source["modifiedTime"] = "2026-08-28T10:00:00Z"

        rerun = client.post(f"/v1/drive-ingestions/{job_id}/rerun")
        assert rerun.status_code == 202
        for _ in range(100):
            job = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if job["status"] == "completed" and job["generation"] == 2:
                break
            time.sleep(0.01)
        assert job["generation"] == 2
        assert drive.read_calls == ["brief", "brief"]


def test_disconnected_rerun_keeps_completed_index_queryable(tmp_path):
    source = {
        "id": "brief",
        "name": "Brief.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-08-27T10:00:00Z",
        "parents": ["root"],
        "webViewLink": "https://drive.example/brief",
    }

    class Drive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [source], "nextPageToken": None}

        def read(self, file_id, max_chars=20000):
            return {
                **source,
                "content": "Fall research dinner evidence.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }

    connected = {"drive": Drive()}
    state = tmp_path / "state"
    store = DriveIngestionStore(state)
    coordinator = DriveIngestionCoordinator(store, lambda: connected["drive"])
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=coordinator,
            people=PersonStore(tmp_path / "people"),
        )
    )

    with TestClient(app) as client:
        started = client.post(
            "/v1/drive-ingestions",
            json={"folder_id": "root", "resolved_path": "Sourcing/Fall 2026"},
        )
        job_id = started.json()["job"]["id"]
        for _ in range(100):
            job = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        assert job["generation"] == 1
        queried = client.get(
            f"/v1/drive-ingestions/{job_id}/query",
            params={"q": "brief"},
        )
        assert queried.status_code == 200
        assert queried.json()["matches"][0]["drive_id"] == "brief"

        connected["drive"] = None
        rerun = client.post(f"/v1/drive-ingestions/{job_id}/rerun")
        assert rerun.status_code == 409
        assert rerun.json() == {"error": "Drive is not connected"}

        after_rerun = client.get(f"/v1/drive-ingestions/{job_id}")
        assert after_rerun.status_code == 200
        assert after_rerun.json()["job"]["status"] == "completed"
        assert after_rerun.json()["job"]["generation"] == 1
        still_indexed = client.get(
            f"/v1/drive-ingestions/{job_id}/query",
            params={"q": "brief"},
        )
        assert still_indexed.status_code == 200
        assert still_indexed.json()["matches"][0]["drive_id"] == "brief"

        persisted = DriveIngestionStore(state)
        reopened_app = FastAPI()
        reopened_app.include_router(
            drive_ingestion_router(
                store=persisted,
                coordinator=DriveIngestionCoordinator(persisted, lambda: None),
                people=PersonStore(tmp_path / "people-reopen"),
            )
        )
        restarted = TestClient(reopened_app).get(f"/v1/drive-ingestions/{job_id}")
        assert restarted.status_code == 200
        assert restarted.json()["job"]["status"] == "completed"
        assert restarted.json()["job"]["generation"] == 1
        restarted_query = TestClient(reopened_app).get(
            f"/v1/drive-ingestions/{job_id}/query",
            params={"q": "brief"},
        )
        assert restarted_query.status_code == 200
        assert restarted_query.json()["matches"][0]["drive_id"] == "brief"

        connected["drive"] = Drive()
        recovered = client.post(f"/v1/drive-ingestions/{job_id}/rerun")
        assert recovered.status_code == 202
        for _ in range(100):
            job = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if job["status"] == "completed" and job["generation"] == 2:
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        assert job["generation"] == 2


def test_api_marks_operator_added_global_source_out_of_scope(tmp_path):
    class Drive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [], "nextPageToken": None}

        def read(self, file_id, max_chars=20000):
            return {
                "id": file_id,
                "name": "Global.txt",
                "mimeType": "text/plain",
                "modifiedTime": "2026-08-27T10:00:00Z",
                "parents": ["elsewhere"],
                "webViewLink": "https://drive.example/global",
                "content": "Explicit global evidence.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }

    store = DriveIngestionStore(tmp_path / "state")
    coordinator = DriveIngestionCoordinator(store, Drive)
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=coordinator,
            people=PersonStore(tmp_path / "people"),
        )
    )

    with TestClient(app) as client:
        started = client.post(
            "/v1/drive-ingestions",
            json={"folder_id": "root", "resolved_path": "Sourcing/Fall 2026"},
        )
        job_id = started.json()["job"]["id"]
        for _ in range(100):
            job = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.01)
        assert job["status"] == "completed"

        added = client.post(
            f"/v1/drive-ingestions/{job_id}/external-sources",
            json={
                "drive_id": "global",
                "name": "Global.txt",
                "parent_id": "elsewhere",
                "display_path": "External/Global.txt",
                "mime_type": "text/plain",
                "modified_time": "2026-08-27T10:00:00Z",
                "web_view_link": "https://drive.example/global",
            },
        )
        assert added.status_code == 202
        for _ in range(100):
            job = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        default = client.get(
            f"/v1/drive-ingestions/{job_id}/query", params={"q": "global"}
        )
        assert default.status_code == 200
        assert default.json()["matches"] == []
        included = client.get(
            f"/v1/drive-ingestions/{job_id}/query",
            params={"q": "global", "include_external": True},
        )
        assert included.status_code == 200
        assert included.json()["matches"][0]["drive_id"] == "global"
        assert included.json()["matches"][0]["out_of_scope"] is True
        assert included.json()["matches"][0]["extraction_status"] == "read"


def test_disconnected_explicit_source_keeps_completed_index_queryable(tmp_path):
    source = {
        "id": "brief",
        "name": "Brief.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-08-27T10:00:00Z",
        "parents": ["root"],
        "webViewLink": "https://drive.example/brief",
    }

    class Drive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [source], "nextPageToken": None}

        def read(self, file_id, max_chars=20000):
            return {
                **source,
                "content": "Fall research dinner evidence.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }

    connected = {"drive": Drive()}
    state = tmp_path / "state"
    store = DriveIngestionStore(state)
    coordinator = DriveIngestionCoordinator(store, lambda: connected["drive"])
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=coordinator,
            people=PersonStore(tmp_path / "people"),
        )
    )

    with TestClient(app) as client:
        started = client.post(
            "/v1/drive-ingestions",
            json={"folder_id": "root", "resolved_path": "Sourcing/Fall 2026"},
        )
        job_id = started.json()["job"]["id"]
        for _ in range(100):
            job = client.get(f"/v1/drive-ingestions/{job_id}").json()["job"]
            if job["status"] == "completed":
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        queried = client.get(
            f"/v1/drive-ingestions/{job_id}/query",
            params={"q": "brief"},
        )
        assert queried.status_code == 200
        assert queried.json()["matches"][0]["drive_id"] == "brief"

        connected["drive"] = None
        added = client.post(
            f"/v1/drive-ingestions/{job_id}/external-sources",
            json={
                "drive_id": "global",
                "name": "Global.txt",
                "parent_id": "elsewhere",
                "display_path": "External/Global.txt",
                "mime_type": "text/plain",
                "modified_time": "2026-08-27T10:00:00Z",
                "web_view_link": "https://drive.example/global",
            },
        )
        assert added.status_code == 409
        assert added.json() == {"error": "Drive is not connected"}

        after_add = client.get(f"/v1/drive-ingestions/{job_id}")
        assert after_add.status_code == 200
        assert after_add.json()["job"]["status"] == "completed"
        assert after_add.json()["job"]["generation"] == 1
        still_indexed = client.get(
            f"/v1/drive-ingestions/{job_id}/query",
            params={"q": "brief"},
        )
        assert still_indexed.status_code == 200
        assert still_indexed.json()["matches"][0]["drive_id"] == "brief"

        persisted = DriveIngestionStore(state)
        reopened_app = FastAPI()
        reopened_app.include_router(
            drive_ingestion_router(
                store=persisted,
                coordinator=DriveIngestionCoordinator(persisted, lambda: None),
                people=PersonStore(tmp_path / "people-reopen"),
            )
        )
        restarted = TestClient(reopened_app).get(f"/v1/drive-ingestions/{job_id}")
        assert restarted.status_code == 200
        assert restarted.json()["job"]["status"] == "completed"
        restarted_query = TestClient(reopened_app).get(
            f"/v1/drive-ingestions/{job_id}/query",
            params={"q": "brief"},
        )
        assert restarted_query.status_code == 200
        assert restarted_query.json()["matches"][0]["drive_id"] == "brief"

        connected["drive"] = Drive()
        recovered = client.post(
            f"/v1/drive-ingestions/{job_id}/external-sources",
            json={
                "drive_id": "global",
                "name": "Global.txt",
                "parent_id": "elsewhere",
                "display_path": "External/Global.txt",
                "mime_type": "text/plain",
                "modified_time": "2026-08-27T10:00:00Z",
                "web_view_link": "https://drive.example/global",
            },
        )
        assert recovered.status_code == 202


def test_active_backend_mounts_ingestion_under_local_auth_and_state(tmp_path):
    class Drive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [], "nextPageToken": None}

    app = create_app(token=TOKEN, state=tmp_path, provider=None)
    app.state.drive = Drive()

    with TestClient(app) as client:
        unauthorized = client.post(
            "/v1/drive-ingestions",
            json={"folder_id": "root", "resolved_path": "Sourcing/Fall 2026"},
        )
        assert unauthorized.status_code == 401
        started = client.post(
            "/v1/drive-ingestions",
            headers={TOKEN_HEADER: TOKEN},
            json={"folder_id": "root", "resolved_path": "Sourcing/Fall 2026"},
        )
        assert started.status_code == 202

    assert (tmp_path / "drive_ingestion.db").is_file()


def test_api_lists_durable_jobs_after_store_reopens(tmp_path):
    state = tmp_path / "state"
    created = DriveIngestionStore(state).create_job(
        folder_id="root",
        resolved_path="Sourcing/Fall 2026",
    )
    reopened = DriveIngestionStore(state)
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=reopened,
            coordinator=DriveIngestionCoordinator(reopened, lambda: None),
            people=PersonStore(tmp_path / "people"),
        )
    )

    response = TestClient(app).get("/v1/drive-ingestions")

    assert response.status_code == 200
    assert [job["id"] for job in response.json()["jobs"]] == [created["id"]]
    assert response.json()["jobs"][0]["resolved_path"] == "Sourcing/Fall 2026"


def test_private_ingestion_get_routes_disable_browser_caching(tmp_path):
    class EmptyDrive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [], "nextPageToken": None}

    store = DriveIngestionStore(tmp_path / "state")
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    DriveIngestionRunner(store, EmptyDrive()).run(job["id"])
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=DriveIngestionCoordinator(store, EmptyDrive),
            people=PersonStore(tmp_path / "people"),
        )
    )
    client = TestClient(app)

    responses = [
        client.get("/v1/drive-ingestions"),
        client.get(f"/v1/drive-ingestions/{job['id']}"),
        client.get(
            f"/v1/drive-ingestions/{job['id']}/query", params={"q": "anything"}
        ),
        client.get(f"/v1/drive-ingestions/{job['id']}/sources"),
        client.get(f"/v1/drive-ingestions/{job['id']}/proposals"),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert [response.headers.get("cache-control") for response in responses] == [
        "no-store"
    ] * 5


def test_disconnected_rerun_keeps_completed_index_queryable_after_restart(tmp_path):
    source = {
        "id": "brief",
        "name": "Brief.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-08-27T10:00:00Z",
        "parents": ["root"],
        "webViewLink": "https://drive.example/brief",
    }

    class Drive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [source], "nextPageToken": None}

        def read(self, file_id, max_chars=20000):
            return {
                **source,
                "content": "Fall research dinner evidence.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }

    state = tmp_path / "state"
    store = DriveIngestionStore(state)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    DriveIngestionRunner(store, Drive()).run(job["id"])
    before = store.get_job(job["id"])
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=DriveIngestionCoordinator(store, lambda: None),
            people=PersonStore(tmp_path / "people"),
        )
    )

    response = TestClient(app).post(f"/v1/drive-ingestions/{job['id']}/rerun")

    assert response.status_code == 409
    after = store.get_job(job["id"])
    assert after["status"] == "completed"
    assert after["generation"] == before["generation"]
    assert after["work_revision"] == before["work_revision"]
    assert store.query(job["id"], "research dinner")["matches"][0]["drive_id"] == "brief"
    restarted = DriveIngestionStore(state).get_job(job["id"])
    assert restarted["status"] == "completed"


def test_disconnected_explicit_add_keeps_completed_index_unchanged_after_restart(tmp_path):
    class EmptyDrive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [], "nextPageToken": None}

    state = tmp_path / "state"
    store = DriveIngestionStore(state)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    DriveIngestionRunner(store, EmptyDrive()).run(job["id"])
    before = store.get_job(job["id"])
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=DriveIngestionCoordinator(store, lambda: None),
            people=PersonStore(tmp_path / "people"),
        )
    )

    response = TestClient(app).post(
        f"/v1/drive-ingestions/{job['id']}/external-sources",
        json={
            "drive_id": "global",
            "name": "Global.txt",
            "parent_id": "elsewhere",
            "display_path": "External/Global.txt",
            "mime_type": "text/plain",
            "modified_time": "2026-08-27T10:00:00Z",
            "web_view_link": "https://drive.example/global",
        },
    )

    assert response.status_code == 409
    after = store.get_job(job["id"])
    assert after["status"] == "completed"
    assert after["work_revision"] == before["work_revision"]
    assert store.list_sources(job["id"]) == []
    restarted = DriveIngestionStore(state).get_job(job["id"])
    assert restarted["status"] == "completed"


def test_disconnected_resume_keeps_paused_job_resumable_after_restart(tmp_path):
    state = tmp_path / "state"
    store = DriveIngestionStore(state)
    job = store.create_job(folder_id="root", resolved_path="Sourcing/Fall 2026")
    store.request_cancel(job["id"])
    DriveIngestionRunner(store, object()).run(job["id"])
    before = store.get_job(job["id"])
    app = FastAPI()
    app.include_router(
        drive_ingestion_router(
            store=store,
            coordinator=DriveIngestionCoordinator(store, lambda: None),
            people=PersonStore(tmp_path / "people"),
        )
    )

    response = TestClient(app).post(f"/v1/drive-ingestions/{job['id']}/resume")

    assert response.status_code == 409
    after = store.get_job(job["id"])
    assert after["status"] == "paused"
    assert after["work_revision"] == before["work_revision"]
    assert after["progress"]["remaining"] == 1
    restarted = DriveIngestionStore(state).get_job(job["id"])
    assert restarted["status"] == "paused"


def test_later_chat_queries_index_without_rereading_drive(tmp_path):
    source = {
        "id": "brief",
        "name": "Brief.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-08-27T10:00:00Z",
        "parents": ["root"],
        "webViewLink": "https://drive.example/brief",
    }

    class Drive:
        def __init__(self):
            self.read_calls = []

        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [source], "nextPageToken": None}

        def read(self, file_id, max_chars=20000):
            self.read_calls.append(file_id)
            return {
                **source,
                "content": "Fall research dinner evidence.",
                "status": "read",
                "sources": [],
                "sensitive_content_redacted": False,
                "redaction_count": 0,
            }

    drive = Drive()
    fake = FakeProvider(steps=[])
    app = create_app(token=TOKEN, state=tmp_path, provider=fake)
    job = app.state.drive_ingestions.create_job(
        folder_id="root", resolved_path="Sourcing/Fall 2026"
    )
    DriveIngestionRunner(app.state.drive_ingestions, drive).run(job["id"])
    drive.read_calls.clear()
    fake.steps.extend(
        [
            {
                "tool_calls": [
                    ToolCall(
                        id="query-index",
                        name="drive_index_query",
                        arguments={"job_id": job["id"], "query": "research dinner"},
                    )
                ]
            },
            {"deltas": ("The indexed brief covers the research dinner.",)},
        ]
    )
    sid = app.state.store.open_session_id()

    with TestClient(app).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "What is in the folder index?", "session_id": sid})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in {"turn_end", "error"}:
                break

    finished = next(event for event in events if event["type"] == "tool_finished")
    assert finished["name"] == "drive_index_query"
    assert finished["ok"] is True
    assert finished["result"]["matches"][0]["drive_id"] == "brief"
    assert drive.read_calls == []
    assert "Fall research dinner evidence." in json.dumps(fake.calls)


def test_failed_index_query_retry_uses_ingestion_store_after_job_completes(tmp_path):
    fake = FakeProvider(steps=[])
    app = create_app(token=TOKEN, state=tmp_path, provider=fake)
    job = app.state.drive_ingestions.create_job(
        folder_id="root", resolved_path="Sourcing/Fall 2026"
    )
    fake.steps.extend(
        [
            {
                "tool_calls": [
                    ToolCall(
                        id="index-query-retry",
                        name="drive_index_query",
                        arguments={"job_id": job["id"], "query": "research dinner"},
                    )
                ]
            },
            {"deltas": ("The index is still running.",)},
        ]
    )
    sid = app.state.store.open_session_id()

    class EmptyDrive:
        def list_folder(self, folder_id, max_results=1000, page_token=None):
            return {"files": [], "nextPageToken": None}

    with TestClient(app).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "query the index", "session_id": sid})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in {"turn_end", "error"}:
                break
        failed = next(event for event in events if event["type"] == "tool_finished")
        assert failed["ok"] is False
        DriveIngestionRunner(app.state.drive_ingestions, EmptyDrive()).run(job["id"])
        ws.send_json(
            {
                "type": "retry_failed_step",
                "session_id": sid,
                "run_id": events[0]["run_id"],
                "call_id": "index-query-retry",
                "command_id": "retry-index-after-complete",
            }
        )
        recovery_events = []
        while True:
            event = ws.receive_json()
            recovery_events.append(event)
            if event["type"] == "tool_recovery":
                break

    recovery = recovery_events[-1]
    assert recovery["status"] == "succeeded"
    retried = next(
        event for event in recovery_events if event["type"] == "tool_finished"
    )
    assert retried["ok"] is True
    assert retried["result"]["matches"] == []
