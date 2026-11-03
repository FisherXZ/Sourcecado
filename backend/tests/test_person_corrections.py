"""Human identity corrections share durable storage without moving attached work."""

import pytest
from fastapi.testclient import TestClient
from coworker.people import PersonStore
from coworker.crm_tasks import TaskService
from coworker.server import create_app, TOKEN_HEADER

HEADERS = {TOKEN_HEADER: "correction-test"}


def person(people, key="maya", email="maya@lumen.test"):
    pid = people.keep_from_apollo(
        apollo_id=key,
        first_name="Maya",
        last_name_obfuscated="Patel",
        title="Lead",
        company="Lumen",
    )["person_id"]
    people.apply_enrichment(pid, email=email)
    return pid


def fixture(tmp_path):
    app = create_app(token="correction-test", state=tmp_path, provider=None)
    pid = person(app.state.people)
    return app, pid, TestClient(app)


def save(client, pid, version, changes, operation="correct-maya-details", **extra):
    return client.post(
        f"/v1/people/{pid}/corrections",
        headers=HEADERS,
        json={
            "changes": changes,
            "expected_version": version,
            "operation_id": operation,
            "reason": "Director verified these details",
            **extra,
        },
    )


def test_review_then_save_preserves_person_work_and_replays_after_restart(tmp_path):
    app, pid, client = fixture(tmp_path)
    people = app.state.people
    task = TaskService(people).create(
        pid,
        title="Send examples",
        timezone="UTC",
        actor="director",
        operation_id="keep-person-task",
    )["task"]
    people.set_handoff(
        pid,
        who="Known offline",
        wanted="Review examples",
        happened="Met",
        they_want="Follow up",
    )
    version = people.get(pid)["version"]
    revision = people.get(pid)["identity_revision"]
    review = client.post(
        f"/v1/people/{pid}/corrections/review",
        headers=HEADERS,
        json={
            "changes": {"company": "Lumen Labs", "email": "maya@lumenlabs.test"},
            "expected_version": version,
        },
    )
    assert review.status_code == 200, review.text
    assert review.json()["changes"][0]["before"] is not None
    response = save(
        client, pid, version, {"company": "Lumen Labs", "email": "maya@lumenlabs.test"}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert (
        result["person"]["person_id"] == pid
        and result["person"]["identity_revision"] == revision + 1
    )
    assert people.get(pid)["handoff_who"] == "Known offline"
    assert TaskService(people).get(pid, task["task_id"])["title"] == "Send examples"
    reopened = create_app(token="correction-test", state=tmp_path, provider=None)
    replay = save(
        TestClient(reopened),
        pid,
        version,
        {"company": "Lumen Labs", "email": "maya@lumenlabs.test"},
    )
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert replay.json()["change"]["change_id"] == result["change"]["change_id"]
    assert (
        len(
            [
                r
                for r in reopened.state.crm.recent_changes(pid)
                if r["entity_type"] == "person_details"
            ]
        )
        == 1
    )
    assert (
        save(
            client,
            pid,
            version,
            {"company": "Different"},
            operation="correct-maya-details",
        ).status_code
        == 409
    )


def test_a_human_correction_is_not_replaced_by_later_provider_data(tmp_path):
    people = PersonStore(tmp_path)
    pid = person(people)
    people.patch(
        pid,
        fields={"email": "human@lumen.test", "company": "Human company"},
        expected_version=people.get(pid)["version"],
        actor="director",
        rationale_summary="Director correction",
    )
    revision = people.get(pid)["identity_revision"]
    people.apply_enrichment(
        pid,
        email="provider@lumen.test",
        company="Provider company",
        phone="+1 415 555 0100",
    )
    assert people.get(pid)["email"] == "human@lumen.test"
    assert people.get(pid)["company"] == "Human company"
    assert people.get(pid)["phone"] == "+1 415 555 0100"
    assert people.get(pid)["identity_revision"] == revision
    assert {r["field"] for r in people.get(pid)["identity_conflicts"]} == {
        "email",
        "company",
    }
    people.keep_from_apollo(
        apollo_id="maya",
        first_name="Maya",
        last_name_obfuscated="Patel",
        title="Lead",
        company="Another provider company",
    )
    assert people.get(pid)["company"] == "Human company"


def test_duplicate_email_requires_exact_review_confirmation_without_merging(tmp_path):
    app, pid, client = fixture(tmp_path)
    other = person(app.state.people, "other", "other@lumen.test")
    version = app.state.people.get(pid)["version"]
    response = save(client, pid, version, {"email": "other@lumen.test"})
    assert response.status_code == 409
    review = response.json()["review"]
    assert review["duplicates"][0]["person_id"] == other
    assert review["duplicates"][0]["href"] == f"#/people/{other}"
    assert (
        save(
            client,
            pid,
            version,
            {"email": "other@lumen.test"},
            collision_token=review["collision_token"],
            confirmed_duplicate_ids=[other],
        ).status_code
        == 200
    )
    assert app.state.people.get(other)["email"] == "other@lumen.test"
    assert app.state.people.get(pid)["person_id"] != other


def test_correction_undo_is_field_scoped_and_keeps_new_work(tmp_path):
    app, pid, client = fixture(tmp_path)
    p = app.state.people
    before = p.get(pid)
    revision = before["identity_revision"]
    response = save(client, pid, before["version"], {"email": "changed@lumen.test"})
    assert response.status_code == 200, response.text
    cid = response.json()["change"]["change_id"]
    task = TaskService(p).create(
        pid,
        title="New independent promise",
        timezone="UTC",
        actor="director",
        operation_id="new-independent-task",
    )["task"]
    p.patch_description(
        pid,
        "general",
        expected_version=0,
        text="Keep new description",
        actor="director",
        rationale_summary="Director edit",
    )
    undo = client.post(
        f"/v1/people/{pid}/crm-changes/{cid}/undo",
        headers=HEADERS,
        json={"operation_id": "undo-person-correction"},
    )
    assert undo.status_code == 200, undo.text
    assert (
        p.get(pid)["email"] == before["email"]
        and p.get(pid)["identity_revision"] == revision + 2
    )
    assert p.descriptions(pid)["general"]["text"] == "Keep new description"
    assert TaskService(p).get(pid, task["task_id"])["state"] == "open"
    assert client.post(
        f"/v1/people/{pid}/crm-changes/{cid}/undo",
        headers=HEADERS,
        json={"operation_id": "undo-person-correction"},
    ).json()["replayed"]


def test_correction_undo_refuses_later_same_field_edit(tmp_path):
    app, pid, client = fixture(tmp_path)
    p = app.state.people
    first = save(client, pid, p.get(pid)["version"], {"company": "First correction"})
    assert first.status_code == 200
    assert (
        save(
            client,
            pid,
            p.get(pid)["version"],
            {"company": "Later correction"},
            operation="later-correction",
        ).status_code
        == 200
    )
    undo = client.post(
        f"/v1/people/{pid}/crm-changes/{first.json()['change']['change_id']}/undo",
        headers=HEADERS,
        json={"operation_id": "stale-person-undo"},
    )
    assert undo.status_code == 409 and p.get(pid)["company"] == "Later correction"


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "bad address"},
        {"linkedin_url": "javascript:alert(1)"},
        {"phone": "abc"},
        {"person_id": "other"},
        {"first_name": "A***"},
    ],
)
def test_invalid_details_are_rejected_without_history(tmp_path, changes):
    app, pid, client = fixture(tmp_path)
    p = app.state.people
    before = p.get(pid)
    result = save(client, pid, before["version"], changes)
    assert result.status_code == 400
    assert p.get(pid) == before


