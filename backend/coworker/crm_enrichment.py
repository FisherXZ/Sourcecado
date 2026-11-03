"""Person-bound enrichment review and results. This adapter never calls Apollo."""
from __future__ import annotations

import math
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from coworker.consequential_actions import ActionRefused, safe_enrichment_match
from coworker.people import PersonStore
from coworker.store import ConversationStore

_OUTCOMES = {'found', 'partial', 'no_match', 'failed', 'insufficient_credit', 'outcome_unknown'}
_FIELDS = {'first_name', 'last_name', 'title', 'company', 'email', 'phone', 'linkedin_url', 'name'}
_FIELD_ALIASES = {'organizationName':'company','linkedinUrl':'linkedin_url'}
_MATCH_FIELDS = {'email', 'first_name', 'last_name', 'organization_name', 'apollo_id'}
_REVIEW_CODES = {'provider_identity_mismatch', 'provider_identity_unverified'}


def _cost(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _text(value: Any, limit: int = 500) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _match(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    result = {key: text for key in _MATCH_FIELDS if (text := _text(value.get(key))) is not None}
    return result or None


def _status(item: dict[str, Any]) -> str:
    if item.get('decision') == 'deny':
        return 'denied'
    execution = item.get('execution_status')
    if execution == 'executing':
        return 'running'
    if item.get('state') == 'pending' and execution == 'pending':
        return 'awaiting_approval'
    result = item.get('execution_result') or {}
    if isinstance(result, dict):
        if result.get('status') in _OUTCOMES:
            return result['status']
        if result.get('code') in {'outcome_unknown', 'effect_outcome_unknown', 'execution_interrupted'}:
            return 'outcome_unknown'
    if execution == 'interrupted':
        return 'outcome_unknown'
    # Legacy success records lack the newer field/cost outcome contract.
    return 'partial' if execution == 'succeeded' else 'failed'


class EnrichmentView:
    def __init__(self, people: PersonStore, store: ConversationStore, configured: Callable[[], bool]):
        self.people, self.store, self.configured = people, store, configured

    def _attempt(self, item: dict[str, Any], person: dict[str, Any]) -> dict[str, Any]:
        resource = item['resource']
        result = item.get('execution_result')
        result = result if isinstance(result, dict) else {}
        state = _status(item)
        fields = lambda key: sorted({_FIELD_ALIASES.get(field,field) for field in result.get(key, []) if isinstance(field, str) and _FIELD_ALIASES.get(field,field) in _FIELDS}) if isinstance(result.get(key, []), list) else []
        return {
            'id': item['id'], 'status': state, 'provider': 'apollo',
            'person': _text(resource.get('person')),
            'company': _text(resource.get('company')), 'title': _text(resource.get('title')),
            'match': _match(resource.get('match')), 'matched_on': _text(resource.get('matched_on'), 40),
            'credits_estimated': _cost(resource.get('credits_estimated')),
            'credits_actual': _cost(result.get('credits_actual')),
            'fetched_at': _text(result.get('fetched_at'), 80),
            'created_at': _text(item.get('created_at'), 80),
            'source_ref_id': _text(result.get('source_ref_id'), 200),
            'fields_found': fields('fields_found'), 'fields_applied': fields('fields_applied'),
            'conflicts': self._conflicts(result.get('conflicts')),
            'review_required': result.get('review_required') is True,
            'code': result.get('code') if result.get('code') in _REVIEW_CODES else None,
            'stale_identity': (resource.get('authority_version') != 2
                or resource.get('identity_revision') != person.get('identity_revision')),
        }

    @staticmethod
    def _conflicts(value: Any) -> list[dict[str, str | None]]:
        if not isinstance(value, list):
            return []
        return [
            {'field': entry['field'], 'current': _text(entry.get('current')), 'proposed': _text(entry.get('proposed'))}
            for entry in value[:20] if isinstance(entry, dict) and entry.get('field') in _FIELDS
        ]

    def get(self, pid: str) -> dict[str, Any] | None:
        person = self.people.get(pid)
        if person is None or person.get('deleted_at'):
            return None
        try:
            match = safe_enrichment_match(self.people, pid)
            match_problem = None
        except ActionRefused as exc:
            match, match_problem = None, exc.code
        items = [item for item in self.store.list_inbox(pending_only=False)
            if item.get('name') == 'apollo_enrich_contact' and isinstance(item.get('resource'), dict)
            and item['resource'].get('kind') == 'apollo_enrichment' and item['resource'].get('person_id') == pid]
        items = [item for _, item in sorted(enumerate(items),
            key=lambda pair: (str(pair[1].get('requested_at') or pair[1].get('created_at') or ''), pair[0]), reverse=True)]
        attempts = [self._attempt(item, person) for item in items[:20]]
        outstanding = next((attempt for attempt in attempts if attempt['status'] in {'awaiting_approval', 'running'}), None)
        latest = outstanding or (attempts[0] if attempts else None)
        state = latest['status'] if latest else 'not_enriched'
        problem = 'apollo_not_configured' if not self.configured() else match_problem
        # A newer result does not settle an older unknown; only a review that names it does.
        acknowledged = {item['resource'].get('acknowledged_unknown_id') for item in items}
        unresolved = next((attempt['id'] for attempt in attempts
            if attempt['status'] == 'outcome_unknown' and attempt['id'] not in acknowledged), None)
        return {
            'person': {'person_id': pid, 'name': ' '.join(str(person.get(k) or '') for k in ('first_name', 'last_name')).strip() or 'Unnamed person',
                'title': person.get('title'), 'company': person.get('company'), 'version': person['version'],
                'identity_revision': person.get('identity_revision', 0)},
            'known': {key: person.get(key) or None for key in ('email', 'phone', 'linkedin_url')},
            'match': _match(match), 'credits_estimated': None,
            'session_id': self.people.session_for_person(pid),
            'status': state, 'latest': latest, 'pending': latest if state == 'awaiting_approval' else None,
            'can_request': problem is None and outstanding is None and unresolved is None,
            'unresolved_unknown_id': unresolved,
            'attempts': attempts,
            'problem': problem,
        }


def enrichment_router(*, people: PersonStore, store: ConversationStore, configured: Callable[[], bool]) -> APIRouter:
    router = APIRouter()
    view = EnrichmentView(people, store, configured)

    @router.get('/v1/people/{person_id}/enrichment')
    def context(person_id: str):
        result = view.get(person_id)
        return result if result is not None else JSONResponse({'error': 'not found'}, status_code=404)

    return router
