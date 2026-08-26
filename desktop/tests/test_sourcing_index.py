import pytest

from coworker.sourcing_index import RECORD_TYPES, SourcingIndex


def test_upsert_supports_every_board_record_type_with_idempotent_audit_receipts(tmp_path):
    index = SourcingIndex(tmp_path)

    for record_type in sorted(RECORD_TYPES):
        created = index.upsert(
            record_type=record_type,
            fields={"name": f"Example {record_type}"},
            idempotency_key=f"fixture:{record_type}",
            actor="assistant",
            session_id="session-1",
            run_id="run-1",
            rationale_summary=f"Create the {record_type} fixture.",
            source_refs=["source:fixture"],
        )
        replayed = index.upsert(
            record_type=record_type,
            fields={"name": f"Example {record_type}"},
            idempotency_key=f"fixture:{record_type}",
            actor="assistant",
            session_id="session-1",
            run_id="run-1",
            rationale_summary=f"Create the {record_type} fixture.",
            source_refs=["source:fixture"],
        )

        assert created["type"] == record_type
        assert created["version"] == 1
        assert replayed == created
        receipts = index.receipts(created["id"])
        assert len(receipts) == 1
        assert receipts[0]["operation"] == "create"
        assert receipts[0]["actor"] == "assistant"
        assert receipts[0]["session_id"] == "session-1"
        assert receipts[0]["run_id"] == "run-1"
        assert receipts[0]["rationale_summary"] == f"Create the {record_type} fixture."
        assert receipts[0]["source_refs"] == ["source:fixture"]


def test_upsert_rejects_unknown_record_type(tmp_path):
    index = SourcingIndex(tmp_path)

    with pytest.raises(ValueError, match="unknown record type"):
        index.upsert(
            record_type="lead",
            fields={"name": "Not a domain record"},
            idempotency_key="fixture:lead",
            actor="assistant",
            rationale_summary="Do not create an ad-hoc type.",
        )


def test_idempotency_key_deduplicates_identity_but_rejects_conflicting_facts(tmp_path):
    index = SourcingIndex(tmp_path)
    created = index.upsert(
        record_type="contact",
        fields={"name": "Ada", "primary_email": "ada@example.com"},
        idempotency_key="contact:ada@example.com",
        actor="assistant",
        rationale_summary="Create the email identity.",
    )
    replayed = index.upsert(
        record_type="contact",
        fields={"name": "Ada", "primary_email": "ada@example.com"},
        idempotency_key="contact:ada@example.com",
        actor="assistant",
        rationale_summary="Create the email identity.",
    )

    assert replayed["id"] == created["id"]
    assert len(index.query(record_type="contact")) == 1
    with pytest.raises(ValueError, match="idempotency conflict"):
        index.upsert(
            record_type="contact",
            fields={"name": "Grace", "primary_email": "ada@example.com"},
            idempotency_key="contact:ada@example.com",
            actor="assistant",
            rationale_summary="Do not overwrite the conflicting identity.",
        )


def test_patch_uses_optimistic_versions_and_survives_restart(tmp_path):
    index = SourcingIndex(tmp_path)
    created = index.upsert(
        record_type="contact",
        fields={"name": "Ada", "owner": "fisher"},
        idempotency_key="contact:ada",
        actor="assistant",
        rationale_summary="Create Ada.",
    )

    updated = index.patch(
        created["id"],
        fields={"title": "Founder"},
        expected_version=1,
        actor="director",
        session_id="session-2",
        run_id="run-2",
        rationale_summary="Add the verified role.",
        source_refs=["source:apollo"],
    )

    assert updated["version"] == 2
    assert updated["fields"] == {"name": "Ada", "owner": "fisher", "title": "Founder"}
    with pytest.raises(ValueError, match="stale record version"):
        index.patch(
            created["id"],
            fields={"title": "Stale overwrite"},
            expected_version=1,
            actor="assistant",
            rationale_summary="This should not overwrite a human edit.",
        )
    receipts = index.receipts(created["id"])
    assert [receipt["operation"] for receipt in receipts] == ["create", "patch"]
    assert receipts[-1]["before"]["version"] == 1
    assert receipts[-1]["after"]["version"] == 2
    assert SourcingIndex(tmp_path).get(created["id"]) == updated