def test_enrichment_receipt_reports_actual_applied_fields_and_sourced_conflicts(
    tmp_path,
):
    p = PersonStore(tmp_path)
    pid = person(p)
    p.patch(
        pid,
        fields={"email": "human@lumen.test", "company": "Human company"},
        expected_version=p.get(pid)["version"],
        actor="director",
        rationale_summary="Verified offline",
    )
    result = p.record_apollo_enrichment(
        pid,
        result={
            "apolloId": "maya",
            "name": "Maya Patel",
            "title": "Engineer",
            "organizationName": "Provider company",
            "email": "provider@lumen.test",
            "phone": "+1 415 555 0100",
        },
        approval_id="approved-provider-lookup",
        credits=1,
        matched_on="apollo_id",
    )
    assert result["fields_found"] == ["company", "email", "name", "phone", "title"]
    assert result["fields_applied"] == ["name", "phone", "title"]
    assert {item["field"] for item in result["conflicts"]} == {"company", "email"}
    assert all(
        item["source_ref_id"] == result["source_ref"]["id"]
        for item in result["conflicts"]
    )
    assert p.get(pid)["email"] == "human@lumen.test"
    assert result["source_ref"]["fields"]["fields_applied"] == result["fields_applied"]


def test_person_revert_cannot_make_an_old_field_correction_undo_eligible_again(
    tmp_path,
):
    from coworker.person_corrections import PersonCorrectionService
    from coworker.crm_tasks import TaskConflict

    p = PersonStore(tmp_path)
    pid = person(p)
    base = p.get(pid)["version"]
    corrected = PersonCorrectionService(p).correct(
        pid,
        changes={"company": "New company"},
        expected_version=base,
        operation_id="correction-before-revert",
        reason="Verified company",
    )
    saved = p.get(pid)["version"]
    p.revert(
        pid,
        to_version=base,
        expected_version=saved,
        actor="director",
        rationale_summary="Revert old company",
    )
    p.revert(
        pid,
        to_version=saved,
        expected_version=p.get(pid)["version"],
        actor="director",
        rationale_summary="Restore newer company",
    )
    with pytest.raises(TaskConflict):
        PersonCorrectionService(p).undo(
            pid, corrected["change"]["change_id"], operation_id="undo-before-revert"
        )


