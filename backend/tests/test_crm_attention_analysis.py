"""The unified worker and Contacts use the same saved work and scoped Undo.

Worker policy matrices remain in test_crm_analysis/test_crm_email_task_adversarial;
these tests retain the former attention worker's end-to-end guarantees through
its replacement, rather than keeping a second inference protocol alive.
"""
import asyncio
import json

import pytest

from coworker.crm_service import CrmService
from coworker.crm_tasks import TaskService
from coworker.people import PersonStore
from test_crm_analysis import DescriptionModel, ReadOnlyMail, tracked_person, worker
from test_crm_email_tasks import TaskMail, TaskModel, candidate, run, saved_tasks


def example(tmp_path):
    people = PersonStore(tmp_path)
    return people, tracked_person(people)


def test_owned_request_is_the_same_task_in_contacts_and_survives_restart(tmp_path):
    people, pid = example(tmp_path)
    service, result = run(people, pid)
    assert result['status'] == 'analyzed'
    task = saved_tasks(people, pid)[0]
    contacts = CrmService(people).contacts(timezone='America/Los_Angeles', include_saved=True)['contacts']
    assert next(p for p in contacts if p['person_id'] == pid)['next_action']['task_id'] == task['task_id']
    restarted = PersonStore(tmp_path)
    model = TaskModel()
    assert asyncio.run(worker(restarted, model, TaskMail()).analyze(pid))['status'] == 'unchanged'
    assert model.calls == []
    assert saved_tasks(restarted, pid)[0]['task_id'] == task['task_id']
    assert task['origin'] == 'assistant' and task['source_refs']


@pytest.mark.parametrize('wording', [
    'It would be nice to see the project proposal sometime.',
    'If approved, please send our team the project proposal by Friday.',
    'Please do not send our team the project proposal by Friday.',
])
def test_uncertain_requests_remain_review_suggestions_in_contacts(tmp_path, wording):
    people, pid = example(tmp_path)
    run(people, pid, [candidate(wording)], TaskMail(wording + ' We want to discuss student engineering projects.'))
    assert saved_tasks(people, pid) == []
    proposals = CrmService(people).proposals(pid)
    assert any(p['kind'] == 'task_create' and p['value']['due_date'] is None for p in proposals)


def test_human_blank_and_dismissal_survive_refresh_and_restart(tmp_path):
    people, pid = example(tmp_path)
    crm = CrmService(people)
    crm.save_description(pid, 'general', text='', expected_version=0, operation_id='human-blank')
    asyncio.run(worker(people).analyze(pid))
    assert people.descriptions(pid)['general']['text'] == ''
    suggestion = next(p for p in crm.proposals(pid) if p['kind'] == 'description')
    crm.resolve(pid, suggestion['proposal_id'], decision='dismissed', operation_id='dismiss-human-blank', timezone='UTC')
    reopened = PersonStore(tmp_path)
    asyncio.run(worker(reopened).analyze(pid, retry=True))
    assert CrmService(reopened).proposals(pid) == []
    assert reopened.descriptions(pid)['general']['text'] == ''


def test_undo_automatic_task_keeps_other_promises_and_suppresses_recreation(tmp_path):
    people, pid = example(tmp_path)
    run(people, pid)
    crm = CrmService(people)
    automatic = saved_tasks(people, pid)[0]
    human = crm.tasks.create(pid, title='Separate human promise', timezone='UTC', actor='director', operation_id='unrelated-human')['task']
    change = crm.tasks.changes(automatic['task_id'])[0]
    crm.undo(pid, change['change_id'], operation_id='undo-inferred')
    assert crm.tasks.get(pid, automatic['task_id'])['state'] == 'cancelled'
    run(PersonStore(tmp_path), pid)
    assert [t['task_id'] for t in saved_tasks(people, pid)] == [human['task_id']]
    assert crm.proposals(pid) == []


