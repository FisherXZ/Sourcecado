"""Regression proofs for source dispatch, proposal replay, and retry identity."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from coworker.crm_descriptions import DescriptionService
from coworker.people import PersonStore
from coworker.server import TOKEN_HEADER, create_app
from test_crm_analysis import DescriptionModel, ReadOnlyMail, tracked_person, worker


def source_for(people, pid, mid):
    with people._lock:
        return next(people._attachment_dict(row) for row in people._conn.execute(
            "SELECT * FROM person_attachments WHERE person_id=? AND record_type='source_ref'", (pid,))
            if json.loads(row["fields_json"]).get("message_id") == mid)


def restrict(people, source_id):
    with people._lock, people._conn:
        people._conn.execute("UPDATE person_attachments SET restricted=1 WHERE id=?", (source_id,))


@pytest.mark.parametrize("during_message", ["reply-1", "sent-1"])
def test_source_revoked_during_body_read_never_reaches_provider(tmp_path, during_message):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    class RevokingMail(ReadOnlyMail):
        def read(self, *, message_id):
            result = super().read(message_id=message_id)
            if message_id == during_message:
                restrict(people, source_for(people, pid, "reply-1")["id"])
            return result
    model = DescriptionModel()
    service = worker(people, model, RevokingMail())
    result = asyncio.run(service.analyze(pid))
    assert model.calls == []
    assert result == {"status": "partial", "failure_code": "private_source"}
    state = service.state(pid)
    assert state["attempts"] == 0
    assert people.descriptions(pid)["general"]["text"] is None


def test_grant_revoked_during_body_read_never_reaches_provider(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    ref = people.upsert_attachment(pid, record_type="source_ref", fields={"message_id": "reply-1", "sensitivity": "restricted"},
        idempotency_key="granted-private-mail", actor="director", rationale_summary="Attach a private source.")
    service = worker(people)
    service.allowed = {ref["id"]}
    class RevokingGrant(ReadOnlyMail):
        def read(self, *, message_id):
            result = super().read(message_id=message_id)
            service.allowed.clear()
            return result
    service.reader._client = RevokingGrant()
    result = asyncio.run(service.analyze(pid))
    assert service.provider.calls == []
    assert result["failure_code"] == "private_source"


def test_dispatch_rechecks_current_sources_and_does_not_charge_an_uncalled_model(tmp_path, monkeypatch):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    service = worker(people)
    claim = service._claim
    def claim_then_revoke(*args, **kwargs):
        result = claim(*args, **kwargs)
        if result[0]:
            restrict(people, source_for(people, pid, "reply-1")["id"])
        return result
    monkeypatch.setattr(service, "_claim", claim_then_revoke)
    result = asyncio.run(service.analyze(pid))
    assert service.provider.calls == []
    assert result == {"status": "partial", "failure_code": "private_source"}
    state = service.state(pid)
    assert state["attempts"] == 1  # one bounded analysis attempt, no model request
    assert state["usage"] is None and state["estimated_cost_usd"] is None
    assert state["budget"]["consumed"]["model_turns"] == 0
    assert state["budget"]["unmeasured_requests"] == 0
    assert state["budget"]["unpriced_requests"] == 0


def test_source_identity_changed_during_later_read_does_not_enter_prompt(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    class ChangedSource(ReadOnlyMail):
        def read(self, *, message_id):
            result = super().read(message_id=message_id)
            if message_id == "sent-1":
                ref = source_for(people, pid, "reply-1")
                fields = {**ref["fields"], "message_id": "other-message"}
                with people._lock, people._conn:
                    people._conn.execute("UPDATE person_attachments SET fields_json=? WHERE id=?", (json.dumps(fields), ref["id"]))
            return result
    model = DescriptionModel()
    result = asyncio.run(worker(people, model, ChangedSource()).analyze(pid))
    assert model.calls == []
    assert result == {"status": "partial", "failure_code": "stale_source"}


@pytest.mark.parametrize("action", ["accept", "dismiss"])
def test_authenticated_resolution_replays_after_response_loss_restart_and_changed_arguments_conflict(tmp_path, action):
    app = create_app(token="proposal-replay", state=tmp_path, provider=DescriptionModel(), gmail=ReadOnlyMail())
    pid = tracked_person(app.state.people)
    app.state.people.patch_description(pid, "general", expected_version=0, text="", actor="director", rationale_summary="Keep this blank.")
    service = worker(app.state.people)
    asyncio.run(service.analyze(pid))
    proposal = service.proposals(pid)[0]
    url = f"/v1/people/{pid}/proposals/{proposal['proposal_id']}/resolve"
    headers = {TOKEN_HEADER: "proposal-replay"}
    body = {"action": action, "expected_version": proposal["version"]}
    with TestClient(app) as client:
        first = client.post(url, headers=headers, json=body)
    assert first.status_code == 200
    saved = first.json()
    versions = app.state.people.descriptions(pid)
    changes = app.state.people._conn.execute("SELECT count(*) FROM crm_changes").fetchone()[0]
    restarted = create_app(token="proposal-replay", state=tmp_path, provider=DescriptionModel(), gmail=ReadOnlyMail())
    with TestClient(restarted) as client:
        replay = client.post(url, headers=headers, json=body)
        assert replay.status_code == 200
        assert replay.json() == saved
        assert client.post(url, headers=headers, json={**body, "action": "dismiss" if action == "accept" else "accept"}).status_code == 409
        assert client.post(url, headers=headers, json={**body, "expected_version": proposal["version"]+1}).status_code == 409
    assert restarted.state.people.descriptions(pid) == versions
    assert restarted.state.people._conn.execute("SELECT count(*) FROM crm_changes").fetchone()[0] == changes
    if action == "accept":
        DescriptionService(restarted.state.people).undo(pid, saved["change_id"], operation_id="undo-after-accept")
        undone = restarted.state.people.descriptions(pid)
        with TestClient(restarted) as client:
            replay = client.post(url, headers=headers, json=body)
        assert replay.status_code == 200 and replay.json() == saved
        assert restarted.state.people.descriptions(pid) == undone
        assert worker(restarted.state.people).proposals(pid, state="dismissed")
    source_id = proposal["source_refs"][0]
    restrict(restarted.state.people, source_id)
    with TestClient(restarted) as client:
        assert client.post(url, headers=headers, json=body).status_code == 400


def test_first_body_failure_does_not_reset_persisted_revision_when_pointer_is_minted(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    class BrokenBody(ReadOnlyMail):
        def read(self, *, message_id):
            self.reads.append(message_id)
            raise RuntimeError("body fetch temporarily unavailable")
    mail = BrokenBody()
    for attempt in range(3):
        service = worker(PersonStore(tmp_path), mail=mail)
        assert asyncio.run(service.analyze(pid, now=1000000 + attempt*1000))["status"] == "partial"
        state = service.state(pid)
        assert state["latest_revision"] == 1
        assert state["attempts"] == attempt+1
    service = worker(PersonStore(tmp_path), mail=mail)
    assert asyncio.run(service.analyze(pid, now=1004000))["status"] == "retry_required"
    assert len(mail.reads) == 3
    # A real privacy change still creates a new pending evidence revision.
    restrict(service.people, source_for(service.people, pid, "reply-1")["id"])
    assert asyncio.run(service.analyze(pid, now=1005000))["failure_code"] == "private_source"
    assert service.state(pid)["latest_revision"] == 2


def test_malformed_resolution_action_returns_400_without_touching_proposal(tmp_path):
    app = create_app(token="bad-action", state=tmp_path, provider=DescriptionModel(), gmail=ReadOnlyMail())
    pid = tracked_person(app.state.people)
    app.state.people.patch_description(pid, "general", expected_version=0, text="Keep", actor="director", rationale_summary="Keep this overview.")
    service = worker(app.state.people)
    asyncio.run(service.analyze(pid))
    proposal = service.proposals(pid)[0]
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"/v1/people/{pid}/proposals/{proposal['proposal_id']}/resolve",
            headers={TOKEN_HEADER: "bad-action"}, json={"action": [], "expected_version": 1})
    assert response.status_code == 400
    assert service.proposals(pid)[0]["state"] == "pending"


def test_genuine_attribution_change_creates_a_new_pending_input_revision(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    class BrokenBody(ReadOnlyMail):
        def read(self, **kwargs):
            raise RuntimeError("temporary body failure")
    for attempt in range(3):
        asyncio.run(worker(people, mail=BrokenBody()).analyze(pid, now=1000000 + attempt*1000))
    other = people.keep_from_apollo(apollo_id="shared", first_name="Other", last_name_obfuscated="Person", title="Lead", company="Lumen")["person_id"]
    people.apply_enrichment(other, email="maya@lumen.test")
    service = worker(people)
    result = asyncio.run(service.analyze(pid, now=1004000))
    assert result == {"status": "partial", "failure_code": "ambiguous_attribution"}
    assert service.state(pid)["latest_revision"] == 2
    assert service.provider.calls == []


def test_resolution_receipt_and_target_write_roll_back_together(tmp_path):
    import sqlite3
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    people.patch_description(pid, "general", expected_version=0, text="Keep", actor="director", rationale_summary="Keep this overview.")
    service = worker(people)
    asyncio.run(service.analyze(pid))
    proposal = service.proposals(pid)[0]
    before = people.descriptions(pid)
    history_count = people._conn.execute("SELECT count(*) FROM crm_changes").fetchone()[0]
    people._conn.execute("""CREATE TRIGGER reject_resolution BEFORE UPDATE OF resolution_receipt_json ON crm_proposals
        BEGIN SELECT RAISE(ABORT, 'simulated receipt storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        service.resolve(pid, proposal["proposal_id"], action="accept", expected_version=1)
    assert people.descriptions(pid) == before
    assert people._conn.execute("SELECT count(*) FROM crm_changes").fetchone()[0] == history_count
    row = people._conn.execute("SELECT state, resolution_receipt_json FROM crm_proposals WHERE proposal_id=?", (proposal["proposal_id"],)).fetchone()
    assert row["state"] == "pending" and row["resolution_receipt_json"] is None


def test_resolution_receipt_json_is_registered_and_doctor_validates_it(tmp_path):
    from coworker import doctor, migrations
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    people.patch_description(pid, "general", expected_version=0, text="Keep", actor="director", rationale_summary="Keep this overview.")
    service = worker(people)
    asyncio.run(service.analyze(pid))
    proposal = service.proposals(pid)[0]
    original = service.resolve(pid, proposal["proposal_id"], action="dismiss", expected_version=1)
    assert ("crm_proposals", "resolution_receipt_json") in migrations.spec_for("people_db").json_columns
    receipt = people._conn.execute("SELECT resolution_receipt_json FROM crm_proposals WHERE proposal_id=?", (proposal["proposal_id"],)).fetchone()[0]
    assert json.loads(receipt)["response"] == original
    with people._conn:
        people._conn.execute("UPDATE crm_proposals SET resolution_receipt_json='broken json' WHERE proposal_id=?", (proposal["proposal_id"],))
    report = doctor.diagnose(tmp_path)
    assert any(f.check == "sqlite.corrupt_row" and f.store_id == "people_db"
        and any("crm_proposals.resolution_receipt_json" in detail for detail in f.detail) for f in report.findings)


def test_pointer_normalization_preserves_unrelated_source_metadata_shapes(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    people.upsert_attachment(pid, record_type="source_ref", fields={"provider": "Drive", "message_id": ["external-metadata"]},
        idempotency_key="unrelated-source", actor="director", rationale_summary="Attach another source.")
    assert asyncio.run(worker(people).analyze(pid))["status"] == "analyzed"


def change_attribution(people, pid, change):
    if change == "recipient":
        target = pid
    else:
        target = people.keep_from_apollo(apollo_id="other", first_name="Other",
            last_name_obfuscated="Person", title="Lead", company="Lumen")["person_id"]
    if change == "shared_address":
        people.apply_enrichment(target, email="maya@lumen.test")
    else:
        people.record_approved_send(target, message_id="changed-send", thread_id=(
            "other-thread" if change == "account" else "tracked-1"), draft_id="changed-draft",
            to="other@lumen.test", subject="Another discussion", body_digest="digest",
            account="maya@lumen.test" if change == "account" else "director@club.test",
            approval_id="changed-approval")


@pytest.mark.parametrize("change", ["shared_address", "thread", "recipient", "account"])
@pytest.mark.parametrize("during_message", ["reply-1", "sent-1"])
@pytest.mark.parametrize("boundary", ["metadata", "body"])
def test_attribution_changed_during_mail_io_blocks_private_dispatch(
        tmp_path, change, during_message, boundary):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    class ChangingMail(ReadOnlyMail):
        changed = False

        def mutate(self, message_id, stage):
            if not self.changed and message_id == during_message and stage == boundary:
                self.changed = True
                change_attribution(people, pid, change)

        def inbound_message(self, *, message_id):
            metadata = super().inbound_message(message_id=message_id)
            self.mutate(message_id, "metadata")
            return metadata

        def read(self, *, message_id):
            full = super().read(message_id=message_id)
            self.mutate(message_id, "body")
            return full

    mail, model = ChangingMail(), DescriptionModel()
    service = worker(people, model, mail)
    result = asyncio.run(service.analyze(pid))
    if boundary == "metadata":
        assert during_message not in mail.reads
    assert model.calls == []
    assert result == {"status": "partial", "failure_code": "ambiguous_attribution"}
    assert people.descriptions(pid)["general"]["text"] is None
    assert service.state(pid)["attempts"] == 0


@pytest.mark.parametrize("change", ["shared_address", "thread", "recipient", "account"])
def test_dispatch_rechecks_attribution_without_charging_an_uncalled_model(tmp_path, monkeypatch, change):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    service = worker(people)
    claim = service._claim
    def claim_then_change(*args, **kwargs):
        result = claim(*args, **kwargs)
        if result[0]:
            change_attribution(people, pid, change)
        return result
    monkeypatch.setattr(service, "_claim", claim_then_change)
    result = asyncio.run(service.analyze(pid))
    assert service.provider.calls == []
    assert result == {"status": "partial", "failure_code": "ambiguous_attribution"}
    state = service.state(pid)
    assert state["attempts"] == 1
    assert state["provider"] is None and state["model"] is None
    assert state["usage"] is None and state["estimated_cost_usd"] is None
    assert state["budget"]["consumed"]["model_turns"] == 0
    assert state["budget"]["unmeasured_requests"] == state["budget"]["unpriced_requests"] == 0


@pytest.mark.parametrize("boundary", ["metadata", "body"])
def test_later_read_revalidates_earlier_message_attribution(tmp_path, boundary):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    people.record_approved_send(pid, message_id="z-sent-2", thread_id="separate-thread", draft_id="draft-2",
        to="alternate@lumen.test", subject="Developer tools", body_digest="digest",
        account="director@club.test", approval_id="approval-2")
    class ChangingEarlierMail(ReadOnlyMail):
        changed = False

        def mutate(self, message_id, stage):
            if not self.changed and message_id == "z-sent-2" and stage == boundary:
                self.changed = True
                change_attribution(people, pid, "shared_address")

        def inbound_message(self, *, message_id):
            metadata = super().inbound_message(message_id=message_id)
            if message_id == "z-sent-2":
                metadata.update(thread_id="separate-thread", **{"from": "director@club.test"},
                    to="alternate@lumen.test", label_ids=["SENT"])
            self.mutate(message_id, "metadata")
            return metadata

        def read(self, *, message_id):
            full = super().read(message_id=message_id)
            self.mutate(message_id, "body")
            return full

    mail, model = ChangingEarlierMail(), DescriptionModel()
    result = asyncio.run(worker(people, model, mail).analyze(pid))
    assert "reply-1" in mail.reads
    if boundary == "metadata":
        assert "z-sent-2" not in mail.reads
    assert model.calls == []
    assert result == {"status": "partial", "failure_code": "ambiguous_attribution"}
