"""One immutable approval authority for Sourcecado sends and paid enrichment.

Inbox owns decisions/claims; Agent Runs owns dispatch ambiguity. This service
checks the action/person/content represented by that claim and files its result.
Adapters without this authority cannot call a consequential connector directly.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import secrets
from typing import Any
from email.utils import parseaddr

from coworker.apollo import (HttpError, LiveHttp, MISSING_KEY, enrich_contact,
    enrichment_match, enrichment_resource)
from coworker.consequential_policy import ACTION_TOOLS
from coworker.gmail import (GmailError, GmailOutcomeUnknown, SendAuthority,
    SendAuthorityError, authority_for_draft, draft_snapshot, send_reviewed_draft)


class ActionRefused(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


class EnrichmentOutcomeUnknown(RuntimeError):
    """The paid endpoint may have consumed credits; its result was not observed."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _authority_error(exc: SendAuthorityError) -> str:
    return 'The live Gmail draft could not be read. Nothing was sent.' if exc.code == 'draft_unavailable' else str(exc)


def safe_enrichment_match(people, person_id) -> dict:
    """Select the exact saved provider id, or reject ambiguous local aliases."""
    person = people.get(person_id)
    if person is None:
        raise ActionRefused('person_not_found', 'The selected person is unavailable.', 404)
    if str(person.get('apollo_id') or '').strip():
        return {'apollo_id':str(person['apollo_id']).strip()}
    match = enrichment_match(person)
    if match is None:
        raise ActionRefused('no_match_key', 'This person has no sufficient enrichment identity. Correct their details first.')
    def normalized(value):
        return ' '.join(str(value or '').split()).casefold()
    for other in people.list_people():
        if other['person_id'] == person_id:
            continue
        shared = normalized(other.get('email')) == normalized(match['email']) if match.get('email') else (
            normalized(other.get('first_name')) == normalized(match.get('first_name'))
            and normalized(other.get('last_name')) == normalized(match.get('last_name')))
        if shared:
            raise ActionRefused('ambiguous_match', 'Another saved person shares this match identity. Correct or disambiguate the person before a paid lookup.')
    return match


