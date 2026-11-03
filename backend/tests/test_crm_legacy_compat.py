"""Legacy attention decisions survive the schema upgrade only for unchanged mail."""
import asyncio
import json
import re

import pytest

from coworker import migrations
from coworker.crm_mail_repository import MailRepository, source_ref
from coworker.crm_tasks import TaskService, _fingerprint
from coworker.people import PersonStore
from test_crm_analysis import worker, tracked_person
from test_crm_email_tasks import BODY, TaskMail, TaskModel, candidate


def legacy_store(tmp_path, *, kind, state):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    mail = TaskMail()
    repo = MailRepository(people)
    evidence = worker(people, TaskModel(), mail)._collect(pid)
    legacy_sources, fingerprints = [], {}
    for support in evidence.manifest:
        mid = support['message_id']
        meta, full = mail.inbound_message(message_id=mid), mail.read(message_id=mid)
        repo.save_message(pid, meta, direction=support['direction'], account='director@club.test',
            content={'body': full['body'], 'body_state': 'present'})
        row = repo.message('director@club.test', mid)
        fingerprints[mid] = row['fingerprint']
        legacy_sources.append({**source_ref(mid, 'tracked-1', 'director@club.test'),
            'account': 'director@club.test', 'evidence_fingerprint': row['fingerprint']})
    value = {'title': 'Send project proposal', 'details': candidate()['request'], 'due_date': '2026-10-23'}
    intent = {'intent': sorted(set(re.findall(r'[a-z0-9]+',value['title'].lower())) -
        {'send','share','email','provide','the','a','an','to','for','please','me','us'})}
    if kind == 'description':
        value = {'slot': 'general', 'text': 'Legacy general suggestion'}
        intent = {'slot': 'general'}
    fp = _fingerprint({'kind': kind, 'intent': intent, 'message': None if kind=='description' else 'reply-1',
        'evidence': sorted(fingerprints.values()) if kind=='description' else fingerprints['reply-1']})
    change_id = None
    if state == 'undone' and kind == 'task_create':
        created = TaskService(people).create(pid, title=value['title'], timezone='UTC', actor='director',
            operation_id='legacy-created-task')
        change_id = created['change']['change_id']
        TaskService(people).update(pid, created['task']['task_id'], changes={'state':'cancelled'},
            expected_version=1, timezone='UTC', actor='director', operation_id='legacy-cancel-task')
    people._conn.execute('DROP TABLE crm_task_evidence')
    people._conn.execute('DROP TABLE crm_proposals')
    from coworker.crm_schema import CRM_SCHEMA
    for ddl in CRM_SCHEMA:
        people._conn.execute(ddl)
    if state != 'undone':
        sources = legacy_sources if kind=='description' else [r for r in legacy_sources if r['message_id']=='reply-1']
        people._conn.execute("INSERT INTO crm_proposals VALUES('legacy-proposal',?,?,?,?,?,?,?,?,?,?)",
            (pid,kind,json.dumps(value),0,'Preserve review',json.dumps(sources),fp,state,'2026-10-22','2026-10-22'))
    people._conn.execute('INSERT INTO crm_interpretations VALUES(?,?,?,?)',
        (pid,fp,change_id,int(state=='undone')))
    people._conn.execute('PRAGMA user_version=7')
    people._conn.commit(); people._conn.close()
    result = migrations.apply_migrations(tmp_path)
    assert result.error is None, result
    return PersonStore(tmp_path), pid, mail


