"""Every consequential entry point needs the same immutable, once-only authority."""
import pytest
from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp, MATCH_URL
from coworker.mcp import FakeMcp
from coworker.permissions import decide
from coworker.tools import execute
from test_approved_send import _app, _gmail, _bound_person, _draft, _park, _decide, HEADERS
from test_approved_enrichment import APOLLO_PERSON


def setup(tmp_path):
    gmail=_gmail();http=FakeHttp({MATCH_URL:APOLLO_PERSON})
    app=_app(tmp_path,gmail,http=http,apollo_key='fake-key')
    client=TestClient(app);pid,sid=_bound_person(app)
    return app,client,pid,sid,gmail,http


@pytest.mark.parametrize('name', ['gmail_send','apollo_enrich_contact'])
@pytest.mark.parametrize('approval_granted', [False,True])
def test_direct_tool_boolean_cannot_authorize_a_consequential_call(tmp_path,name,approval_granted):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    draft=_draft(client,pid,sid)
    ok,result=execute(name,{'draft_id':draft['id'],'person_id':pid},people=app.state.people,
        gmail=gmail,http=http,apollo_key='fake-key',session_id=sid,approval_granted=approval_granted)
    assert not ok,result
    assert result['code']=='approval_binding_required'
    assert gmail.send_attempts==[] and http.calls==[]


@pytest.mark.parametrize('aba', [False,True])
def test_send_refuses_current_person_identity_drift_even_when_draft_is_unchanged(tmp_path,aba):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    draft=_draft(client,pid,sid);approval=_park(client,pid,sid,draft)
    app.state.people.apply_enrichment(pid,email='different@example.test')
    if aba:app.state.people.apply_enrichment(pid,email=draft['to'])
    result=_decide(client,approval).json()
    assert not result['ok'] and result['result']['code']=='identity_changed',result
    assert gmail.send_attempts==[]


def test_enrichment_approval_snapshots_match_and_refuses_later_identity(tmp_path):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    item=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json={'session_id':sid}).json()['item']
    assert item['resource']['match']=={'apollo_id':'ada'}
    assert item['resource']['credits_estimated'] is None
    app.state.people.apply_enrichment(pid,email='other@example.test')
    outcome=_decide(client,item['id']).json()
    assert not outcome['ok'] and outcome['result']['code']=='identity_changed',outcome
    assert http.calls==[]


@pytest.mark.parametrize('action', ['send','enrich'])
def test_backend_rejects_always_before_claiming_a_send_or_paid_call(tmp_path,action):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    if action=='send':
        item_id=_park(client,pid,sid,_draft(client,pid,sid))
    else:
        item_id=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json={'session_id':sid}).json()['item']['id']
    response=client.post(f'/v1/inbox/{item_id}',headers=HEADERS,json={'decision':'allow','scope':'always'})
    assert response.status_code==400,response.text
    assert app.state.inbox.get(item_id)['decision'] is None
    assert gmail.send_attempts==[] and http.calls==[]


@pytest.mark.parametrize('name',['mcp__gmail__send_email','mcp__apollo__people_match','mcp__mail__dispatch'])
def test_opaque_consequential_mcp_handlers_are_not_an_alternate_approval_door(tmp_path,name):
    calls=[]
    mcp=FakeMcp([{'name':name,'description':'External action','handler':lambda args:calls.append(args) or {'ok':True}}])
    assert not decide(name).allowed
    ok,result=execute(name,{},mcp=mcp,approval_granted=True)
    assert not ok,result
    assert calls==[]


def test_enrich_operation_retry_reuses_the_same_approval_and_credit_cost_is_unknown(tmp_path):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    payload={'session_id':sid,'operation_id':'stable-enrich-request'}
    first=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json=payload).json()['item']
    second=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json=payload).json()['item']
    assert first['id']==second['id']
    outcome=_decide(client,first['id']).json()
    assert outcome['ok'],outcome
    assert outcome['result']['credits_actual'] is None
    assert outcome['result']['status']=='found'
    assert len(http.calls)==1


def test_enrichment_retry_after_success_returns_the_original_approval(tmp_path):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    payload={'session_id':sid,'operation_id':'enrich-after-success'}
    first=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json=payload).json()['item']
    assert _decide(client,first['id']).json()['ok']
    repeated=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json=payload)
    assert repeated.status_code==201,repeated.text
    assert repeated.json()['item']['id']==first['id']
    assert _decide(client,first['id']).json()['idempotent']
    assert len(http.calls)==1


@pytest.mark.parametrize('field',['cc','bcc','from'])
def test_gmail_header_changes_cannot_add_unreviewed_people_or_sender(tmp_path,field):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    draft=_draft(client,pid,sid);item=_park(client,pid,sid,draft)
    gmail.drafts[-1][field]='someone-else@example.test'
    result=_decide(client,item).json()
    assert not result['ok'],result
    assert gmail.send_attempts==[]