def test_query_filters_board_records_by_type_stage_owner_and_due_date(tmp_path):
    index = SourcingIndex(tmp_path)
    for key, fields in (
        (
            "opportunity:ada",
            {"name": "Ada dinner", "stage": "research", "owner": "fisher", "due_date": "2026-09-01"},
        ),
        (
            "opportunity:grace",
            {"name": "Grace panel", "stage": "ready", "owner": "lavanya", "due_date": "2026-09-02"},
        ),
    ):
        index.upsert(
            record_type="opportunity",
            fields=fields,
            idempotency_key=key,
            actor="assistant",
            rationale_summary="Create a query fixture.",
        )

    rows = index.query(
        record_type="opportunity",
        filters={"stage": "research", "owner": "fisher", "due_date": "2026-09-01"},
    )

    assert [row["fields"]["name"] for row in rows] == ["Ada dinner"]


def test_restricted_source_refs_require_server_supplied_grants_for_reads_and_expansion(tmp_path):
    index = SourcingIndex(tmp_path)
    restricted = index.upsert(
        record_type="source_ref",
        fields={
            "title": "Resume",
            "provider": "drive",
            "external_id": "drive-resume-1",
            "sensitivity": "restricted",
        },
        idempotency_key="source:resume-1",
        actor="director",
        rationale_summary="Register a restricted resume source.",
    )
    contact = index.upsert(
        record_type="contact",
        fields={"name": "Ada"},
        idempotency_key="contact:ada-restricted",
        actor="assistant",
        rationale_summary="Create the contact without copying resume content.",
        source_refs=[restricted["id"]],
    )

    assert index.get(restricted["id"]) is None
    assert index.get(restricted["id"], allowed_source_ids={restricted["id"]}) == restricted
    without_grant = index.get(contact["id"], expand_sources=True)
    assert without_grant is not None
    assert without_grant["sources"] == []
    assert without_grant["restricted_source_count"] == 1
    with_grant = index.get(
        contact["id"],
        expand_sources=True,
        allowed_source_ids={restricted["id"]},
    )
    assert with_grant is not None
    assert [source["id"] for source in with_grant["sources"]] == [restricted["id"]]


def test_relationship_link_and_unlink_are_idempotent_audited_and_durable(tmp_path):
    index = SourcingIndex(tmp_path)
    contact = index.upsert(
        record_type="contact",
        fields={"name": "Ada"},
        idempotency_key="contact:ada-link",
        actor="assistant",
        rationale_summary="Create Ada.",
    )
    opportunity = index.upsert(
        record_type="opportunity",
        fields={"name": "Fall dinner", "stage": "research"},
        idempotency_key="opportunity:fall-dinner",
        actor="assistant",
        rationale_summary="Create the opportunity.",
    )

    linked = index.link(
        contact["id"],
        opportunity["id"],
        relationship="candidate_for",
        actor="assistant",
        session_id="session-3",
        run_id="run-3",
        rationale_summary="Associate the contact with the opportunity.",
        source_refs=["source:director-note"],
    )
    replayed = index.link(
        contact["id"],
        opportunity["id"],
        relationship="candidate_for",
        actor="assistant",
        session_id="session-3",
        run_id="run-3",
        rationale_summary="Associate the contact with the opportunity.",
        source_refs=["source:director-note"],
    )

    assert replayed == linked
    assert SourcingIndex(tmp_path).links(contact["id"]) == [linked]
    removed = index.unlink(
        contact["id"],
        opportunity["id"],
        relationship="candidate_for",
        actor="director",
        rationale_summary="Remove the mistaken relationship.",
    )
    assert removed["removed"] is True
    assert index.links(contact["id"]) == []
    operations = [receipt["operation"] for receipt in index.receipts(contact["id"])]
    assert operations == ["create", "link", "unlink"]


