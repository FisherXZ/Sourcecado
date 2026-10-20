"""Adversarial review reproductions against the real email/task pipeline."""
import asyncio
import json

import pytest

from coworker.crm_tasks import TaskConflict, TaskService
from coworker.people import PersonStore
from test_crm_analysis import tracked_person
from test_crm_email_tasks import TaskMail, candidate, run, saved_tasks

CONTEXT = " We want to discuss the engineering projects with our infrastructure team."


@pytest.mark.parametrize("first_action,first_object,second_action,second_object", [
    ("review", "project proposal", "send", "project proposal"),
    ("prepare", "project proposal", "send", "project proposal"),
    ("send", "original project proposal", "send", "revised project proposal"),
    ("send", "project proposal", "send", "revised project proposal"),
])
@pytest.mark.parametrize("separate_message", [False, True])
def test_distinct_supported_promises_do_not_reconcile_or_move_each_others_dates(tmp_path, first_action, first_object, second_action, second_object, separate_message):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    first = f"Please {first_action} the {first_object} by Monday"
    second = f"{second_action} the {second_object} by Friday."
    first_title = f"{first_action.title()} {first_object}"
    second_title = f"{second_action.title()} {second_object}"
    if separate_message:
        service, result = run(people, pid, [candidate(first + ".", title=first_title)], TaskMail(first + "." + CONTEXT))
        assert result["status"] == "analyzed"
        people.file_inbound_reply(pid, message_id="reply-2", thread_id="tracked-1", sender="maya@lumen.test", subject="Developer tools", snippet="follow-up", received_at="2026-10-18T13:00:00Z")
        second = "Please " + second
        class DistinctMail(TaskMail):
            def read(self, *, message_id):
                result = super().read(message_id=message_id)
                if message_id == "reply-2":
                    result["body"] = second + CONTEXT
                return result
        service, result = run(people, pid, [candidate(second, source="mail_2", title=second_title)], DistinctMail(first + "." + CONTEXT))
    else:
        service, result = run(people, pid, [candidate(first, title=first_title), candidate(second, title=second_title)], TaskMail(first + " and " + second + CONTEXT))
    assert result["status"] == "analyzed"
    # Acceptance must never use a reconciliation to mutate the other promise.
    for proposal in service.proposals(pid):
        if proposal["target_kind"] == "task_update":
            with pytest.raises(TaskConflict):
                service.resolve(pid, proposal["proposal_id"], action="accept", expected_version=proposal["version"])
    tasks = saved_tasks(people, pid)
    assert {(t["title"], t["due_date"]) for t in tasks} == {(first_title, "2026-10-19"), (second_title, "2026-10-23")}
    assert service.proposals(pid) == []
    assert asyncio.run(service.analyze(pid))["status"] == "unchanged"
    assert len(saved_tasks(PersonStore(tmp_path), pid)) == 2


@pytest.mark.parametrize("wording", [
    "Please send our team the project proposal from last Friday.",
    "Please send us the minutes of the meeting last Friday.",
    "Please review the file named Friday.",
    "Please send our team the project proposal Friday.",
])
def test_bare_topical_and_past_weekdays_never_establish_future_deadline(tmp_path, wording):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    title = "Review file" if "review" in wording else ("Send minutes" if "minutes" in wording else "Send project proposal")
    service, result = run(people, pid, [candidate(wording, title=title)], TaskMail(wording + CONTEXT))
    assert result["status"] == "analyzed"
    assert all(t["due_date"] is None for t in saved_tasks(people, pid))
    assert all(json.loads(p["proposed_value"]).get("due_date") is None for p in service.proposals(pid))


@pytest.mark.parametrize("correction", [
    "Actually, disregard that request; we have the proposal already.",
    "Actually, scratch that request; we have the proposal already.",
    "Ignore that request; we have the proposal already.",
    "Forget that request; the work is already fulfilled.",
    "That request has already been fulfilled.",
    "We already received the proposal, so no further work is needed.",
])
def test_surrounding_retraction_blocks_automatic_creation_and_date(tmp_path, correction):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    wording = "Please send our team the project proposal by Friday."
    service, result = run(people, pid, [candidate(wording)], TaskMail(wording + " " + correction + CONTEXT))
    assert result["status"] == "analyzed"
    assert saved_tasks(people, pid) == []
    proposals = service.proposals(pid)
    assert len(proposals) == 1 and json.loads(proposals[0]["proposed_value"])["due_date"] is None


