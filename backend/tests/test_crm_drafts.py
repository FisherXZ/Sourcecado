import asyncio
import json

from coworker.crm_drafts import prepare_task_draft
from coworker.crm_tasks import TaskService
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
