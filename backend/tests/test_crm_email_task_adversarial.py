"""Adversarial review reproductions against the real email/task pipeline."""
import asyncio
import json

import pytest

from coworker.crm_tasks import TaskConflict, TaskService
from coworker.people import PersonStore
from test_crm_analysis import tracked_person
from test_crm_email_tasks import TaskMail, add_unrelated_mail, candidate, run, saved_tasks

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


@pytest.mark.parametrize("wording,title", [
    ("Please send the project proposal that was due Friday.", "Send project proposal"),
    ("Please review the proposal we sent by Friday.", "Review proposal"),
    ("Please send the proposal that Maya will review by Friday.", "Send proposal"),
    ("Please send the project proposal which was due 2026-10-16.", "Send project proposal"),
    ("Please review the project proposal our team prepared by 2026-10-16.", "Review project proposal"),
    ("Please send the proposal Maya reviews tomorrow.", "Send proposal"),
    ("Please review the proposal to be sent by Friday.", "Review proposal"),
    ("Please send the proposal after Maya reviews it by Friday.", "Send proposal"),
    ('Please review the file named "Due Friday".', "Review file"),
    ('Please review the file named "2026-10-23".', "Review file"),
    ('Please send the proposal titled "Today".', "Send proposal"),
    ('Please review the "By Friday" project proposal.', "Review project proposal"),
])
def test_embedded_past_and_literal_dates_need_review_of_the_requested_action(tmp_path, wording, title):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    service, result = run(people, pid, [candidate(wording, title=title)], TaskMail(wording + CONTEXT))
    assert result["status"] == "analyzed"
    assert saved_tasks(people, pid) == []
    proposal = service.proposals(pid)[0]
    assert proposal["target_kind"] == "task_create"
    assert json.loads(proposal["proposed_value"])["due_date"] is None
    assert proposal["source_refs"]


@pytest.mark.parametrize("suffix", [
    "That request is cancelled.",
    "That request is canceled.",
    "No need to send it; we received a copy.",
    "Wait, hold off on that request until we finish our discussion.",
    "You can wait for our approval before sending it.",
    "We withdraw the request.",
    "The request is conditional on budget approval.",
    "The proposal is unnecessary now.",
])
def test_unsupported_surrounding_request_context_cannot_authorize_work(tmp_path, suffix):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    request = "Please send the project proposal by Friday."
    service, result = run(people, pid, [candidate(request)], TaskMail(request + " " + suffix + CONTEXT))
    assert result["status"] == "analyzed"
    assert saved_tasks(people, pid) == []
    assert json.loads(service.proposals(pid)[0]["proposed_value"])["due_date"] is None


@pytest.mark.parametrize("wording", [
    "Please send the project proposal when we approve the budget by Friday.",
    "Please send the project proposal once the budget is approved by Friday.",
    "Please send no project proposal by Friday.",
    "Please send No project proposal by Friday.",
    "Please send neither project proposal by Friday.",
    "Please send Neither project proposal by Friday.",
    "Please send the project proposal provided the budget clears by Friday.",
    "Please send the project proposal only upon approval tomorrow.",
])
def test_negative_and_conditional_objects_are_outside_positive_request_grammar(tmp_path, wording):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    service, result = run(people, pid, [candidate(wording)], TaskMail(wording + CONTEXT))
    assert result["status"] == "analyzed"
    assert saved_tasks(people, pid) == []
    assert json.loads(service.proposals(pid)[0]["proposed_value"])["due_date"] is None