def test_correction_record_failure_rolls_back_values_controls_and_identity_revision(
    tmp_path, monkeypatch
):
    from coworker.person_corrections import PersonCorrectionService
    from coworker.crm_service import CrmService

    p = PersonStore(tmp_path)
    pid = person(p)
    before = p.get(pid)

    def fail(*args, **kwargs):
        raise RuntimeError("Receipt interrupted")

    monkeypatch.setattr(CrmService, "_record", fail)
    with pytest.raises(RuntimeError):
        PersonCorrectionService(p).correct(
            pid,
            changes={"email": "new@lumen.test"},
            expected_version=before["version"],
            operation_id="atomic-person-correction",
            reason="Verified email",
        )
    assert p.get(pid) == before
    assert (
        p._conn.execute(
            "SELECT count(*) FROM person_field_controls WHERE person_id=?", (pid,)
        ).fetchone()[0]
        == 0
    )


def test_schema_9_correction_upgrade_is_backed_up_and_can_roll_back(
    tmp_path, monkeypatch
):
    import dataclasses
    from coworker import migrations

    p = PersonStore(tmp_path)
    pid = person(p)
    p._conn.execute("DROP TABLE person_field_controls")
    p._conn.execute("DROP TABLE person_correction_conflicts")
    p._conn.execute("PRAGMA user_version=9")
    p._conn.commit()
    before = p.get(pid)
    p._conn.close()
    spec = migrations.spec_for("people_db")
    original = next(step for step in spec.migrations if step.from_version == 9)

    def fail(context):
        original.apply(context)
        raise RuntimeError("Interrupted correction migration")

    broken = dataclasses.replace(
        spec,
        migrations=tuple(
            dataclasses.replace(step, apply=fail) if step.from_version == 9 else step
            for step in spec.migrations
        ),
    )
    monkeypatch.setattr(
        migrations,
        "REGISTRY",
        tuple(
            broken if item.store_id == "people_db" else item
            for item in migrations.REGISTRY
        ),
    )
    result = migrations.apply_migrations(tmp_path)
    assert result.error and result.backup_id
    monkeypatch.undo()
    assert migrations.apply_migrations(tmp_path).error is None
    p = PersonStore(tmp_path)
    assert p.get(pid) == before
    assert p._conn.execute("PRAGMA user_version").fetchone()[0] == 10
    assert p._conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_paid_result_filing_failure_rolls_back_details_conflicts_and_identity(
    tmp_path, monkeypatch
):
    p = PersonStore(tmp_path)
    pid = person(p)
    p.patch(
        pid,
        fields={"company": "Human company"},
        expected_version=p.get(pid)["version"],
        actor="director",
        rationale_summary="Verified company",
    )
    before = p.get(pid)

    def fail(*args, **kwargs):
        raise RuntimeError("Source filing failed")

    monkeypatch.setattr(p, "upsert_attachment", fail)
    with pytest.raises(RuntimeError):
        p.record_apollo_enrichment(
            pid,
            result={
                "email": "new@lumen.test",
                "organizationName": "Provider company",
                "phone": "+1 415 555 0100",
            },
            approval_id="paid-result-filing-failure",
            credits=1,
            matched_on="apollo_id",
        )
    assert p.get(pid) == before
    assert not p._conn.execute(
        "SELECT 1 FROM events WHERE person_id=? AND kind='enrich'", (pid,)
    ).fetchone()