def test_ambiguous_human_paraphrase_hint_cannot_accept_a_date_change_without_review(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    human = TaskService(people).create(pid, title="Prepare material for Maya", due_date="2026-11-01", actor="director", timezone="America/Los_Angeles", operation_id="ambiguous-hint-human")["task"]
    service, _ = run(people, pid, [candidate(task_id=human["task_id"])])
    proposal = service.proposals(pid)[0]
    assert proposal["target_kind"] == "task_update" and proposal["target_id"] == human["task_id"]
    with pytest.raises(TaskConflict):
        service.resolve(pid, proposal["proposal_id"], action="accept", expected_version=1)
    assert saved_tasks(people, pid)[0]["due_date"] == "2026-11-01"
    assert service.proposals(pid)[0]["state"] == "pending"


def test_accepted_distinct_task_proposal_does_not_conflict_with_same_object_other_action(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    existing = TaskService(people).create(pid, title="Review project proposal", due_date="2026-11-01", actor="director", timezone="America/Los_Angeles", operation_id="review-before-send")["task"]
    wording = "Please send the project proposal by Friday."
    service, _ = run(people, pid, [candidate(wording)], TaskMail(wording + " Actually, review this suggestion before recording the work." + CONTEXT))
    proposal = service.proposals(pid)[0]
    assert proposal["target_kind"] == "task_create"
    service.resolve(pid, proposal["proposal_id"], action="accept", expected_version=1)
    assert {(t["title"], t["due_date"]) for t in saved_tasks(people, pid)} == {("Review project proposal", "2026-11-01"), ("Send project proposal", None)}
    assert TaskService(people).get(pid, existing["task_id"])["version"] == 1


def test_legacy_date_reconciliation_without_request_proof_cannot_mutate_task(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    existing = TaskService(people).create(pid, title="Send project proposal", due_date="2026-11-01", actor="director", timezone="America/Los_Angeles", operation_id="legacy-date-proof")["task"]
    service, _ = run(people, pid)
    proposal = service.proposals(pid)[0]
    with people._conn:
        people._conn.execute("UPDATE crm_proposals SET proposed_value=? WHERE proposal_id=?", (json.dumps({"changes": {"due_date": "2026-10-23"}}), proposal["proposal_id"]))
    with pytest.raises(TaskConflict):
        service.resolve(pid, proposal["proposal_id"], action="accept", expected_version=1)
    assert TaskService(people).get(pid, existing["task_id"])["due_date"] == "2026-11-01"
    assert service.proposals(pid)[0]["state"] == "pending"


@pytest.mark.parametrize("wording,title", [
    ("Please send the proposal discussed on Friday.", "Send proposal"),
    ("Please send the minutes from our meeting held on Friday.", "Send minutes"),
    ('Please review the file titled "By Friday".', "Review file"),
    ("Please send the project proposal on Friday.", "Send project proposal"),
])
def test_discussion_dates_and_object_title_literals_do_not_authorize_deadlines(tmp_path, wording, title):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    service, result = run(people, pid, [candidate(wording, title=title)], TaskMail(wording + CONTEXT))
    assert result["status"] == "analyzed"
    assert saved_tasks(people, pid) == []
    proposal = service.proposals(pid)[0]
    assert proposal["target_kind"] == "task_create"
    assert json.loads(proposal["proposed_value"])["due_date"] is None


@pytest.mark.parametrize("clause,due", [
    ("by Friday", "2026-10-23"),
    ("by 2026-10-23", "2026-10-23"),
    ("due Friday", "2026-10-23"),
    ("due on Friday", "2026-10-23"),
    ("today", "2026-10-18"),
    ("tomorrow", "2026-10-19"),
])
def test_supported_explicit_deadline_clauses_remain_grounded(tmp_path, clause, due):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    wording = f"Please send the project proposal {clause}."
    service, result = run(people, pid, [candidate(wording)], TaskMail(wording + CONTEXT))
    assert result["status"] == "analyzed"
    assert saved_tasks(people, pid)[0]["due_date"] == due
    assert service.proposals(pid) == []