class ConsequentialActions:
    def __init__(self, *, people, inbox, gmail, http, apollo_key, agent_runs=None, sync_title=None):
        self.people, self.inbox = people, inbox
        self.gmail, self.http, self.apollo_key = gmail, http, apollo_key
        self.agent_runs, self.sync_title = agent_runs, sync_title

    def _person(self, person_id, session_id):
        self.people.require_crm_schema()
        person = self.people.get(person_id) if person_id else None
        if person is None:
            raise ActionRefused('person_not_found', 'The selected person is unavailable.', 404)
        if not session_id:
            raise ActionRefused('unbound_session', 'A sourcing chat for this person is required.', 400)
        if self.people.person_for_session(session_id) != person_id:
            raise ActionRefused('unbound_session', 'This sourcing chat is not bound to the selected person.')
        return person

    def _unsettled(self, person_id, name, *, exclude=None, acknowledged_unknown_id=None):
        acknowledged = set()
        prior_id = acknowledged_unknown_id
        while prior_id and prior_id not in acknowledged:
            prior = self.inbox.get(prior_id)
            if (not prior or prior['name'] != name or (prior.get('resource') or {}).get('person_id') != person_id
                    or (prior.get('execution_result') or {}).get('code') != 'outcome_unknown'):
                raise ActionRefused('invalid_acknowledgement', 'A new review must name this person’s previous unknown lookup.')
            acknowledged.add(prior_id)
            prior_id = (prior.get('resource') or {}).get('acknowledged_unknown_id')
        for item in self.inbox.store.list_inbox(pending_only=False):
            resource = item.get('resource') or {}
            if item['id'] == exclude or item['name'] != name or resource.get('person_id') != person_id:
                continue
            if item['id'] in acknowledged and item.get('execution_status') != 'executing':
                continue
            effects = self.agent_runs.effects_for_approval(item['id']) if self.agent_runs else []
            unresolved = any(e['status'] in {'ambiguous', 'dispatched'} for e in effects)
            if unresolved or (item.get('execution_result') or {}).get('code') == 'outcome_unknown' and not effects:
                raise ActionRefused('outcome_unknown', 'The previous action has an unknown outcome. Settle it before another attempt.')
            if item.get('execution_status') == 'executing':
                raise ActionRefused('action_in_progress', 'An approved action for this person is still running.')

    def send_resource(self, person_id, *, session_id, draft_id, reviewed_body_digest,
                      expected_thread_id=None, task_id=None, approval_id='pending'):
        person = self._person(person_id, session_id)
        if not reviewed_body_digest:
            raise ActionRefused('review_required', 'Review the full draft in the person file before requesting this send.', 400)
        if expected_thread_id and not any(t['thread_id'] == expected_thread_id for t in self.people.mail_threads(person_id)):
            raise ActionRefused('thread_not_on_file', 'That Gmail conversation is not filed on this person.')
        try:
            authority = authority_for_draft(self.gmail(), approval_id=approval_id, person_id=person_id,
                draft_id=draft_id, reviewed_body_digest=reviewed_body_digest,
                expected_thread_id=expected_thread_id, task_id=task_id)
        except SendAuthorityError as exc:
            raise ActionRefused(exc.code, _authority_error(exc)) from None
        if not authority.account:
            raise ActionRefused('account_unavailable', 'The sending account could not be verified. Reconnect Gmail and review again.')
        self._reviewable_snapshot(draft_id)
        if authority.to.strip().casefold() != str(person.get('email') or '').strip().casefold() or not person.get('email'):
            raise ActionRefused('recipient_not_bound', 'This draft is addressed to someone other than this person.')
        return {**authority.as_resource(), 'authority_version': 2,
            'identity_revision': person['identity_revision'], 'thread_id': authority.thread_id}

    def _reviewable_snapshot(self, draft_id):
        snapshot = draft_snapshot(self.gmail(), draft_id=draft_id)
        self._validate_reviewable(snapshot)
        return snapshot

    def _validate_reviewable(self, snapshot):
        if snapshot['cc'] or snapshot['bcc']:
            raise ActionRefused('recipient_mismatch', 'This bounded review does not include Cc or Bcc recipients. Remove them and review again.')
        sender = parseaddr(snapshot['from'])[1]
        if sender and sender.casefold() != str(snapshot['account'] or '').casefold():
            raise ActionRefused('account_mismatch', 'The draft sender differs from the reviewed sending account.')
        if snapshot['unreviewed_parts']:
            raise ActionRefused('unsupported_message', 'This editor cannot review attachments or HTML message parts. Nothing was sent.')

    def enrichment_resource(self, person_id, *, session_id):
        person = self._person(person_id, session_id)
        if not self.apollo_key():
            raise ActionRefused('apollo_unavailable', MISSING_KEY)
        match = safe_enrichment_match(self.people, person_id)
        resource = enrichment_resource(person, match)
        return {**resource, 'authority_version': 2, 'identity_revision': person['identity_revision'],
            'match': match, 'match_fingerprint': _digest(match), 'credits': None, 'credits_estimated': None,
            'reason': f"Look up {resource['person']} in Apollo. Credit cost is unknown. Allow once or Deny."}

    def _existing(self, name, person_id, session_id, operation_id, request_key):
        if operation_id is None:
            return None
        if not isinstance(operation_id, str) or not 8 <= len(operation_id.strip()) <= 128:
            raise ActionRefused('invalid_operation', 'operation_id must contain 8–128 characters.', 400)
        item = self.inbox.get('action_'+_digest({'session':session_id,'operation':operation_id})[:32])
        if item and (item['name'] != name or (item.get('resource') or {}).get('person_id') != person_id
                or (item.get('resource') or {}).get('request_key') != request_key):
            raise ActionRefused('operation_conflict', 'This operation id belongs to a different reviewed action.')
        return item

    def _park(self, name, resource, *, session_id, operation_id=None, run_id=None, request_key=None):
        if operation_id is not None and (not isinstance(operation_id, str) or not 8 <= len(operation_id.strip()) <= 128):
            raise ActionRefused('invalid_operation', 'operation_id must contain 8–128 characters.', 400)
        fingerprint = _digest({'name': name, 'session_id': session_id, 'resource': resource})
        resource = {**resource, 'request_fingerprint': fingerprint, 'request_key':request_key}
        item_id = 'action_'+_digest({'session': session_id, 'operation': operation_id})[:32] if operation_id else 'action_'+secrets.token_hex(16)
        with self.inbox.store._lock:
            existing = self.inbox.get(item_id)
            if existing:
                if existing['name'] != name or (existing.get('resource') or {}).get('request_key') != request_key:
                    raise ActionRefused('operation_conflict', 'This operation id belongs to a different reviewed action.')
                return existing
            self._unsettled(resource['person_id'], name, acknowledged_unknown_id=resource.get('acknowledged_unknown_id'))
            arguments = {'draft_id': resource['draft_id']} if name == 'gmail_send' else {'person_id': resource['person_id']}
            return self.inbox.park(name, arguments, item_id=item_id, resource=resource,
                reason=resource.get('reason') or 'Send this reviewed message once. Allow or Deny.',
                session_id=session_id, run_id=run_id)

    def prepare_send(self, person_id, *, session_id, draft_id, reviewed_body_digest,
                     expected_thread_id=None, task_id=None, operation_id=None, run_id=None):
        request_key = _digest({'draft_id':draft_id,'reviewed_body_digest':reviewed_body_digest,
            'expected_thread_id':expected_thread_id,'task_id':task_id,'person_id':person_id})
        existing = self._existing('gmail_send',person_id,session_id,operation_id,request_key)
        if existing:
            return existing
        resource = self.send_resource(person_id, session_id=session_id, draft_id=draft_id,
            reviewed_body_digest=reviewed_body_digest, expected_thread_id=expected_thread_id, task_id=task_id)
        return self._park('gmail_send', resource, session_id=session_id, operation_id=operation_id, run_id=run_id, request_key=request_key)

    def prepare_enrichment(self, person_id, *, session_id, operation_id=None, run_id=None, acknowledged_unknown_id=None):
        request_key = _digest({'person_id':person_id,'acknowledged_unknown_id':acknowledged_unknown_id})
        existing = self._existing('apollo_enrich_contact',person_id,session_id,operation_id,request_key)
        if existing:
            return existing
        resource = self.enrichment_resource(person_id, session_id=session_id)
        if acknowledged_unknown_id:
            resource['acknowledged_unknown_id'] = acknowledged_unknown_id
        return self._park('apollo_enrich_contact', resource,
            session_id=session_id, operation_id=operation_id, run_id=run_id, request_key=request_key)

    def tool_resource(self, name, arguments, *, session_id):
        person_id = arguments.get('person_id') or self.people.person_for_session(session_id)
        if name == 'gmail_send':
            return self.send_resource(person_id, session_id=session_id, draft_id=arguments.get('draft_id'),
                reviewed_body_digest=arguments.get('reviewed_body_digest'), expected_thread_id=arguments.get('expected_thread_id'))
        resource = self.enrichment_resource(person_id, session_id=session_id)
        supplied = {key: arguments[key] for key in ('email', 'firstName', 'lastName', 'organizationName') if arguments.get(key)}
        keys = {'email':'email','firstName':'first_name','lastName':'last_name','organizationName':'organization_name'}
        if any(str(value).strip().casefold() != str(resource['match'].get(keys[key]) or '').strip().casefold() for key,value in supplied.items()):
            raise ActionRefused('match_not_bound', 'Enrichment must use this person’s reviewed identity, not a model-selected match.')
        self._unsettled(person_id, name)
        return resource

    def execute_claimed(self, approval_id, claimant, *, expected_name=None, arguments=None):
        item = self.inbox.get(approval_id) if approval_id else None
        if (not item or not claimant or item.get('execution_claimant') != claimant
                or item.get('execution_status') != 'executing' or item.get('decision') != 'allow'
                or item.get('scope') != 'once' or item.get('name') not in ACTION_TOOLS
                or expected_name is not None and item['name'] != expected_name):
            return False, {'code':'approval_binding_required','error':'This action needs its own claimed, once-only approval.'}
        try:
            resource = item.get('resource') or {}
            if arguments and ((arguments.get('person_id') and arguments['person_id'] != resource.get('person_id'))
                    or (item['name']=='gmail_send' and arguments.get('draft_id') != resource.get('draft_id'))):
                raise ActionRefused('approval_binding_required', 'The tool arguments do not match the approved action.')
            if resource.get('authority_version') != 2:
                raise ActionRefused('review_required', 'This approval lacks current identity and message checks. Review the action again.')
            person = self._person(resource.get('person_id'), item.get('session_id'))
            if person['identity_revision'] != resource.get('identity_revision'):
                raise ActionRefused('identity_changed', 'This person’s recipient or match identity changed. Review the action again.')
            self._unsettled(person['person_id'], item['name'], exclude=approval_id,
                acknowledged_unknown_id=resource.get('acknowledged_unknown_id'))
            if item['name'] == 'gmail_send':
                return self._send(item, resource, person)
            return self._enrich(item, resource, person)
        except (ActionRefused, SendAuthorityError) as exc:
            message = _authority_error(exc) if isinstance(exc, SendAuthorityError) else str(exc)
            return False, {'code':exc.code,'error':message,'sent':False} if item['name']=='gmail_send' else {'code':exc.code,'error':message}

    def _send(self, item, resource, person):
        authority = SendAuthority.from_resource(resource, approval_id=item['id'])
        if authority is None or resource.get('kind') != 'gmail_send_authority' or not authority.account or 'thread_id' not in resource:
            raise ActionRefused('review_required', 'This send authority is incomplete. Review the draft again.')
        if authority.to.strip().casefold() != str(person.get('email') or '').strip().casefold():
            raise ActionRefused('identity_changed', 'This person’s recipient changed. Review the draft again.')
        def final_check(snapshot):
            self._validate_reviewable(snapshot)
            current = self._person(person['person_id'], item.get('session_id'))
            if current['identity_revision'] != resource['identity_revision'] or str(current.get('email') or '').strip().casefold() != authority.to.strip().casefold():
                raise ActionRefused('identity_changed', 'This person’s recipient or match identity changed. Review the draft again.')
            if snapshot['thread_id'] != resource['thread_id']:
                raise ActionRefused('thread_mismatch', 'The Gmail conversation changed. Nothing was sent.')
        try:
            result = send_reviewed_draft(self.gmail(), authority, before_send=final_check)
        except GmailOutcomeUnknown:
            raise
        except SendAuthorityError:
            raise
        except GmailError:
            return False, {'code':'gmail_failed','error':'Gmail refused this send. Nothing was sent.','sent':False}
        try:
            filed = self.people.record_approved_send(person['person_id'], message_id=result['message_id'],
                thread_id=result['thread_id'], draft_id=authority.draft_id, to=authority.to,
                subject=authority.subject, body_digest=authority.body_digest, account=authority.account,
                approval_id=item['id'], actor='director', session_id=item.get('session_id'),
                run_id=item.get('run_id'), task_id=authority.task_id)
        except Exception:
            return True, {**result, 'receipt_error':'The send succeeded, but its local person receipt could not be saved.', 'person_event_id':None}
        return True, {**result,'person_event_id':filed['event']['event_id'],
            'sequence_state':filed['person']['sequence_state'],'advanced_to_open':filed['advanced_to_open']}

    def _enrich(self, item, resource, person):
        match = resource.get('match')
        if resource.get('kind') != 'apollo_enrichment' or not isinstance(match, dict) or _digest(match) != resource.get('match_fingerprint') or safe_enrichment_match(self.people, person['person_id']) != match:
            raise ActionRefused('identity_changed', 'The reviewed enrichment match changed. Review it again.')
        if not self.apollo_key():
            raise ActionRefused('apollo_unavailable', MISSING_KEY)
        base = {'person_id':person['person_id'],'approval_id':item['id'],'provider':'apollo',
            'fetched_at':datetime.now(UTC).isoformat(),'credits_estimated':resource.get('credits_estimated'),
            'credits_actual':None,'fields_found':[],'fields_applied':[],'conflicts':[],'source_ref_id':None}
        try:
            result = enrich_contact(http=self.http() or LiveHttp(), api_key=self.apollo_key(), **match)
        except HttpError as exc:
            insufficient = exc.status == 402 or isinstance(exc.body,dict) and exc.body.get('error_code')=='INSUFFICIENT_CREDITS'
            return False, {**base,'status':'insufficient_credit' if insufficient else 'failed',
                'code':'insufficient_credit' if insufficient else 'apollo_failed','error':'Apollo has insufficient credits.' if insufficient else 'Apollo refused this lookup.'}
        except Exception:
            raise EnrichmentOutcomeUnknown('Apollo did not provide an observable outcome.') from None
        fields = sorted(key for key in ('name','title','organizationName','email','linkedinUrl','phone') if result.get(key))
        outcome = 'found' if result.get('email') else 'partial' if fields else 'no_match'
        base.update(status=outcome, fields_found=fields, credits_actual=result.get('credits_actual'))
        try:
            filed = self.people.record_apollo_enrichment(person['person_id'], result=result,
                approval_id=item['id'], credits=result.get('credits_actual'),
                matched_on=resource.get('matched_on','name'), actor='director',
                session_id=item.get('session_id'), run_id=item.get('run_id'))
            if self.sync_title:
                self.sync_title(person['person_id'])
        except Exception:
            return True, {**base,'receipt_error':'The lookup completed, but its local receipt could not be saved.'}
        if filed:
            base.update(fields_applied=filed['fields_applied'], source_ref_id=filed['source_ref']['id'], conflicts=filed.get('conflicts',[]))
        return True, {**base,'apollo_id':result.get('apolloId'), 'credits':result.get('credits_actual')}