def test_enrichment_unknown_actual_cost_stays_unknown(tmp_path):
    p = PersonStore(tmp_path)
    pid = person(p)
    result = p.record_apollo_enrichment(
        pid,
        result={"email": "new@lumen.test"},
        approval_id="unknown-provider-cost",
        credits=None,
        matched_on="apollo_id",
    )
    assert result["source_ref"]["fields"]["credits"] is None
    assert p.get(pid)["email"] == "new@lumen.test"


def test_paid_result_receipt_replay_does_not_apply_again_after_a_human_correction(tmp_path):
    p=PersonStore(tmp_path);pid=person(p)
    kwargs={'result':{'email':'provider@lumen.test','organizationName':'Provider company'},'approval_id':'once-filed-provider-result','credits':None,'matched_on':'apollo_id'}
    first=p.record_apollo_enrichment(pid,**kwargs)
    p.patch(pid,fields={'company':'Human company'},expected_version=p.get(pid)['version'],actor='director',rationale_summary='Later human correction')
    before=p.get(pid)
    replay=p.record_apollo_enrichment(pid,**kwargs)
    assert replay['replayed']
    assert p.get(pid)==before
    assert replay['fields_applied']==first['fields_applied']
    assert p._conn.execute("SELECT count(*) FROM events WHERE person_id=? AND kind='enrich'",(pid,)).fetchone()[0]==1
    assert p.get_apollo_enrichment_receipt(pid,kwargs['approval_id'])['source_ref']['id']==first['source_ref']['id']


def test_paid_result_approval_id_cannot_be_reused_for_different_provider_facts(tmp_path):
    from coworker.crm_tasks import TaskConflict
    p=PersonStore(tmp_path);pid=person(p)
    p.record_apollo_enrichment(pid,result={'email':'provider@lumen.test'},approval_id='bound-provider-result',credits=None,matched_on='apollo_id')
    before=p.get(pid)
    with pytest.raises(TaskConflict):
        p.record_apollo_enrichment(pid,result={'email':'different@lumen.test'},approval_id='bound-provider-result',credits=None,matched_on='apollo_id')
    assert p.get(pid)==before
