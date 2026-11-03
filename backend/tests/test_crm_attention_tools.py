"""Attention/proposal chat writes retain the current director authority boundary."""

import pytest

from coworker.crm_service import CrmService
from coworker.crm_tasks import TaskService
from coworker.crm_tools import CRMMutationBudget
from coworker.people import PersonStore
from coworker.tools import execute
from test_crm_tools import invoke, keep


def setup_person(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    return people, pid, CrmService(people)


def test_attention_tools_share_saved_flags_and_require_explicit_director_commands(tmp_path):
    people, pid, service = setup_person(tmp_path)
    args = {"flag_reason": "Call Maya", "expected_version": people.get(pid)["version"],
            "operation_id": "chat-set-flag", "reason": "Director requested attention"}
    assert not execute("crm_set_attention", args, people=people, session_id="chat-crm")[0]
    budget = CRMMutationBudget()
    ok, saved = invoke(people, "crm_set_attention", args, "Set attention to: Call Maya", crm_mutation_budget=budget)
    assert ok, saved
    assert saved["attention"]["reason"] == "Call Maya"
    assert invoke(people, "crm_set_attention", args, "Set attention to: Call Maya", crm_mutation_budget=budget)[1]["replayed"]
    assert not invoke(people, "crm_set_attention", {**args, "operation_id": "different-operation"},
                      "Set attention to: Call Maya", crm_mutation_budget=budget)[0]
    assert people.get(pid)["sequence_state"] is None
    ok, listing = invoke(people, "crm_list_attention", {})
    assert ok, listing
    assert listing["contacts"][0]["person_id"] == pid
    assert listing["contacts"][0]["attention_reasons"][0]["label"] == "Call Maya"
    ok, cleared = invoke(people, "crm_set_attention", {
        "flag_reason": None, "expected_version": people.get(pid)["version"],
        "operation_id": "chat-clear-flag", "reason": "Director cleared attention",
    }, "Clear attention")
    assert ok and cleared["attention"]["reason"] is None, cleared


@pytest.mark.parametrize("text,flag,until", [
    ("Set attention to: Call Maya", "Different message", None),
    ("Set attention to: Call Maya", "Call Maya", "2026-11-20"),
    ("Set attention if approved to: Call Maya", "Call Maya", None),
    ("Do not set attention to: Call Maya", "Call Maya", None),
    ("Clear attention", "Call Maya", None),
])
def test_attention_arguments_cannot_extend_or_reverse_the_director_command(tmp_path, text, flag, until):
    people, pid, service = setup_person(tmp_path)
    before = people.get(pid)
    ok, result = invoke(people, "crm_set_attention", {
        "flag_reason": flag, "until_date": until, "expected_version": before["version"],
        "operation_id": "unsafe-attention", "reason": "Model supplied rationale",
    }, text)
    assert not ok, result
    assert people.get(pid) == before


def test_attention_clear_cannot_target_a_different_bound_person(tmp_path):
    people, pid, service = setup_person(tmp_path)
    other = keep(people, key="other", first="Other")["person_id"]
    ok, result = invoke(people, "crm_set_attention", {
        "person_id": other, "flag_reason": None, "expected_version": people.get(other)["version"],
        "operation_id": "wrong-person-clear", "reason": "Director request",
    }, "Clear attention")
    assert not ok and result["code"] == "bound_person_mismatch", result


@pytest.mark.parametrize("resolution,decision", [("accept", "accepted"), ("dismiss", "dismissed")])
def test_proposal_resolution_requires_the_exact_director_named_proposal(tmp_path, resolution, decision):
    people, pid, service = setup_person(tmp_path)
    proposal = service.propose(pid, kind="task_create", value={"title": "Send examples"},
        expected_version=0, sources=[], fingerprint="chat-proposal", reason="Suggested task")
    args = {"proposal_id": proposal["proposal_id"], "resolution": resolution,
            "expected_version": 1, "expected_target_version": 0,
            "operation_id": "chat-review-proposal", "reason": "Director reviewed suggestion"}
    ok, result = invoke(people, "crm_resolve_proposal", args,
                        f"{resolution.title()} suggestion {proposal['proposal_id']}")
    assert ok, result
    assert result["proposal"]["state"] == decision
    assert len(TaskService(people).list(person_id=pid, timezone="UTC")["tasks"]) == (resolution == "accept")


def test_resolving_another_proposal_is_not_authorized_by_an_explicit_one(tmp_path):
    people, pid, service = setup_person(tmp_path)
    proposals = [service.propose(pid, kind="task_create", value={"title": title},
        expected_version=0, sources=[], fingerprint=title, reason="Suggestion")
        for title in ("First", "Second")]
    ok, result = invoke(people, "crm_resolve_proposal", {
        "proposal_id": proposals[1]["proposal_id"], "resolution": "accept", "expected_version": 1,
        "expected_target_version": 0, "operation_id": "wrong-proposal", "reason": "Request",
    }, f"Accept suggestion {proposals[0]['proposal_id']}")
    assert not ok, result
    assert len(service.proposals(pid)) == 2


def test_non_task_undo_requires_the_exact_change_and_matching_entity(tmp_path):
    people, pid, service = setup_person(tmp_path)
    saved = service.set_attention(pid, reason="Call Maya", until=None,
        expected_version=people.get(pid)["version"], operation_id="before-attention-undo")
    args = {"change_id": saved["change"]["change_id"], "operation_id": "chat-undo-attention",
            "reason": "Director requested undo"}
    assert not invoke(people, "crm_undo_change", args, f"Undo description change {args['change_id']}")[0]
    ok, result = invoke(people, "crm_undo_change", args, f"Undo attention change {args['change_id']}")
    assert ok, result
    assert result["attention"]["reason"] is None


def test_person_named_in_attention_command_cannot_disagree_with_bound_person(tmp_path):
    people, pid, service = setup_person(tmp_path)
    before = people.get(pid)
    ok, result = invoke(people, "crm_set_attention", {
        "flag_reason": "Call Leo", "expected_version": before["version"],
        "operation_id": "wrong-command-person", "reason": "Director request",
    }, "Set attention for Leo to: Call Leo")
    assert not ok, result
    assert people.get(pid) == before


@pytest.mark.parametrize("kind", ["description", "status", "proposal"])
def test_chat_undo_uses_the_same_local_entity_receipt_as_the_app(tmp_path, kind):
    people, pid, service = setup_person(tmp_path)
    if kind == "description":
        saved = service.save_description(pid, "general", text="Director's notes", expected_version=0,
            operation_id="before-description-undo", human_requested=True)
    elif kind == "status":
        saved = service.set_status(pid, state="open", expected_version=people.get(pid)["version"],
            operation_id="before-status-undo")
    else:
        proposal = service.propose(pid, kind="task_create", value={"title": "Send examples"},
            expected_version=0, sources=[], fingerprint="undo-proposal", reason="Suggestion")
        saved = service.resolve(pid, proposal["proposal_id"], decision="accepted",
            operation_id="before-proposal-undo", timezone="America/Los_Angeles")
    args = {"change_id": saved["change"]["change_id"], "operation_id": "chat-undo-local-entity",
            "reason": "Director requested Undo"}
    ok, result = invoke(people, "crm_undo_change", args, f"Undo {kind} change {args['change_id']}")
    assert ok, result
    assert result["change"]["actor"] == "assistant"
    if kind == "description":
        assert people.descriptions(pid)["general"]["text"] is None
    elif kind == "status":
        assert people.get(pid)["sequence_state"] is None
    else:
        assert TaskService(people).list(person_id=pid, timezone="UTC")["tasks"] == []


def test_attention_expiry_and_literal_reason_match_the_director(tmp_path):
    people, pid, service = setup_person(tmp_path)
    ok, result = invoke(people, "crm_set_attention", {
        "flag_reason": "Call before lunch", "until_date": "2026-11-20",
        "expected_version": people.get(pid)["version"], "operation_id": "dated-attention-request",
        "reason": "Director requested dated attention",
    }, "Set attention until 2026-11-20 to: Call before lunch")
    assert ok, result
    assert result["attention"] == {"reason": "Call before lunch", "until": "2026-11-20"}


def test_proposal_source_access_is_required_even_for_exact_director_acceptance(tmp_path):
    import json
    people, pid, service = setup_person(tmp_path)
    source = people.upsert_attachment(pid, record_type="source_ref",
        fields={"id": "private-reply", "sensitivity": "restricted"},
        idempotency_key="private-proposal-ref", actor="director", rationale_summary="Source")
    proposal = service.propose(pid, kind="task_create", value={"title": "Private task"},
        expected_version=0, sources=[], fingerprint="private-proposal", reason="Private suggestion")
    # A saved proposal may outlive the source permission of the current chat.
    with people.crm_transaction():
        people._conn.execute("UPDATE crm_proposals SET source_refs=? WHERE proposal_id=?",
            (json.dumps([source["id"]]), proposal["proposal_id"]))
    args = {"proposal_id": proposal["proposal_id"], "resolution": "accept", "expected_version": 1,
        "expected_target_version": 0, "operation_id": "private-proposal-accept", "reason": "Director request"}
    text = f"Accept suggestion {proposal['proposal_id']}"
    ok, result = invoke(people, "crm_resolve_proposal", args, text)
    assert not ok and result["code"] == "source_access_denied", result
    assert "Private task" not in json.dumps(result)
    ok, result = invoke(people, "crm_resolve_proposal", args, text, allowed_source_ids={source["id"]})
    assert ok, result
    assert result["task"]["title"] == "Private task"


def test_attention_with_inaccessible_work_is_partial_instead_of_an_all_clear(tmp_path):
    people, pid, service = setup_person(tmp_path)
    source = people.upsert_attachment(pid, record_type="source_ref",
        fields={"id": "private-attention-source", "sensitivity": "restricted"},
        idempotency_key="private-attention-ref", actor="director", rationale_summary="Source")
    TaskService(people).create(pid, title="Private overdue action", due_date="2020-01-01",
        timezone="America/Los_Angeles", actor="assistant", operation_id="private-attention-task",
        source_refs=[source["id"]], allowed_source_ids={source["id"]})
    ok, result = invoke(people, "crm_list_attention", {})
    assert ok and result["contacts"] == [] and result["partial"], result
    assert "Private overdue action" not in str(result)


def test_global_chat_tasks_include_the_apps_kept_person_task(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    saved = TaskService(people).create(pid, title="Prepare before outreach", timezone="America/Los_Angeles",
        actor="director", operation_id="kept-person-app-task")["task"]
    assert people.get(pid)["sequence_state"] is None
    ok, result = invoke(people, "crm_list_tasks", {})
    assert ok, result
    assert [task["task_id"] for task in result["tasks"]] == [saved["task_id"]]
    assert people.get(pid)["sequence_state"] is None


def test_sourced_proposal_outcome_keeps_only_canonical_source_ids(tmp_path):
    import json
    from coworker.crm_tools import crm_outcome_references

    people, pid, service = setup_person(tmp_path)
    ref = people.upsert_attachment(pid, record_type="source_ref",
        fields={"id": "reviewed-proposal-source", "provider": "Drive", "title": "Private source title"},
        idempotency_key="proposal-outcome-source", actor="director", rationale_summary="File source")
    proposal = service.propose(pid, kind="task_create", value={"title": "Sensitive proposed message"},
        expected_version=0, sources=[ref["id"]], fingerprint="proposal-outcome", reason="Review suggestion")
    resolution = service.resolve(pid, proposal["proposal_id"], decision="dismissed",
        operation_id="dismiss-source-outcome", timezone="America/Los_Angeles")
    assert resolution["proposal"]["source_refs"][0]["id"] == ref["id"]

    references = crm_outcome_references({**resolution, "person_id": pid})

    assert references["source_refs"] == [{"id": ref["id"], "provider": "sourcecado", "title": "CRM source reference"}]
    encoded = json.dumps(references)
    assert "Sensitive proposed message" not in encoded
    assert "Private source title" not in encoded
    assert '"url"' not in encoded and '"body"' not in encoded
