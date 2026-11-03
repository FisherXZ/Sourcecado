"""Exact retries of v7 attention description receipts survive the schema join."""

import pytest
from coworker.people import PersonStore
from coworker.crm_service import CrmService
from coworker.crm_tasks import TaskConflict, _fingerprint
from coworker.migrations import apply_migrations


def historical_save(people, pid, *, sources=()):
    """The released v7 writer stored these receipts in crm_operations."""
    service = CrmService(people)
    before = people.descriptions(pid)["general"]
    after = {
        **before,
        "text": "Saved human context",
        "authority": "human",
        "source_refs": list(sources),
        "version": 1,
        "analyzed_input_revision": 0,
    }
    fingerprint = _fingerprint(
        {
            "description": pid,
            "slot": "general",
            "text": after["text"],
            "release": False,
            "version": 0,
            "actor": "director",
            "sources": list(sources),
            "human": True,
        }
    )
    with people.crm_transaction():
        service._write_description(pid, after)
        service._bump_person(pid)
        result = service._record(
            pid,
            "description",
            pid + ":general",
            1,
            before,
            after,
            operation="legacy-description-save",
            fingerprint=fingerprint,
            actor="director",
            sources=sources,
        )
    return result


def setup(tmp_path, sources=()):
    people = PersonStore(tmp_path)
    pid = people.keep_from_apollo(
        apollo_id="maya",
        first_name="Maya",
        last_name_obfuscated=None,
        title="Lead",
        company="Lumen",
    )["person_id"]
    return people, pid


def test_legacy_description_save_retry_after_upgrade_and_restart(tmp_path):
    people, pid = setup(tmp_path)
    saved = historical_save(people, pid)
    people._conn.execute("PRAGMA user_version=7")
    people._conn.commit()
    people._conn.close()
    assert apply_migrations(tmp_path).error is None
    people = PersonStore(tmp_path)
    service = CrmService(people)
    replay = service.save_description(
        pid,
        "general",
        text="Saved human context",
        expected_version=0,
        operation_id="legacy-description-save",
    )
    assert replay["replayed"] and replay["description"] == saved["description"]
    assert replay["change"]["change_id"] == saved["change"]["change_id"]
    assert people.descriptions(pid)["general"]["version"] == 1
    with pytest.raises(TaskConflict, match="operation"):
        service.save_description(
            pid,
            "general",
            text="Different wording",
            expected_version=0,
            operation_id="legacy-description-save",
        )
    assert people.descriptions(pid)["general"]["text"] == "Saved human context"


def test_legacy_description_replay_rechecks_revoked_source_grant(tmp_path):
    people, pid = setup(tmp_path)
    ref = people.upsert_attachment(
        pid,
        record_type="source_ref",
        fields={"id": "private-source", "sensitivity": "restricted"},
        idempotency_key="private-source",
        actor="director",
        rationale_summary="File private source",
    )["id"]
    historical_save(people, pid, sources=(ref,))
    args = dict(
        text="Saved human context",
        expected_version=0,
        operation_id="legacy-description-save",
        sources=(ref,),
    )
    assert CrmService(people).save_description(
        pid, "general", **args, allowed_source_ids={ref}
    )["replayed"]
    with pytest.raises((ValueError, TaskConflict), match="private"):
        CrmService(people).save_description(
            pid, "general", **args, allowed_source_ids=set()
        )


def test_legacy_description_undo_retry_uses_its_saved_receipt(tmp_path):
    people, pid = setup(tmp_path)
    saved = historical_save(people, pid)
    service = CrmService(people)
    current = people.descriptions(pid)["general"]
    restored = {
        **current,
        "text": None,
        "authority": "human",
        "source_refs": [],
        "version": 2,
    }
    with people.crm_transaction():
        service._write_description(pid, restored)
        service._bump_person(pid)
        original = service._record(
            pid,
            "description",
            pid + ":general",
            2,
            current,
            restored,
            operation="legacy-description-undo",
            fingerprint=_fingerprint(
                {
                    "undo": saved["change"]["change_id"],
                    "person": pid,
                    "actor": "director",
                }
            ),
            actor="director",
            action="undo",
            compensates=saved["change"]["change_id"],
        )
    replay = service.undo(
        pid, saved["change"]["change_id"], operation_id="legacy-description-undo"
    )
    assert replay["replayed"] and replay["description"] == original["description"]
    assert people.descriptions(pid)["general"]["version"] == 2