def test_opportunity_transition_requires_real_touchpoint_evidence_not_collateral(tmp_path):
    index = SourcingIndex(tmp_path)
    opportunity = index.upsert(
        record_type="opportunity",
        fields={"name": "Fall dinner", "stage": "ready"},
        idempotency_key="opportunity:transition",
        actor="assistant",
        rationale_summary="Create the ready opportunity.",
    )
    artifact = index.upsert(
        record_type="artifact",
        fields={"title": "Draft email", "artifact_type": "gmail_draft"},
        idempotency_key="artifact:draft",
        actor="assistant",
        rationale_summary="Record a draft, not a sent message.",
    )

    with pytest.raises(ValueError, match="outbound sent touchpoint"):
        index.transition(
            opportunity["id"],
            to_stage="contacted",
            evidence_record_ids=[artifact["id"]],
            expected_version=1,
            actor="assistant",
            rationale_summary="A draft alone must not count as contact.",
        )

    touchpoint = index.upsert(
        record_type="touchpoint",
        fields={"channel": "email", "direction": "outbound", "status": "sent"},
        idempotency_key="touchpoint:sent-email",
        actor="director",
        rationale_summary="Record the actual sent email.",
        source_refs=["source:gmail-message"],
    )
    transitioned = index.transition(
        opportunity["id"],
        to_stage="contacted",
        evidence_record_ids=[touchpoint["id"]],
        expected_version=1,
        actor="director",
        rationale_summary="The sent touchpoint proves contact occurred.",
        source_refs=["source:gmail-message"],
    )

    assert transitioned["fields"]["stage"] == "contacted"
    assert transitioned["version"] == 2
    assert index.receipts(opportunity["id"])[-1]["operation"] == "transition"


def test_action_completion_and_outcome_capture_are_versioned_domain_operations(tmp_path):
    index = SourcingIndex(tmp_path)
    action = index.upsert(
        record_type="action",
        fields={"title": "Review draft", "status": "open"},
        idempotency_key="action:review-draft",
        actor="assistant",
        rationale_summary="Create the review action.",
    )
    opportunity = index.upsert(
        record_type="opportunity",
        fields={"name": "Fall dinner", "stage": "contacted"},
        idempotency_key="opportunity:outcome",
        actor="assistant",
        rationale_summary="Create the contacted opportunity.",
    )

    completed = index.complete_action(
        action["id"],
        expected_version=1,
        actor="director",
        rationale_summary="The director reviewed the draft.",
    )
    captured = index.capture_outcome(
        opportunity["id"],
        outcome="declined",
        expected_version=1,
        actor="director",
        rationale_summary="Record the explicit response outcome.",
        source_refs=["source:gmail-reply"],
    )

    assert completed["fields"]["status"] == "completed"
    assert completed["fields"]["completed_at"]
    assert captured["fields"]["outcome"] == "declined"
    assert captured["fields"]["outcome_at"]
    assert index.receipts(action["id"])[-1]["operation"] == "complete_action"
    assert index.receipts(opportunity["id"])[-1]["operation"] == "capture_outcome"


def test_revert_restores_a_prior_snapshot_as_a_new_audited_version(tmp_path):
    index = SourcingIndex(tmp_path)
    created = index.upsert(
        record_type="contact",
        fields={"name": "Ada", "title": "Founder"},
        idempotency_key="contact:revert",
        actor="assistant",
        rationale_summary="Create Ada.",
    )
    updated = index.patch(
        created["id"],
        fields={"title": "Incorrect title"},
        expected_version=1,
        actor="assistant",
        rationale_summary="Apply a mistaken source value.",
    )

    reverted = index.revert(
        created["id"],
        to_version=1,
        expected_version=updated["version"],
        actor="director",
        rationale_summary="Restore the verified title.",
    )

    assert reverted["fields"] == created["fields"]
    assert reverted["version"] == 3
    assert index.receipts(created["id"])[-1]["operation"] == "revert"


def test_delete_removes_record_but_preserves_immutable_delete_receipt(tmp_path):
    index = SourcingIndex(tmp_path)
    created = index.upsert(
        record_type="knowledge_gap",
        fields={"question": "Who approved this?"},
        idempotency_key="gap:delete",
        actor="assistant",
        rationale_summary="Create the gap.",
    )

    deleted = index.delete(
        created["id"],
        expected_version=1,
        actor="director",
        rationale_summary="Delete the duplicate after explicit approval.",
    )

    assert deleted == {"deleted": True, "id": created["id"]}
    assert index.get(created["id"]) is None
    receipts = index.receipts(created["id"])
    assert [receipt["operation"] for receipt in receipts] == ["create", "delete"]
    assert receipts[-1]["before"]["id"] == created["id"]
    assert receipts[-1]["after"] is None


def test_every_low_level_write_requires_actor_and_rationale(tmp_path):
    index = SourcingIndex(tmp_path)
    created = index.upsert(
        record_type="contact",
        fields={"name": "Ada"},
        idempotency_key="contact:write-metadata",
        actor="assistant",
        rationale_summary="Create Ada.",
    )

    with pytest.raises(ValueError, match="actor and rationale_summary are required"):
        index.delete(
            created["id"],
            expected_version=1,
            actor="",
            rationale_summary="",
        )
