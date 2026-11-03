from coworker.people import PersonStore
from coworker.tools import execute
from test_crm_tools import invoke


def setup(tmp_path):
    p = PersonStore(tmp_path)
    pid = p.keep_from_apollo(
        apollo_id="maya",
        first_name="Maya",
        last_name_obfuscated="Patel",
        title="Lead",
        company="Lumen",
    )["person_id"]
    p.bind_session("chat-crm", pid)
    return p, pid


def args(p, pid, **changes):
    return {
        "changes": changes,
        "expected_version": p.get(pid)["version"],
        "operation_id": "chat-person-correction",
        "reason": "Director verified the correction",
    }


def test_chat_correction_requires_exact_current_director_intent_and_protects_values(
    tmp_path,
):
    p, pid = setup(tmp_path)
    request = args(p, pid, email="maya@new.test")
    assert not execute("crm_correct_person", request, people=p, session_id="chat-crm")[
        0
    ]
    ok, result = invoke(
        p, "crm_correct_person", request, "Change Maya's email to maya@new.test"
    )
    assert ok, result
    assert (
        result["person"]["email"] == "maya@new.test"
        and result["change"]["actor"] == "assistant"
    )
    p.apply_enrichment(pid, email="provider@new.test")
    assert p.get(pid)["email"] == "maya@new.test"
    assert invoke(
        p, "crm_correct_person", request, "Change Maya's email to maya@new.test"
    )[1]["replayed"]


def test_chat_cannot_add_unrequested_fields_or_choose_another_person(tmp_path):
    p, pid = setup(tmp_path)
    assert not invoke(
        p,
        "crm_correct_person",
        args(p, pid, email="maya@new.test", company="Invented"),
        "Change Maya's email to maya@new.test",
    )[0]
    assert not invoke(
        p,
        "crm_correct_person",
        args(p, pid, email="maya@new.test"),
        "Change Other's email to maya@new.test",
    )[0]
    assert not invoke(
        p,
        "crm_correct_person",
        args(p, pid, email="maya@new.test"),
        "If confirmed, change Maya's email to maya@new.test",
    )[0]
    assert p.get(pid)["email"] is None


def test_explicit_multiple_corrections_and_field_scoped_chat_undo(tmp_path):
    p, pid = setup(tmp_path)
    ok, result = invoke(
        p,
        "crm_correct_person",
        args(p, pid, company="Lumen Labs", email="maya@new.test"),
        "Change Maya's company to Lumen Labs and email to maya@new.test",
    )
    assert ok, result
    cid = result["change"]["change_id"]
    ok, undone = invoke(
        p,
        "crm_undo_change",
        {
            "change_id": cid,
            "operation_id": "undo-chat-correction",
            "reason": "Director undid correction",
        },
        "Undo details " + cid,
    )
    assert ok, undone
    assert p.get(pid)["company"] == "Lumen" and p.get(pid)["email"] is None


def test_legacy_board_identity_writes_and_forged_authority_cannot_bypass_corrections(
    tmp_path,
):
    p, pid = setup(tmp_path)
    version = p.get(pid)["version"]
    for tool, request in [
        (
            "board_mutate",
            {
                "action": "patch",
                "person_id": pid,
                "expected_version": version,
                "fields": {"company": "Injected"},
                "rationale_summary": "Untrusted identity edit",
            },
        ),
        (
            "board_upsert",
            {
                "person_id": pid,
                "record_type": "person",
                "fields": {"company": "Injected"},
                "idempotency_key": "fake-person-upsert",
                "rationale_summary": "Untrusted identity edit",
            },
        ),
        (
            "crm_correct_person",
            {**args(p, pid, company="Injected"), "human_requested": True},
        ),
    ]:
        assert not execute(tool, request, people=p, session_id="chat-crm")[0]
    assert p.get(pid)["company"] == "Lumen"


def test_unverified_whole_person_revert_cannot_replace_corrected_identity(tmp_path):
    import pytest

    p, pid = setup(tmp_path)
    base = p.get(pid)["version"]
    p.patch(
        pid,
        fields={"company": "Human company"},
        expected_version=base,
        actor="director",
        rationale_summary="Human correction",
    )
    with pytest.raises(ValueError, match="correction"):
        p.revert(
            pid,
            to_version=base,
            expected_version=p.get(pid)["version"],
            actor="assistant",
            rationale_summary="Unverified model revert",
        )
    assert p.get(pid)["company"] == "Human company"