@pytest.mark.parametrize("resolution", ["complete", "cancel", "undo"])
@pytest.mark.parametrize("new_message", [True, False])
@pytest.mark.parametrize("project", ["Alpha", "Beta"])
def test_new_uncertain_obligation_after_finished_work_remains_reviewable(tmp_path, resolution, new_message, project):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    alpha = "Please send the Alpha project proposal by Friday."
    beta = f"Please send the {project} project proposal by Friday."
    service, result = run(people, pid, [candidate(alpha, title="Send Alpha project proposal")], TaskMail(alpha + CONTEXT))
    assert result["status"] == "analyzed"
    task = saved_tasks(people, pid)[0]
    tasks = TaskService(people)
    if resolution == "undo":
        tasks.undo(pid, tasks.changes(task["task_id"])[0]["change_id"], actor="director", operation_id="alpha-undo", expected_version=1)
    else:
        tasks.update(pid, task["task_id"], changes={"state": "completed" if resolution == "complete" else "cancelled"}, actor="director", timezone="America/Los_Angeles", operation_id="alpha-finish", expected_version=1)
    finished = tasks.get(pid, task["task_id"])
    if new_message:
        people.file_inbound_reply(pid, message_id="reply-2", thread_id="tracked-1", sender="maya@lumen.test", subject="Developer tools", snippet="new deliverable", received_at="2026-10-18T13:00:00Z")
    beta_body = (alpha + CONTEXT if project == "Alpha" and new_message else
        beta + f" This is a separate deliverable for the {project} project and our engineering team.")
    class NewObligationMail(TaskMail):
        def read(self, *, message_id):
            result = super().read(message_id=message_id)
            if message_id == ("reply-2" if new_message else "reply-1"):
                result["body"] = beta_body
            return result
    restarted = PersonStore(tmp_path)
    service, result = run(restarted, pid, [candidate(beta, title=f"Send {project} project proposal", source="mail_2" if new_message else "mail_1")], NewObligationMail(alpha + CONTEXT))
    assert result["status"] == "analyzed"
    assert saved_tasks(restarted, pid) == []
    proposal = service.proposals(pid)[0]
    assert proposal["target_kind"] == "task_update" and proposal["target_id"] == task["task_id"]
    assert json.loads(proposal["proposed_value"])["requires_target_review"] is True
    with pytest.raises(TaskConflict):
        service.resolve(pid, proposal["proposal_id"], action="accept", expected_version=1)
    assert TaskService(restarted).get(pid, task["task_id"]) == finished
    assert service.proposals(pid)[0]["state"] == "pending"
    assert asyncio.run(service.analyze(pid))["status"] == "unchanged"
    assert len(service.proposals(pid)) == 1


def test_distinct_request_span_in_unchanged_message_is_not_finished_work(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    alpha = "Please send the Alpha project proposal."
    beta = "Please send the Beta project proposal."
    body = alpha + " " + beta + CONTEXT
    run(people, pid, [candidate(alpha, title="Send Alpha project proposal")], TaskMail(body))
    task = saved_tasks(people, pid)[0]
    TaskService(people).update(pid, task["task_id"], changes={"state": "completed"}, actor="director", timezone="America/Los_Angeles", operation_id="alpha-span-finished", expected_version=1)
    add_unrelated_mail(people, pid)
    class SameMessageMail(TaskMail):
        def read(self, *, message_id):
            result = super().read(message_id=message_id)
            if message_id == "aaa-unrelated":
                result["body"] = "Our infrastructure team had a productive discussion about the student engineering projects and learned a great deal."
            return result
    restarted = PersonStore(tmp_path)
    service, result = run(restarted, pid, [candidate(beta, title="Send Beta project proposal", source="mail_2")], SameMessageMail(body))
    assert result["status"] == "analyzed"
    proposal = service.proposals(pid)[0]
    assert json.loads(proposal["proposed_value"])["requires_target_review"] is True
    assert TaskService(restarted).get(pid, task["task_id"])["state"] == "completed"
    evidence = restarted._conn.execute("SELECT message_id, body_fingerprint, request_start FROM crm_task_evidence ORDER BY rowid").fetchall()
    assert len(evidence) == 2
    assert evidence[0]["message_id"] == evidence[1]["message_id"] == "reply-1"
    assert evidence[0]["body_fingerprint"] == evidence[1]["body_fingerprint"]
    assert evidence[0]["request_start"] != evidence[1]["request_start"]


@pytest.mark.parametrize("new_message", [True, False])
def test_dismissed_support_suppresses_only_unchanged_request_after_restart(tmp_path, new_message):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    alpha = "Please send the Alpha project proposal by Friday."
    alpha_body = alpha + " We might need to revise this request." + CONTEXT
    service, _ = run(people, pid, [candidate(alpha, title="Send Alpha project proposal")], TaskMail(alpha_body))
    dismissed = service.proposals(pid)[0]
    service.resolve(pid, dismissed["proposal_id"], action="dismiss", expected_version=1)
    if new_message:
        people.file_inbound_reply(pid, message_id="reply-2", thread_id="tracked-1", sender="maya@lumen.test", subject="Developer tools", snippet="new deliverable", received_at="2026-10-18T13:00:00Z")
    beta = "Please send the Beta project proposal by Friday."
    class NewObligationMail(TaskMail):
        def read(self, *, message_id):
            result = super().read(message_id=message_id)
            if message_id == ("reply-2" if new_message else "reply-1"):
                result["body"] = beta + CONTEXT
            return result
    restarted = PersonStore(tmp_path)
    service, result = run(restarted, pid, [candidate(beta, title="Send Beta project proposal", source="mail_2" if new_message else "mail_1")], NewObligationMail(alpha_body))
    assert result["status"] == "analyzed"
    assert len(saved_tasks(restarted, pid)) + len(service.proposals(pid)) == 1
    assert service.proposals(pid, state="dismissed")[0]["proposal_id"] == dismissed["proposal_id"]
