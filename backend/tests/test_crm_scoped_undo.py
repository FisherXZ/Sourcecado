import pytest
from coworker.people import PersonStore
from coworker.crm_service import CrmService
from coworker.crm_tasks import TaskConflict,TaskService

def setup(tmp_path):
 p=PersonStore(tmp_path);pid=p.keep_from_apollo(apollo_id='maya',first_name='Maya',last_name_obfuscated=None,title='Lead',company='Lumen')['person_id'];p.set_sequence(pid,'open',actor='director');return p,pid,CrmService(p)

def test_status_undo_preserves_unrelated_description_and_replays(tmp_path):
 p,pid,c=setup(tmp_path);r=c.set_status(pid,state='done',expected_version=p.get(pid)['version'],operation_id='status-done')
 c.save_description(pid,'general',text='Keep this edit',expected_version=0,operation_id='other-description')
 assert next(v for v in c.recent_changes(pid) if v['change_id']==r['change']['change_id'])['can_undo']
 undo=c.undo(pid,r['change']['change_id'],operation_id='undo-status')
 assert p.get(pid)['sequence_state']=='open' and p.descriptions(pid)['general']['text']=='Keep this edit'
 assert c.undo(pid,r['change']['change_id'],operation_id='undo-status')['replayed']

def test_accepted_creation_undo_cancels_exact_task_and_suppresses_proposal(tmp_path):
 p,pid,c=setup(tmp_path);proposal=c.propose(pid,kind='task_create',value={'title':'Send examples'},expected_version=0,sources=[],fingerprint='request',reason='Review')
 r=c.resolve(pid,proposal['proposal_id'],decision='accepted',operation_id='accept-proposal',timezone='UTC');tid=r['task']['task_id']
 unrelated=TaskService(p).create(pid,title='Other promise',timezone='UTC',actor='director',operation_id='unrelated')['task']
 assert c.recent_changes(pid)[0]['can_undo'] is False # unrelated human creation
 assert next(v for v in c.recent_changes(pid) if v['change_id']==r['change']['change_id'])['can_undo']
 c.undo(pid,r['change']['change_id'],operation_id='undo-accept')
 assert TaskService(p).get(pid,tid)['state']=='cancelled'
 assert TaskService(p).get(pid,unrelated['task_id'])['state']=='open'
 assert p._conn.execute('SELECT state FROM crm_proposals WHERE proposal_id=?',(proposal['proposal_id'],)).fetchone()[0]=='dismissed'
 assert c.undo(pid,r['change']['change_id'],operation_id='undo-accept')['replayed']

def test_accepted_creation_undo_conflicts_with_later_human_edit(tmp_path):
 p,pid,c=setup(tmp_path);proposal=c.propose(pid,kind='task_create',value={'title':'Send examples'},expected_version=0,sources=[],fingerprint='request',reason='Review')
 r=c.resolve(pid,proposal['proposal_id'],decision='accepted',operation_id='accept-proposal',timezone='UTC');task=r['task']
 TaskService(p).update(pid,task['task_id'],changes={'title':'Human corrected'},expected_version=1,timezone='UTC',actor='director',operation_id='edit-task')
 with pytest.raises(TaskConflict):c.undo(pid,r['change']['change_id'],operation_id='undo-accept')