@pytest.mark.parametrize('kind', ['task_create','description'])
@pytest.mark.parametrize('state', ['pending','dismissed','undone'])
def test_upgrade_keeps_unchanged_legacy_decision_without_duplicate(tmp_path, kind, state):
    people,pid,mail = legacy_store(tmp_path,kind=kind,state=state)
    model = TaskModel(tasks=[candidate()] if kind=='task_create' else [])
    # Detailed description is unrelated work; only general has an old decision.
    service = worker(people,model,mail)
    previous = [r[0] for r in people._conn.execute("SELECT proposal_id FROM crm_proposals WHERE person_id=? AND " +
        ("target_kind='description' AND target_slot='general'" if kind=='description' else "target_kind IN ('task_create','task_update')"),(pid,))]
    result = asyncio.run(service.analyze(pid))
    assert result['status']=='analyzed',result
    rows = people._conn.execute("SELECT * FROM crm_proposals WHERE person_id=? AND " +
        ("target_kind='description' AND target_slot='general'" if kind=='description' else "target_kind IN ('task_create','task_update')"),(pid,)).fetchall()
    if state=='pending':
        assert [r['proposal_id'] for r in rows] == ['legacy-proposal']
        assert 'legacy-proposal' in result['proposal_ids']
    elif state=='dismissed':
        assert [r['proposal_id'] for r in rows] == ['legacy-proposal']
    else:
        assert [r['proposal_id'] for r in rows]==previous
    if kind=='task_create':
        assert TaskService(people).list(person_id=pid,timezone='UTC')['tasks']==[]
    else:
        assert people.descriptions(pid)['general']['text'] is None


@pytest.mark.parametrize('kind', ['task_create','description'])
def test_changed_body_is_new_evidence_after_legacy_dismissal(tmp_path, kind):
    people,pid,mail=legacy_store(tmp_path,kind=kind,state='dismissed')
    mail.body=BODY+' This is a new explicit message revision and a renewed request.'
    result=asyncio.run(worker(people,TaskModel(tasks=[candidate()] if kind=='task_create' else []),mail).analyze(pid))
    assert result['status']=='analyzed',result
    if kind=='task_create':
        assert TaskService(people).list(person_id=pid,timezone='UTC')['tasks'] or people._conn.execute("SELECT count(*) FROM crm_proposals WHERE person_id=? AND target_kind IN ('task_create','task_update')",(pid,)).fetchone()[0]>1
    else:
        assert people.descriptions(pid)['general']['text']


@pytest.mark.parametrize('kind', ['task_create','description'])
@pytest.mark.parametrize('invalid_saved', ['account','partial','missing','changed-fingerprint'])
def test_legacy_decision_needs_exact_full_saved_mail_identity(tmp_path, kind, invalid_saved):
    people,pid,mail=legacy_store(tmp_path,kind=kind,state='dismissed')
    with people.crm_transaction():
        if invalid_saved=='account':
            people._conn.execute("UPDATE gmail_messages SET account='other@club.test' WHERE message_id='reply-1'")
        elif invalid_saved=='partial':
            people._conn.execute("UPDATE gmail_messages SET body_state='partial' WHERE message_id='reply-1'")
        elif invalid_saved=='missing':
            people._conn.execute("DELETE FROM gmail_messages WHERE message_id='reply-1'")
        else:
            people._conn.execute("UPDATE gmail_messages SET fingerprint='new-fingerprint' WHERE message_id='reply-1'")
    service=worker(people,TaskModel(tasks=[candidate()] if kind=='task_create' else []),mail)
    result=asyncio.run(service.analyze(pid))
    assert result['status']=='analyzed',result
    if kind=='description':
        assert people.descriptions(pid)['general']['text']
    else:
        assert TaskService(people).list(person_id=pid,timezone='UTC')['tasks'] or people._conn.execute("SELECT count(*) FROM crm_proposals WHERE person_id=? AND target_kind IN ('task_create','task_update')",(pid,)).fetchone()[0]>1


def test_public_alias_cannot_reactivate_legacy_private_evidence(tmp_path):
    people,pid,mail=legacy_store(tmp_path,kind='task_create',state='dismissed')
    people.upsert_attachment(pid,record_type='source_ref',fields={
        'id':'gmail:reply-1','message_id':'reply-1','account':'director@club.test','sensitivity':'restricted'},
        idempotency_key='private-legacy-alias',actor='director',rationale_summary='Private source')
    model=TaskModel()
    result=asyncio.run(worker(people,model,mail).analyze(pid))
    assert result['status']=='partial' and result['failure_code']=='private_source',result
    assert model.calls==[]
    assert TaskService(people).list(person_id=pid,timezone='UTC')['tasks']==[]
