"""S6: a redacted diagnostic bundle for one failed run.

The bundle exists to be handed to someone else, so the tests that matter are
the ones that prove nothing rides out in it. Every leak test plants a canary,
asserts the evidence category that carries the canary is actually populated,
and only then asserts the canary is gone. A leak test that passes because its
category produced no output proves nothing and keeps passing after redaction
breaks.

No credential-shaped literal is written in this file. Every canary is assembled
at runtime from fragments.
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coworker import bundle_redaction, diagnostic_bundle
from coworker.agent_run_repository import AgentRunRepository
from coworker.diagnostic_bundle import (
    BundleScanFailed,
    BundleSources,
    BundleSubject,
    EVIDENCE_CATEGORIES,
    archive_bytes,
    build_document,
    build_preview,
    export_bundle,
    inspect_archive,
)
from coworker.run_evidence import Evidence
from coworker.run_ledger import RunLedger
from coworker.store import ConversationStore
from tests.state_fixtures import build_current_state

# --- canaries, assembled at runtime --------------------------------------

MARK = "BUNDLELEAKCANARY"
API_KEY = "s" + "k-" + "live-" + MARK + "0123456789abcdefGHIJ"
ACCESS_TOKEN = "ya" + "29." + MARK + "GoogleOauthAccessToken0123"
REFRESH_TOKEN = "1/" + "/0g" + MARK + "RefreshTokenValue01234"
FORGE_TOKEN = "gh" + "p_" + MARK + "0123456789abcdefGHIJ"
AWS_KEY = "AK" + "IA" + "BUNDLECANARY0123"
WEB_TOKEN = (
    "ey" + "JhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiJCVU5ETEVDQU5BUlkifQ." + MARK + "sig"
)
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    + MARK
    + "keymaterial\n-----END RSA PRIVATE KEY-----"
)
AUTH_HEADER = "Authorization: " + "Bear" + "er " + MARK + "headervalue0123456789"
ASSIGNMENT = "api" + "_key=" + MARK + "assignedvalue"

MESSAGE_BODY = (
    "Hi Dana, the Codeology offer is 180k base and my cell is 555-0147. "
    + MARK
    + "body"
)
REASONING = "private reasoning: Dana looked lukewarm on the last thread " + MARK + "why"
TOOL_ARGUMENTS = {"query": "layoffs at Rippling " + MARK + "args", "limit": 5}
TOOL_RESULT = {"text": "row one\nrow two " + MARK + "result"}
COMMAND_OUTPUT = "$ ls -la\ntotal 48\n" + MARK + "stdout"

HOME = Path("/Users/leakcanary")
HOME_PATH = "/Users/leakcanary/Documents/Q3-layoff-plan.pdf"

EPOCH = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
APPLICATION = {"name": "Sourcecado", "version": "0.0.2", "slice": 29}


# --- harness -------------------------------------------------------------


class Harness:
    """One failed run, one state directory, and everything the bundle reads."""

    def __init__(self, tmp_path: Path, *, plant: bool = False) -> None:
        self.root = build_current_state(tmp_path / "state", plant_secrets=plant)
        self.store = ConversationStore(self.root)
        self.repo = AgentRunRepository(self.root)
        self.owner = self.repo.registry.register()
        self.ledger = RunLedger(self.repo, approvals=self.store)
        self.plant = plant
        self.run_id = self._failed_run()
        self.connectors = self._connectors()
        self._events()

    def _failed_run(self) -> str:
        started = self.repo.create_run(
            session_id="main",
            trigger="chat",
            goal="Find three Codeology leads at Rippling",
            owner=self.owner,
            person_id="per_" + "0" * 32,
        )
        commit = self.repo.checkpoint(
            started.lease,
            kind="model_pending",
            state="running",
            payload={
                "attempt_id": "attempt_1",
                "provider": "openai",
                "model_id": "gpt-5",
                "persona_id": "sourcing",
                "prompt_version": "sourcing-v1",
            },
        )
        commit = self.repo.checkpoint(
            commit.lease,
            kind="model_completed",
            state="running",
            payload={
                "attempt_id": "attempt_1",
                "status": "ok",
                "duration_ms": 1840,
            },
            usage={"input_tokens": 4120, "output_tokens": 310},
        )
        commit = self.repo.checkpoint(
            commit.lease,
            kind="tool_pending",
            state="running",
            payload={
                "tool_call_id": "call_gmail_send_1",
                "tool_name": "gmail_send",
                "approval_id": "call_gmail_send_1",
                # A caller handing the write path a whole argument map. The
                # checkpoint allowlist drops it; the bundle must not resurrect it.
                **({"arguments": TOOL_ARGUMENTS} if self.plant else {}),
            },
            approval_ids=["call_gmail_send_1"],
        )
        run_id = str(started.run["run_id"])
        commit = self.repo.checkpoint(
            commit.lease,
            kind="tool_completed",
            state="running",
            payload={
                "tool_call_id": "call_gmail_send_1",
                "tool_name": "gmail_send",
                "status": "failed",
                "error_class": "GmailSendError",
                "duration_ms": 620,
                **({"result": TOOL_RESULT} if self.plant else {}),
            },
        )
        commit = self.repo.checkpoint(
            commit.lease,
            kind="tool_outcome_unknown",
            state="interrupted",
            payload={
                "tool_call_id": "call_gmail_send_1",
                "reason": "process exited before the tool reported",
            },
        )
        summary = (
            f"send refused while reading {HOME_PATH} for {MESSAGE_BODY}"
            if self.plant
            else "send refused by the provider"
        )
        self.repo.checkpoint(
            commit.lease or self.repo.acquire_lease(run_id, self.owner, 300),
            kind="terminal",
            state="failed",
            payload={"error_class": "GmailSendError", "error_summary": summary},
            terminal_result={
                "status": "failed",
                "error_class": "GmailSendError",
                "error": summary,
                "text": MESSAGE_BODY if self.plant else "no answer",
            },
        )
        self.store.resolve_inbox(
            "call_gmail_send_1",
            "allow",
            actor=(MARK + "operator@example.com") if self.plant else "operator",
        )
        return run_id

    def _connectors(self) -> list[dict]:
        """The shape `GET /v1/connectors` returns, with a token planted in it."""
        return [
            {
                "id": "gmail",
                "title": "Gmail",
                "description": "Search email and create review-only drafts.",
                "status": "connected",
                "catalog_group": "connected",
                "email": (MARK + "@example.com") if self.plant else "op@example.com",
                "required_scopes": ["Read Gmail messages"],
                "missing_scopes": [],
                "health": {
                    "category": "healthy",
                    "label": "Ready",
                    "message": (
                        f"refresh_token={REFRESH_TOKEN}"
                        if self.plant
                        else "This connection is ready to use."
                    ),
                },
                "recovery": None,
                "supported_actions": ["Search and read email"],
                "available_actions": ["disconnect"],
                "repair_route": "#/connections/gmail",
                "authorization_group": "google",
            },
            {
                "id": "apollo",
                "title": "Apollo",
                "description": "Search people and enrich sourcing records.",
                "status": "available",
                "catalog_group": "available",
                "email": None,
                "required_scopes": [],
                "missing_scopes": [],
                "health": {
                    "category": "setup_required",
                    "label": "Available",
                    "message": "This connection has not been set up yet.",
                },
                "recovery": {
                    "category": "configure",
                    "action_label": "View setup guide",
                    "message": (
                        f"Set APOLLO_API_KEY={API_KEY}"
                        if self.plant
                        else "Set APOLLO_API_KEY in the local environment."
                    ),
                },
                "supported_actions": ["Search people"],
                "available_actions": ["view_guidance"],
                "repair_route": "#/connections/apollo",
                "authorization_group": None,
            },
        ]

    def _events(self) -> None:
        base = {
            "version": 2,
            "session_id": "main",
            "run_id": "run_b1",
            "message_id": "message_b1",
            "part_id": "part_b1",
        }
        rows = [
            {**base, "type": "turn_start", "event_id": "event_b1", "state": "running"},
            {
                **base,
                "type": "assistant_delta",
                "event_id": "event_b2",
                "delta": MESSAGE_BODY if self.plant else "Searching Apollo.",
            },
            {
                **base,
                "type": "tool_started",
                "event_id": "event_b3",
                "id": "call_gmail_send_1",
                "name": "gmail_send",
                "arguments": TOOL_ARGUMENTS if self.plant else {"query": "rippling"},
            },
            {
                **base,
                "type": "tool_finished",
                "event_id": "event_b4",
                "id": "call_gmail_send_1",
                "name": "gmail_send",
                "ok": False,
                "result": TOOL_RESULT if self.plant else {"text": "ok"},
            },
            {
                **base,
                "type": "provider_recovery",
                "event_id": "event_b5",
                "action": "retry",
                "provider": "openai",
                "model": "gpt-5",
                "reason": "RateLimited",
                "attempt": 2,
                "delay_ms": 500,
                "message": AUTH_HEADER if self.plant else "retrying shortly",
            },
            {
                **base,
                "type": "error",
                "event_id": "event_b6",
                "state": "failed",
                "message": (
                    f"Traceback (most recent call last):\n"
                    f'  File "{HOME_PATH}", line 12\n'
                    f"{PRIVATE_KEY}\n{COMMAND_OUTPUT}\n{REASONING}"
                    if self.plant
                    else "the provider refused the request"
                ),
            },
        ]
        for row in rows:
            self.store.append_event("main", row)

    def sources(self, **overrides) -> BundleSources:
        doctor_report = _doctor_report(self.root)
        registered = bundle_redaction.registered_secret_values(
            json.loads((self.root / "secrets.json").read_text(encoding="utf-8"))
        )
        defaults = dict(
            application=APPLICATION,
            receipt=self.ledger.receipt(self.run_id),
            recent_runs=self.ledger.query(limit=diagnostic_bundle.HEALTH_WINDOW_RUNS),
            doctor=doctor_report,
            connectors=self.connectors,
            events=self.store.load_events("main"),
            registered_secrets=registered,
            state_root=self.root,
            home=HOME,
        )
        defaults.update(overrides)
        return BundleSources(**defaults)

    def subject(self) -> BundleSubject:
        return BundleSubject(kind="run", run_id=self.run_id)


def _doctor_report(root: Path) -> dict:
    from coworker import doctor

    return doctor.diagnose(root).to_dict()


def _document_text(document: dict) -> str:
    return json.dumps(document, sort_keys=True, ensure_ascii=False)


def _archive_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8", errors="replace")
            for name in archive.namelist()
        )


# --- criterion 1 and 2: what the bundle carries ---------------------------


def test_a_bundle_from_a_failed_run_carries_every_declared_evidence_category(tmp_path):
    harness = Harness(tmp_path)
    document = build_document(harness.subject(), harness.sources())

    assert set(document) == set(diagnostic_bundle.MEMBERS)

    subject = document["subject.json"]
    assert subject["subject"] == {"kind": "run", "run_id": harness.run_id}
    declared = {item["id"] for item in subject["evidence_categories"]}
    assert declared == {item["id"] for item in EVIDENCE_CATEGORIES}
    for item in subject["evidence_categories"]:
        assert item["description"].strip()
    assert subject["excluded"]

    environment = document["environment.json"]
    assert environment["application"] == APPLICATION
    assert environment["python"]["version"]
    assert environment["platform"]["system"]
    assert "node" not in environment["platform"]

    run = document["run.json"]
    assert run["run"]["run_id"] == harness.run_id
    assert run["outcome"]["state"] == "failed"
    assert run["outcome"]["result"]["error_class"] == "GmailSendError"
    assert run["model_attempts"]["attempts"][0]["duration_ms"] == 1840
    assert run["tools"]["calls"][0]["duration_ms"] == 620
    assert run["usage"]["totals"]["input_tokens"] == 4120
    assert run["approvals"]["decisions"][0]["decision"] == "allow"
    assert run["recovery"]["events"]
    assert run["record"]["checkpoints_stored"] == 7

    health = document["health.json"]
    assert health["runs_considered"] >= 1
    assert health["states"]["failed"] >= 1
    assert health["recent"][0]["run_id"] == harness.run_id

    state = document["state.json"]
    assert state["stores"]
    assert {item["store_id"] for item in state["stores"]} >= {"conversation_db"}
    assert state["migration"]["target_versions"]["conversation_db"] >= 1

    connectors = document["connectors.json"]
    assert {item["id"] for item in connectors["connectors"]} == {"gmail", "apollo"}

    logs = document["logs.json"]
    assert logs["records"]
    assert logs["counts"]["error"] == 1
    assert {record["type"] for record in logs["records"]} >= {"error", "tool_finished"}


def test_a_bundle_can_start_from_a_doctor_finding_without_a_run(tmp_path):
    harness = Harness(tmp_path)
    subject = BundleSubject(kind="doctor_finding", check="permissions", store_id="state_root")
    document = build_document(subject, harness.sources(receipt=None))

    assert document["subject.json"]["subject"] == {
        "kind": "doctor_finding",
        "check": "permissions",
        "store_id": "state_root",
    }
    included = {
        item["id"] for item in document["subject.json"]["evidence_categories"] if item["included"]
    }
    assert "run" not in included
    assert "state" in included
    assert document["run.json"]["run"] is None
    assert document["state.json"]["stores"]


def _clean_run(harness: Harness) -> str:
    """A run that ended with nothing to report. Its silence is an absence."""
    started = harness.repo.create_run(
        session_id="main",
        trigger="chat",
        goal="Confirm the Rippling shortlist",
        owner=harness.owner,
    )
    harness.repo.checkpoint(started.lease, kind="terminal", state="complete")
    return str(started.run["run_id"])


def _evidence_values(payload, found=None):
    found = set() if found is None else found
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "evidence" and isinstance(value, str):
                found.add(value)
            else:
                _evidence_values(value, found)
    elif isinstance(payload, list):
        for item in payload:
            _evidence_values(item, found)
    return found


def test_the_bundle_reuses_the_merged_evidence_vocabulary(tmp_path):
    """One vocabulary for "we do not know", not a parallel one.

    The bundle carries the ledger's own `evidence` value through unchanged.
    This fails if either side coins a word the other does not have.
    """
    harness = Harness(tmp_path)
    document = build_document(harness.subject(), harness.sources())

    values = _evidence_values(document["run.json"])
    assert values, "no section carried evidence, so this test proves nothing"
    assert values <= {member.value for member in Evidence}


def test_absent_and_missing_stay_different_through_the_projection(tmp_path):
    """A hole in the record must not read like a positive absence.

    The failed run's record carries `tool_outcome_unknown`, so nothing about it
    is settled. The clean run's record covers its whole life. The same section
    has to render differently for the two, or the bundle has flattened the one
    distinction the evidence vocabulary exists for.
    """
    harness = Harness(tmp_path)
    clean_id = _clean_run(harness)

    failed = build_document(harness.subject(), harness.sources())["run.json"]
    clean = build_document(
        BundleSubject(kind="run", run_id=clean_id),
        harness.sources(receipt=harness.ledger.receipt(clean_id)),
    )["run.json"]

    assert failed["record"]["complete"] is False
    assert clean["record"]["complete"] is True

    # Nothing was recorded about sources in either run. Only one of them can
    # honestly say that means none were touched.
    assert failed["sources"]["count"] == clean["sources"]["count"] == 0
    assert failed["sources"]["evidence"] == Evidence.MISSING.value
    assert clean["sources"]["evidence"] == Evidence.ABSENT.value

    assert failed["tools"]["evidence"] == Evidence.AMBIGUOUS.value
    assert clean["tools"]["evidence"] == Evidence.ABSENT.value


# --- criterion 8: the planted-secret matrix -------------------------------


def test_every_planted_category_is_carried_by_the_bundle_before_any_leak_check(tmp_path):
    """Non-vacuity guard. Each leak category must produce real evidence."""
    harness = Harness(tmp_path, plant=True)
    document = build_document(harness.subject(), harness.sources())

    # logs
    assert len(document["logs.json"]["records"]) >= 6
    # errors
    assert document["run.json"]["outcome"]["result"]["error_class"] == "GmailSendError"
    assert document["run.json"]["rationale"]["notes"]
    # connector payloads
    assert len(document["connectors.json"]["connectors"]) == 2
    # paths
    assert MARK not in _document_text(document)
    assert "<home>" in _document_text(document) or "<state>" in _document_text(document)
    # approval data
    assert document["run.json"]["approvals"]["decisions"][0]["decision"] == "allow"
    # state integrity, which is where the fixture's own canaries live
    assert document["state.json"]["findings"]


@pytest.mark.parametrize(
    "member, probe",
    [
        ("logs.json", lambda doc: len(doc["logs.json"]["records"]) >= 6),
        ("run.json", lambda doc: bool(doc["run.json"]["rationale"]["notes"])),
        ("connectors.json", lambda doc: len(doc["connectors.json"]["connectors"]) == 2),
        ("state.json", lambda doc: bool(doc["state.json"]["findings"])),
        (
            "health.json",
            lambda doc: doc["health.json"]["states"].get("failed", 0) >= 1,
        ),
    ],
)
def test_no_planted_secret_survives_into_any_member(tmp_path, member, probe):
    harness = Harness(tmp_path, plant=True)
    document = build_document(harness.subject(), harness.sources())

    assert probe(document), f"{member} carried no evidence, so this test proves nothing"

    text = json.dumps(document[member], sort_keys=True, ensure_ascii=False)
    for canary in (
        API_KEY,
        ACCESS_TOKEN,
        REFRESH_TOKEN,
        FORGE_TOKEN,
        AWS_KEY,
        WEB_TOKEN,
        PRIVATE_KEY,
        MESSAGE_BODY,
        REASONING,
        COMMAND_OUTPUT,
        json.dumps(TOOL_ARGUMENTS),
        json.dumps(TOOL_RESULT),
        MARK,
    ):
        assert canary not in text, f"{member} carried a planted secret"


def test_a_home_path_in_carried_text_becomes_the_home_placeholder(tmp_path):
    """Proves the path rewrite is load-bearing, not incidental.

    A connector label is text the bundle does carry, so a home path planted
    there reaches the projection and has to be rewritten on the way through.
    """
    harness = Harness(tmp_path, plant=True)
    connectors = harness.connectors
    connectors[0]["title"] = f"Gmail {HOME_PATH}"
    document = build_document(harness.subject(), harness.sources(connectors=connectors))

    carried = document["connectors.json"]["connectors"]
    assert len(carried) == 2, "no connector was carried, so this test proves nothing"
    assert carried[0]["title"] == "Gmail <home>"
    assert "/Users/" not in _document_text(document)
    assert "leakcanary" not in _document_text(document)


def test_a_raw_home_path_never_survives(tmp_path):
    harness = Harness(tmp_path, plant=True)
    document = build_document(harness.subject(), harness.sources())
    text = _document_text(document)

    notes = document["run.json"]["rationale"]["notes"]
    assert notes and any(note["error_summary_length"] for note in notes), (
        "no note carried the planted path, so this test proves nothing"
    )
    assert "/Users/" not in text
    assert "leakcanary" not in text
    assert "Q3-layoff-plan" not in text


def test_prompts_and_message_bodies_have_no_field_to_land_in(tmp_path):
    harness = Harness(tmp_path, plant=True)
    document = build_document(harness.subject(), harness.sources())
    run = document["run.json"]

    assert run["prompt"]["persona_id"] == "sourcing"
    assert set(run["prompt"]) == {"evidence", "persona_id", "prompt_version"}
    assert "text" not in run["outcome"]["result"]
    assert "error" not in run["outcome"]["result"]
    assert run["outcome"]["result"]["text_length"] > 0
    assert all("title" not in ref for ref in run["sources"]["providers"])
    for record in document["logs.json"]["records"]:
        assert set(record) <= diagnostic_bundle.LOG_RECORD_FIELDS


# --- criterion 4: fail closed --------------------------------------------


def test_a_registered_secret_reaching_the_bundle_fails_the_export_closed(tmp_path):
    harness = Harness(tmp_path)
    secret = "notarealsecret-" + MARK + "-registered"
    connectors = harness.connectors
    connectors[0]["title"] = f"Gmail {secret}"

    with pytest.raises(BundleScanFailed) as raised:
        export_bundle(
            harness.subject(),
            harness.sources(connectors=connectors, registered_secrets=frozenset({secret})),
            destination_dir=tmp_path / "out",
        )

    assert [match.category for match in raised.value.matches] == ["registered_secret"]
    assert not list((tmp_path / "out").glob("*"))


def test_a_credential_pattern_reaching_the_bundle_fails_the_export_closed(tmp_path):
    harness = Harness(tmp_path)
    connectors = harness.connectors
    connectors[0]["title"] = f"Gmail {FORGE_TOKEN}"

    with pytest.raises(BundleScanFailed) as raised:
        export_bundle(
            harness.subject(),
            harness.sources(connectors=connectors),
            destination_dir=tmp_path / "out",
        )

    assert "issued_credential" in {match.category for match in raised.value.matches}


def test_a_scan_failure_names_a_category_and_a_location_but_never_the_value(tmp_path):
    harness = Harness(tmp_path)
    connectors = harness.connectors
    connectors[0]["title"] = f"Gmail {API_KEY}"

    with pytest.raises(BundleScanFailed) as raised:
        export_bundle(
            harness.subject(),
            harness.sources(connectors=connectors),
            destination_dir=tmp_path / "out",
        )

    rendered = str(raised.value) + repr(raised.value.matches)
    assert "connectors" in rendered
    assert "issued_credential" in rendered
    assert MARK not in rendered
    assert API_KEY not in rendered


def test_the_scan_still_catches_a_secret_when_the_upstream_layers_are_disabled(
    tmp_path, monkeypatch
):
    """Layer three must not lean on layers one and two.

    Both upstream redactors become identity functions here. The receipt
    allowlist and Doctor's filter stop doing anything, and the export must
    still refuse.
    """
    from coworker import doctor, run_receipt

    monkeypatch.setattr(run_receipt, "redact_secrets", lambda value: value)
    monkeypatch.setattr(doctor, "redact", lambda text, root: str(text))

    harness = Harness(tmp_path)
    connectors = harness.connectors
    connectors[0]["title"] = f"Gmail {ACCESS_TOKEN}"

    with pytest.raises(BundleScanFailed):
        export_bundle(
            harness.subject(),
            harness.sources(connectors=connectors),
            destination_dir=tmp_path / "out",
        )


def test_the_third_layer_imports_neither_of_the_first_two(tmp_path):
    source = Path(bundle_redaction.__file__).read_text(encoding="utf-8")
    assert "doctor" not in source
    assert "agent_runs" not in source
    assert "run_receipt" not in source


# --- criterion 5: preview, and never an upload ----------------------------


def test_the_preview_is_bounded_and_explains_what_is_included(tmp_path):
    harness = Harness(tmp_path)
    document = build_document(harness.subject(), harness.sources())
    preview = build_preview(document)

    assert preview["subject"]["kind"] == "run"
    assert len(preview["evidence_categories"]) == len(EVIDENCE_CATEGORIES)
    assert preview["excluded"]
    assert len(preview["findings"]) <= diagnostic_bundle.PREVIEW_FINDINGS
    assert len(preview["log_records"]) <= diagnostic_bundle.PREVIEW_LOG_RECORDS
    assert preview["counts"]["log_records"] == len(document["logs.json"]["records"])
    assert len(json.dumps(preview)) <= diagnostic_bundle.MAX_PREVIEW_CHARS


def test_the_bundle_module_opens_no_network_client():
    source = Path(diagnostic_bundle.__file__).read_text(encoding="utf-8")
    for forbidden in ("httpx", "requests", "urllib.request", "socket", "smtplib"):
        assert forbidden not in source


# --- criterion 6: determinism --------------------------------------------


def test_two_exports_from_identical_state_differ_only_in_package_identity(tmp_path):
    harness = Harness(tmp_path)
    destination = tmp_path / "out"

    first = export_bundle(harness.subject(), harness.sources(), destination_dir=destination)
    second = export_bundle(harness.subject(), harness.sources(), destination_dir=destination)

    assert first["bundle_id"] != second["bundle_id"]

    with zipfile.ZipFile(first["path"]) as one, zipfile.ZipFile(second["path"]) as two:
        assert one.namelist() == two.namelist()
        for info in [*one.infolist(), *two.infolist()]:
            # A real member timestamp would make one state produce two archives.
            assert info.date_time == diagnostic_bundle.ZIP_EPOCH
            assert info.external_attr >> 16 == diagnostic_bundle.MEMBER_MODE
        for name in one.namelist():
            if name == diagnostic_bundle.MANIFEST_NAME:
                left = json.loads(one.read(name))
                right = json.loads(two.read(name))
                del left["package"], right["package"]
                assert left == right
                continue
            assert one.read(name) == two.read(name), f"{name} was not deterministic"


def test_the_document_builder_has_no_clock_and_no_randomness(tmp_path):
    harness = Harness(tmp_path)
    first = build_document(harness.subject(), harness.sources())
    second = build_document(harness.subject(), harness.sources())
    assert first == second


# --- criterion 7: no partially readable artifact --------------------------


def _leftovers(destination: Path) -> list[Path]:
    return [path for path in destination.rglob("*") if path.is_file()]


def test_a_failed_scan_leaves_no_file_and_no_temporary(tmp_path):
    harness = Harness(tmp_path)
    destination = tmp_path / "out"
    connectors = harness.connectors
    connectors[0]["title"] = f"Gmail {API_KEY}"

    with pytest.raises(BundleScanFailed):
        export_bundle(
            harness.subject(),
            harness.sources(connectors=connectors),
            destination_dir=destination,
        )

    assert _leftovers(destination) == []


def test_a_crash_while_assembling_leaves_no_file_and_no_temporary(tmp_path, monkeypatch):
    harness = Harness(tmp_path)
    destination = tmp_path / "out"
    destination.mkdir()

    def boom(*args, **kwargs):
        raise RuntimeError("assembly failed")

    monkeypatch.setattr(diagnostic_bundle, "build_document", boom)
    with pytest.raises(RuntimeError):
        export_bundle(harness.subject(), harness.sources(), destination_dir=destination)

    assert _leftovers(destination) == []


def test_a_crash_after_the_temporary_archive_is_written_leaves_nothing_readable(
    tmp_path, monkeypatch
):
    """The riskiest window: full bytes on disk, one rename from being a bundle."""
    harness = Harness(tmp_path)
    destination = tmp_path / "out"
    destination.mkdir()
    seen: list[Path] = []

    real_replace = diagnostic_bundle.os.replace

    def boom(source, target):
        seen.append(Path(source))
        assert Path(source).is_file() and Path(source).stat().st_size > 0
        raise OSError("rename failed")

    monkeypatch.setattr(diagnostic_bundle.os, "replace", boom)
    with pytest.raises(OSError):
        export_bundle(harness.subject(), harness.sources(), destination_dir=destination)

    assert seen, "the export never reached the rename"
    assert not seen[0].exists()
    assert _leftovers(destination) == []
    assert real_replace is not boom


def test_a_successful_export_writes_one_owner_only_file(tmp_path):
    harness = Harness(tmp_path)
    destination = tmp_path / "out"
    result = export_bundle(harness.subject(), harness.sources(), destination_dir=destination)

    written = Path(result["path"])
    assert written.is_file()
    assert _leftovers(destination) == [written]
    assert written.stat().st_mode & 0o077 == 0
    assert result["sha256"] == diagnostic_bundle.sha256_bytes(written.read_bytes())


# --- criterion 9: offline inspection --------------------------------------


def test_the_inspection_command_verifies_the_manifest_and_the_checksums(tmp_path):
    harness = Harness(tmp_path)
    result = export_bundle(
        harness.subject(), harness.sources(), destination_dir=tmp_path / "out"
    )

    report = inspect_archive(Path(result["path"]))
    assert report["ok"] is True
    assert report["problems"] == []
    assert report["package"]["bundle_id"] == result["bundle_id"]
    assert {entry["name"] for entry in report["entries"]} == set(
        diagnostic_bundle.MEMBERS
    ) | {diagnostic_bundle.CHECKSUMS_NAME}
    assert all(entry["ok"] for entry in report["entries"])

    assert diagnostic_bundle.main(["inspect", result["path"]]) == 0


def test_the_inspection_command_reports_a_tampered_member(tmp_path):
    harness = Harness(tmp_path)
    result = export_bundle(
        harness.subject(), harness.sources(), destination_dir=tmp_path / "out"
    )
    original = Path(result["path"])
    tampered = tmp_path / "tampered.zip"

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "connectors.json":
                payload = payload.replace(b"gmail", b"gmai1")
            target.writestr(info, payload)

    report = inspect_archive(tampered)
    assert report["ok"] is False
    assert any("connectors.json" in problem for problem in report["problems"])
    assert diagnostic_bundle.main(["inspect", str(tampered)]) == 1


def test_the_inspection_command_reports_a_tampered_checksum_list(tmp_path):
    """`checksums.txt` cannot vouch for itself. The manifest is what covers it."""
    harness = Harness(tmp_path)
    result = export_bundle(
        harness.subject(), harness.sources(), destination_dir=tmp_path / "out"
    )
    tampered = tmp_path / "tampered.zip"

    with zipfile.ZipFile(result["path"]) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == diagnostic_bundle.CHECKSUMS_NAME:
                payload = payload.replace(b"connectors.json", b"connectors.json ")
            target.writestr(info, payload)

    report = inspect_archive(tampered)
    assert report["ok"] is False
    assert any(
        entry["name"] == diagnostic_bundle.CHECKSUMS_NAME and not entry["ok"]
        for entry in report["entries"]
    )


def test_the_checksums_file_verifies_with_plain_tools(tmp_path):
    harness = Harness(tmp_path)
    result = export_bundle(
        harness.subject(), harness.sources(), destination_dir=tmp_path / "out"
    )
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(result["path"]) as archive:
        archive.extractall(extracted)

    lines = (extracted / diagnostic_bundle.CHECKSUMS_NAME).read_text(encoding="utf-8")
    named = [line.split("  ", 1)[1] for line in lines.strip().splitlines()]
    assert set(named) == set(diagnostic_bundle.MEMBERS)
    for line in lines.strip().splitlines():
        digest, name = line.split("  ", 1)
        assert digest == diagnostic_bundle.sha256_bytes((extracted / name).read_bytes())


# --- the redaction layer on its own ---------------------------------------


def test_relativize_keeps_state_paths_and_erases_home_paths():
    state_root = HOME / ".config" / "club"
    relativize = bundle_redaction.relativize

    assert (
        relativize(f"{state_root}/club.db", home=HOME, state_root=state_root)
        == "<state>/club.db"
    )
    assert relativize(HOME_PATH, home=HOME, state_root=state_root) == "<home>"
    assert (
        relativize("/Users/someone-else/Desktop/a.txt", home=HOME, state_root=state_root)
        == "<home>"
    )
    assert (
        relativize("/home/dana/notes.md", home=HOME, state_root=state_root) == "<home>"
    )
    assert (
        relativize("https://example.com/home/dana", home=HOME, state_root=state_root)
        == "https://example.com/home/dana"
    )
    assert relativize("run_0f3c", home=HOME, state_root=state_root) == "run_0f3c"


def test_registered_secret_values_take_credentials_and_leave_urls_and_ids():
    values = bundle_redaction.registered_secret_values(
        {
            "google": {
                "refresh_token": REFRESH_TOKEN,
                "email": "operator@example.com",
                "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
                "token_uri": "https://oauth2.googleapis.com/token",
            },
            "mcp-oauth:granola": {"access_token": ACCESS_TOKEN, "provider": "granola"},
        }
    )
    assert REFRESH_TOKEN in values
    assert ACCESS_TOKEN in values
    assert "operator@example.com" not in values
    assert "https://oauth2.googleapis.com/token" not in values
    assert "granola" not in values


@pytest.mark.parametrize(
    "text, category",
    [
        (API_KEY, "issued_credential"),
        (ACCESS_TOKEN, "issued_credential"),
        (FORGE_TOKEN, "issued_credential"),
        (AWS_KEY, "issued_credential"),
        (WEB_TOKEN, "json_web_token"),
        (PRIVATE_KEY, "private_key"),
        (AUTH_HEADER, "authorization_header"),
        (ASSIGNMENT, "credential_assignment"),
        (HOME_PATH, "home_path"),
    ],
)
def test_the_scan_names_every_credential_shape(text, category):
    matches = bundle_redaction.scan(
        {"field": text},
        registered=frozenset(),
        home=HOME,
        state_root=HOME / ".config" / "club",
    )
    assert category in {match.category for match in matches}
    assert {match.category for match in matches} <= bundle_redaction.SCAN_CATEGORIES
    assert all(MARK not in repr(match) for match in matches)


@pytest.mark.parametrize(
    "text",
    [
        "run_0f3c9a1b2c3d4e5f60718293a4b5c6d7",
        "per_0f3c9a1b2c3d4e5f60718293a4b5c6d7",
        "2026-08-27T16:45:00.123456+00:00",
        "gpt-5",
        "claude-opus-4-1-20250805",
        "<state>/club.db",
        "weekly_sourcing_review",
        "a" * 64,
        # A refusal on prose would refuse every export, not the leaking one.
        "Basic authentication is required by this connector",
        "context_window_tokens: 200000",
        "input_tokens=4120",
        "<state>/secrets.json is group readable",
        "https://www.googleapis.com/auth/gmail.readonly",
    ],
)
def test_the_scan_leaves_sourcecado_identifiers_alone(text):
    assert (
        bundle_redaction.scan(
            {"field": text},
            registered=frozenset(),
            home=HOME,
            state_root=HOME / ".config" / "club",
        )
        == ()
    )


def test_the_archive_is_byte_stable_for_one_document(tmp_path):
    harness = Harness(tmp_path)
    document = build_document(harness.subject(), harness.sources())
    one = archive_bytes(document, bundle_id="abc123", generated_at=EPOCH.isoformat())
    two = archive_bytes(document, bundle_id="abc123", generated_at=EPOCH.isoformat())
    assert one == two


# --- the HTTP surface -----------------------------------------------------


API_TOKEN = "diagnostic-bundle-token"


def _client(harness: Harness):
    from fastapi.testclient import TestClient

    from coworker.server import TOKEN_HEADER, create_app

    app = create_app(token=API_TOKEN, state=harness.root, provider=None)
    return TestClient(app), {TOKEN_HEADER: API_TOKEN}, app


def test_the_route_previews_before_anything_is_written_and_saves_only_when_asked(
    tmp_path,
):
    harness = Harness(tmp_path)
    client, headers, _ = _client(harness)
    diagnostics = harness.root / "diagnostics"

    preview = client.post(
        "/v1/diagnostics/bundle/preview",
        json={"run_id": harness.run_id},
        headers=headers,
    )
    assert preview.status_code == 200
    body = preview.json()["preview"]
    assert body["subject"] == {"kind": "run", "run_id": harness.run_id}
    assert len(body["evidence_categories"]) == len(EVIDENCE_CATEGORIES)
    assert body["excluded"]
    assert not diagnostics.exists(), "a preview must not write a bundle"

    saved = client.post(
        "/v1/diagnostics/bundle/export",
        json={"run_id": harness.run_id},
        headers=headers,
    )
    assert saved.status_code == 200
    written = saved.json()["bundle"]
    archive = Path(written["path"])
    assert archive.parent == diagnostics
    assert archive.is_file()
    assert inspect_archive(archive)["ok"] is True
    assert MARK not in _archive_text(archive)


def test_the_route_can_start_from_a_state_finding(tmp_path):
    harness = Harness(tmp_path)
    client, headers, _ = _client(harness)

    response = client.post(
        "/v1/diagnostics/bundle/preview",
        json={"check": "permissions", "store_id": "state_root"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["preview"]["subject"]["check"] == "permissions"
    assert response.json()["preview"]["run"] is None


def test_the_route_refuses_an_unknown_run_and_an_empty_start(tmp_path):
    harness = Harness(tmp_path)
    client, headers, _ = _client(harness)

    missing = client.post(
        "/v1/diagnostics/bundle/export", json={"run_id": "run-nope"}, headers=headers
    )
    assert missing.status_code == 404

    empty = client.post("/v1/diagnostics/bundle/export", json={}, headers=headers)
    assert empty.status_code == 400


def test_the_route_fails_closed_when_a_registered_secret_reaches_the_bundle(tmp_path):
    """End to end: a value the operator registered can never ride out.

    Registering the run's own id as a credential is artificial, but it is the
    only way to force a registered value into a bundle that is otherwise clean,
    and it proves the refusal reaches the caller rather than being swallowed.
    """
    harness = Harness(tmp_path)
    client, headers, app = _client(harness)
    app.state.secrets.put("canary", {"access_token": harness.run_id})

    refused = client.post(
        "/v1/diagnostics/bundle/export",
        json={"run_id": harness.run_id},
        headers=headers,
    )
    assert refused.status_code == 409
    body = refused.json()
    assert body["error"] == "scan_refused"
    assert {match["category"] for match in body["matches"]} == {"registered_secret"}
    assert all(harness.run_id not in match["location"] for match in body["matches"])
    assert not (harness.root / "diagnostics").exists()

    blocked = client.post(
        "/v1/diagnostics/bundle/preview",
        json={"run_id": harness.run_id},
        headers=headers,
    )
    assert blocked.status_code == 409