def test_model_failures_keep_evidence_and_require_deliberate_retry(tmp_path):
    people, pid = example(tmp_path)
    model = DescriptionModel(fail=True)
    service = worker(people, model)
    for index in range(3):
        asyncio.run(service.analyze(pid, now=10**12 + index * 2000))
    assert asyncio.run(service.analyze(pid, now=10**12 + 8000))['status'] == 'retry_required'
    assert len(model.calls) == 3
    assert service.state(pid)['analyzed_revision'] == 0
    assert any(e['payload'].get('message_id') == 'reply-1' for e in people.timeline(pid))
    assert saved_tasks(people, pid) == []
    assert 'sk-never-persist-this' not in json.dumps(service.state(pid))
    assert asyncio.run(worker(PersonStore(tmp_path)).analyze(pid, retry=True))['status'] == 'analyzed'


def test_cancelled_analysis_restarts_without_duplicate_work(tmp_path):
    people, pid = example(tmp_path)
    started = asyncio.Event()
    class SlowModel(DescriptionModel):
        async def astream(self, **kwargs):
            started.set()
            await asyncio.Event().wait()
            yield
    service = worker(people, SlowModel())
    async def cancel():
        task = asyncio.create_task(service.analyze(pid))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancel())
    assert service.state(pid)['failure_code'] == 'cancelled'
    reopened = PersonStore(tmp_path)
    assert asyncio.run(worker(reopened).analyze(pid, retry=True))['status'] == 'analyzed'
    assert not saved_tasks(reopened, pid)


def test_manual_collection_shares_refresh_without_waiting_for_analysis():
    from coworker.crm_maintenance import RefreshCoordinator
    calls = []
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        class SlowAnalysis:
            async def run_pending(self):
                started.set()
                await release.wait()
        def collect():
            calls.append("collection")
            return {"status": "ok", "filed": 2}
        coordinator = RefreshCoordinator(collect, SlowAnalysis())
        full = coordinator.request()
        result = await asyncio.wait_for(coordinator.collection_result(), timeout=2)
        await started.wait()
        assert result == {"status": "ok", "filed": 2}
        assert not full.done()
        assert coordinator.request() is full
        release.set()
        await full
    asyncio.run(scenario())
    assert calls == ["collection"]


def test_refresh_requests_share_collection_and_analysis():
    from coworker.crm_maintenance import RefreshCoordinator
    collected, analyzed = [], []
    class Analysis:
        async def run_pending(self):
            analyzed.append("read-only analysis")
    def collect():
        collected.append("read-only collection")
    async def scenario():
        coordinator = RefreshCoordinator(collect, Analysis())
        first, second = coordinator.request(), coordinator.request()
        assert first is second
        await asyncio.gather(first, second)
    asyncio.run(scenario())
    assert collected == ["read-only collection"]
    assert analyzed == ["read-only analysis"]


def test_mail_arriving_after_prepare_stays_pending_for_next_maintenance(tmp_path, monkeypatch):
    from coworker.crm_maintenance import AnalysisMaintenance
    people, pid = example(tmp_path)
    service = worker(people)
    assert asyncio.run(service.analyze(pid))['status'] == 'analyzed'
    with people.crm_transaction() as conn:
        conn.execute('UPDATE crm_analysis_state SET latest_evidence_revision=1,observed_collection_revision=0 WHERE person_id=?', (pid,))
    prepare = service._prepare
    def prepare_then_new_mail(person_id):
        prepare(person_id)
        with people.crm_transaction() as conn:
            conn.execute('UPDATE crm_analysis_state SET latest_evidence_revision=latest_evidence_revision+1 WHERE person_id=?', (person_id,))
    monkeypatch.setattr(service, '_prepare', prepare_then_new_mail)
    asyncio.run(AnalysisMaintenance(people, lambda: service).run_pending())
    state = people._conn.execute('SELECT latest_evidence_revision,observed_collection_revision FROM crm_analysis_state WHERE person_id=?', (pid,)).fetchone()
    assert tuple(state) == (2, 1)