def test_only_explicit_provider_credit_usage_is_actual_cost(tmp_path):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    http.routes[MATCH_URL]={**APOLLO_PERSON,'credits_used':3}
    item=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json={'session_id':sid}).json()['item']
    result=_decide(client,item['id']).json()
    assert result['ok'] and result['result']['credits_actual']==3,result


def test_unknown_paid_call_stops_until_a_new_explicitly_acknowledged_approval(tmp_path):
    class UncertainHttp(FakeHttp):
        uncertain=True
        def post(self,*args,**kwargs):
            result=super().post(*args,**kwargs)
            if self.uncertain:raise TimeoutError('request may have been processed')
            return result
    gmail=_gmail();http=UncertainHttp({MATCH_URL:APOLLO_PERSON})
    app=_app(tmp_path,gmail,http=http,apollo_key='fake-key');client=TestClient(app)
    pid,sid=_bound_person(app)
    payload={'session_id':sid,'operation_id':'uncertain-enrich-first'}
    first=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json=payload).json()['item']
    result=_decide(client,first['id']).json()
    assert not result['ok'] and result['result']['code']=='outcome_unknown',result
    _decide(client,first['id'])
    assert len(http.calls)==1
    fresh={**payload,'operation_id':'uncertain-enrich-second'}
    assert client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json=fresh).status_code==409
    fresh['acknowledged_unknown_id']=first['id']
    reviewed=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json=fresh)
    assert reviewed.status_code==201,reviewed.text
    assert len(http.calls)==1
    http.uncertain=False
    assert _decide(client,reviewed.json()['item']['id']).json()['ok']
    assert len(http.calls)==2
    assert app.state.inbox.get(first['id'])['execution_result']['code']=='outcome_unknown'


def test_exact_apollo_id_is_preferred_to_a_duplicate_local_email(tmp_path):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    other=app.state.people.keep_from_apollo(apollo_id='other',first_name='Other',last_name_obfuscated='Person',title=None,company=None)
    app.state.people.apply_enrichment(other['person_id'],email=app.state.people.get(pid)['email'])
    item=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json={'session_id':sid}).json()['item']
    assert item['resource']['match']=={'apollo_id':'ada'}
    assert item['resource']['matched_on']=='apollo_id'


@pytest.mark.parametrize('shared', ['email','name'])
def test_ambiguous_non_id_match_refuses_before_approval_or_credit_spend(tmp_path,shared):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    people=app.state.people
    # Model a manually retained identity without the original provider identifier.
    with people.crm_transaction():
        people._conn.execute('UPDATE people SET apollo_id=NULL WHERE person_id=?',(pid,))
    person=people.get(pid)
    other=people.keep_from_apollo(apollo_id=None,first_name=person['first_name'] if shared=='name' else 'Other',
        last_name_obfuscated=person['last_name'] if shared=='name' else 'Person',title=None,company=person['company'])
    if shared=='email':people.apply_enrichment(other['person_id'],email=person['email'])
    else:people.apply_enrichment(pid,email='')
    response=client.post(f'/v1/people/{pid}/enrich-approval',headers=HEADERS,json={'session_id':sid})
    assert response.status_code==409 and response.json()['code']=='ambiguous_match',response.text
    assert http.calls==[] and app.state.inbox.pending()==[]


def test_gmail_preflight_failure_never_returns_provider_exception_secrets(tmp_path):
    from coworker.gmail import GmailError
    app,client,pid,sid,gmail,http=setup(tmp_path)
    draft=_draft(client,pid,sid)
    def broken(*args,**kwargs):
        raise GmailError('Authorization: Bearer private-fake-access-token')
    gmail.get_draft=broken
    response=client.post(f'/v1/people/{pid}/outreach/send-approval',headers=HEADERS,
        json={'session_id':sid,'draft_id':draft['id'],'reviewed_body_digest':draft['body_digest']})
    assert response.status_code==409
    assert 'private-fake-access-token' not in response.text and 'Bearer' not in response.text
    assert gmail.send_attempts==[]


def test_identity_correction_during_live_gmail_review_refuses_before_send(tmp_path):
    app,client,pid,sid,gmail,http=setup(tmp_path)
    draft=_draft(client,pid,sid);approval=_park(client,pid,sid,draft)
    original=gmail.get_draft
    def correct_while_reading(**kwargs):
        snapshot=original(**kwargs)
        app.state.people.apply_enrichment(pid,email='corrected-during-read@example.test')
        return snapshot
    gmail.get_draft=correct_while_reading
    outcome=_decide(client,approval).json()
    assert not outcome['ok'] and outcome['result']['code']=='identity_changed',outcome
    assert gmail.send_attempts==[]
