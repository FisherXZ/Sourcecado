import asyncio
import json

import pytest

from coworker.crm_drafts import prepare_task_draft
from coworker.crm_tasks import TaskService
from coworker.crm_tasks import TaskError
from coworker.people import PersonStore
from coworker.provider import FakeProvider


def test_preparation_is_durable_local_and_retry_safe_without_email(tmp_path):
    people = PersonStore(tmp_path)
    pid = people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")["person_id"]
    task = TaskService(people).create(pid, title="Send examples", timezone="UTC", actor="director", operation_id="draft-test-task")["task"]
    provider = FakeProvider(deltas=(json.dumps({"subject": "Project examples", "body": "Hi Maya, here are the examples for review."}),))
    args = dict(people=people, provider=provider, person_id=pid, task_id=task["task_id"], expected_version=1, operation_id="draft-prepare-test")
    first = asyncio.run(prepare_task_draft(**args))
    assert first["artifact"]["local_only"]
    assert not first["artifact"]["sent"]
    assert "Hi Maya" in first["artifact"]["body"]
    assert asyncio.run(prepare_task_draft(**args))["replayed"]
    assert len(provider.calls) == 1
    assert TaskService(people).get(pid, task["task_id"])["state"] == "open"
    assert people.get(pid)["sequence_state"] is None
    assert PersonStore(tmp_path).get(pid, expand_sources=True)["artifacts"]


def test_suggestion_preparation_does_not_accept_or_create_a_task(tmp_path):
    from coworker.crm_service import CrmService
    people = PersonStore(tmp_path)
    pid = people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")["person_id"]
    proposal = CrmService(people).propose(pid, kind="task_create", value={"title": "Maybe reconnect"}, expected_version=0, sources=[], fingerprint="maybe", reason="Review timing")
    provider = FakeProvider(deltas=(json.dumps({"subject": "Checking in", "body": "Hi Maya, would you like to reconnect?"}),))
    prepared = asyncio.run(prepare_task_draft(people=people, provider=provider, person_id=pid, task_id=proposal["proposal_id"],
        expected_version=0, operation_id="prepare-suggestion", proposal=True))
    assert prepared["artifact"]["proposal_id"] == proposal["proposal_id"]
    assert prepared["artifact"]["task_id"] is None
    assert CrmService(people).proposals(pid)[0]["state"] == "pending"
    assert TaskService(people).list(timezone="UTC")["tasks"] == []


def test_draft_uses_task_conversation_instead_of_newer_unrelated_thread(tmp_path):
    from coworker.crm_mail_repository import MailRepository, source_ref
    people = PersonStore(tmp_path)
    pid = people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")["person_id"]
    mail = MailRepository(people)
    mail.save_message(pid, {"id": "request", "thread_id": "examples"}, direction="inbound", account="director@example.com",
        content={"body": "Please send the project examples.", "body_state": "present"})
    task = TaskService(people).create(pid, title="Send examples", timezone="UTC", actor="assistant", operation_id="sourced-task",
        source_refs=({**source_ref("request", "examples", "director@example.com"), "account": "director@example.com"},))["task"]
    mail.save_message(pid, {"id": "other", "thread_id": "unrelated"}, direction="inbound", account="director@example.com",
        content={"body": "UNRELATED PRIVATE TALKING POINT", "body_state": "present"})
    provider = FakeProvider(deltas=(json.dumps({"subject": "Examples", "body": "Hi Maya, [review needed: add examples]."}),))
    result = asyncio.run(prepare_task_draft(people=people, provider=provider, person_id=pid, task_id=task["task_id"],
        expected_version=1, operation_id="prepare-correct-thread"))
    assert result["artifact"]["thread_id"] == "examples"
    supplied = json.dumps(provider.calls)
    assert "Please send the project examples" in supplied
    assert "UNRELATED PRIVATE TALKING POINT" not in supplied


def test_ambiguous_source_threads_stay_local_with_explicit_review_warning(tmp_path):
    from coworker.crm_mail_repository import MailRepository, source_ref
    people = PersonStore(tmp_path)
    pid = people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")["person_id"]
    sources = []
    for thread in ("one", "two"):
        MailRepository(people).save_message(pid, {"id": thread, "thread_id": thread}, direction="inbound", account="director@example.com",
            content={"body": "PRIVATE THREAD " + thread, "body_state": "present"})
        sources.append({**source_ref(thread, thread, "director@example.com"), "account": "director@example.com"})
    task = TaskService(people).create(pid, title="Reconnect", timezone="UTC", actor="assistant", operation_id="ambiguous-task", source_refs=tuple(sources))["task"]
    provider = FakeProvider(deltas=(json.dumps({"subject": "Checking in", "body": "[review needed]"}),))
    result = asyncio.run(prepare_task_draft(people=people, provider=provider, person_id=pid, task_id=task["task_id"],
        expected_version=1, operation_id="ambiguous-draft"))
    assert result["artifact"]["thread_id"] is None
    assert result["artifact"]["thread_review_required"] is True
    assert "PRIVATE THREAD" not in json.dumps(provider.calls)


def test_provider_failure_is_bounded_and_keeps_task(tmp_path):
    people = PersonStore(tmp_path)
    pid = people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")["person_id"]
    task = TaskService(people).create(pid, title="Reconnect", timezone="UTC", actor="director", operation_id="failure-task")["task"]
    class BrokenProvider:
        async def astream(self, **kwargs):
            raise RuntimeError("private provider response")
            yield
    with pytest.raises(TaskError, match="Couldn’t prepare draft text") as failure:
        asyncio.run(prepare_task_draft(people=people, provider=BrokenProvider(), person_id=pid, task_id=task["task_id"],
            expected_version=1, operation_id="failed-draft"))
    assert "private provider response" not in str(failure.value)
    assert TaskService(people).get(pid, task["task_id"])["state"] == "open"
    assert not people.get(pid, expand_sources=True)["artifacts"]
