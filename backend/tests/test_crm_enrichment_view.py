"""Read-only enrichment review/results for a single saved person."""
import json
import pytest
from fastapi.testclient import TestClient
from coworker.apollo import FakeHttp
from coworker.server import TOKEN_HEADER, create_app

HEADERS = {TOKEN_HEADER: 'enrichment-view-test'}

def setup(tmp_path, *, configured=True, match=True):
    http=FakeHttp()
    app=create_app(token=HEADERS[TOKEN_HEADER],state=tmp_path,provider=None,http=http,apollo_key='fixture-key' if configured else None)
    p=app.state.people.keep_from_apollo(apollo_id='maya' if match else None,first_name='Maya',last_name_obfuscated='Pa***' if not match else 'Patel',title='Lead',company='Example Labs')
    return app,p['person_id'],http

def read(app,pid):
    return TestClient(app).get(f'/v1/people/{pid}/enrichment',headers=HEADERS)

def approval(app,pid,*,item_id='enrich-one',**extra):
    resource={'kind':'apollo_enrichment','authority_version':2,'person_id':pid,'person':'Maya Patel','title':'Lead','company':'Example Labs','match':{'apollo_id':'maya'},'matched_on':'apollo_id','identity_revision':app.state.people.get(pid)['identity_revision'],'credits_estimated':None,'reason':'One selected lookup; cost unknown'}
    resource.update(extra)
    return app.state.inbox.park('apollo_enrich_contact',{'person_id':pid},item_id=item_id,resource=resource)

def test_opening_enrichment_is_local_read_with_honest_unknown_cost(tmp_path):
    app,pid,http=setup(tmp_path)
    r=read(app,pid)
    assert r.status_code==200,r.text
    d=r.json();assert d['status']=='not_enriched' and d['credits_estimated'] is None
    assert d['person']['person_id']==pid and d['known']['email'] is None and d['can_request'] is True
    assert http.calls==[] and app.state.inbox.pending()==[]
    assert app.state.people.get(pid)['sequence_state'] is None

def test_ambiguous_identity_and_unconfigured_connection_stop_before_spending(tmp_path):
    app,pid,http=setup(tmp_path/'ambiguous',match=False)
    d=read(app,pid).json();assert d['can_request'] is False and d['problem']=='no_match_key'
    assert http.calls==[]
    app,pid,http=setup(tmp_path/'missing',configured=False)
    d=read(app,pid).json();assert d['can_request'] is False and d['problem']=='apollo_not_configured'

def test_read_requires_local_auth_and_live_person(tmp_path):
    app,pid,_=setup(tmp_path)
    c=TestClient(app)
    assert c.get(f'/v1/people/{pid}/enrichment').status_code==401
    assert read(app,'missing').status_code==404


def test_pending_approval_is_same_after_restart_and_cost_never_invented(tmp_path):
    app,pid,http=setup(tmp_path)
    approval(app,pid)
    data=read(app,pid).json()
    assert data['status']=='awaiting_approval' and data['pending']['id']=='enrich-one'
    assert data['pending']['match']=={'apollo_id':'maya'} and data['can_request'] is False
    restarted=create_app(token=HEADERS[TOKEN_HEADER],state=tmp_path,provider=None,http=http,apollo_key='fixture-key')
    assert read(restarted,pid).json()['pending']['id']=='enrich-one'
    assert http.calls==[]


@pytest.mark.parametrize('state',['found','partial','no_match','failed','insufficient_credit','outcome_unknown'])
def test_saved_terminal_states_and_reported_cost_are_projected_safely(tmp_path,state):
    app,pid,http=setup(tmp_path);approval(app,pid)
    # Exercise the real durable execution-claim and completion store.
    claim=app.state.inbox.decide_and_claim('enrich-one','allow',actor='director',scope='once',claimant='fixture-claim')
    assert claim.claimed
    app.state.inbox.complete_execution('enrich-one',claimant='fixture-claim',ok=state in {'found','partial','no_match'},result={'person_id':pid,'approval_id':'enrich-one','status':state,'provider':'apollo','fetched_at':'2026-11-02T12:00:00Z','credits_actual':2,'fields_found':['email'],'fields_applied':['email'],'error':'do-not-expose-provider-secret'})
    data=read(app,pid).json()
    assert data['status']==state and data['latest']['credits_actual']==2
    assert 'do-not-expose-provider-secret' not in json.dumps(data)
    assert data['can_request'] is (state!='outcome_unknown')
    assert http.calls==[]


def test_other_person_approval_never_appears_and_stale_identity_needs_new_review(tmp_path):
    app,pid,_=setup(tmp_path)
    other=app.state.people.keep_from_apollo(apollo_id='noor',first_name='Noor',last_name_obfuscated='Chen',title=None,company=None)['person_id']
    approval(app,other,item_id='other-approval')
    assert read(app,pid).json()['status']=='not_enriched'
    approval(app,pid,identity_revision=-1)
    assert read(app,pid).json()['pending']['stale_identity'] is True


def test_non_numeric_cost_is_unknown_not_zero(tmp_path):
    app,pid,_=setup(tmp_path)
    approval(app,pid,credits_estimated=True)
    assert read(app,pid).json()['pending']['credits_estimated'] is None


def test_a_reviewed_new_result_does_not_hide_the_older_unknown_history(tmp_path):
    app,pid,_=setup(tmp_path)
    for item_id,status in [('older-unknown','outcome_unknown'),('new-result','found')]:
        approval(app,pid,item_id=item_id)
        app.state.inbox.decide_and_claim(item_id,'allow',actor='director',scope='once',claimant=item_id)
        app.state.inbox.complete_execution(item_id,claimant=item_id,ok=status=='found',result={'status':status,'person_id':pid,'credits_actual':None})
    result=read(app,pid).json()
    assert result['status']=='found' and result['latest']['id']=='new-result'
    assert [item['status'] for item in result['attempts']]==['found','outcome_unknown']
