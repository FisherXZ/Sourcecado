"""Recognize v7 attention decisions only against unchanged, permitted full mail.

The old interpretation identity included the persisted Gmail fingerprint as well
as the task intent or description slot. Reconstruct that identity only after the
saved body, account and message agree with current attributed evidence. A title
alone, a public source alias, or an old model fingerprint grants no authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from email.utils import getaddresses
from typing import Any

from coworker.crm_sources import source_ids
from coworker.crm_tasks import _fingerprint


def _body_digest(body: str) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _saved_mail(people: Any, pid: str, support: dict, evidence: Any, allowed: set[str]) -> dict | None:
    """A saved, full body for the exact attributed mailbox message, else unknown."""
    source_ids(people, pid, [support['source_id']], allowed)
    messages = [item for item in evidence.messages if item['key'] == support['key']]
    if len(messages) != 1 or _body_digest(messages[0]['body']) != support['body_fingerprint']:
        return None
    metadata = [item.metadata for item in evidence.attribution
                if item.metadata.get('id') == support['message_id']]
    if len(metadata) != 1:
        return None
    meta = metadata[0]
    mailbox_fields = ('from',) if support['direction'] == 'outbound' else ('to', 'cc', 'delivered_to')
    accounts = {address.casefold() for _name, address in getaddresses(
        [str(meta[field]) for field in mailbox_fields if meta.get(field)])}
    rows = people._conn.execute("""SELECT * FROM gmail_messages WHERE person_id=? AND message_id=?
        AND thread_id=? AND direction=? AND body_state='present'""",
        (pid, support['message_id'], meta.get('thread_id'), support['direction'])).fetchall()
    rows = [dict(row) for row in rows if row['account'].casefold() in accounts]
    if len(rows) != 1:
        return None
    saved = rows[0]
    if not saved['fingerprint'] or _body_digest(saved['body']) != support['body_fingerprint']:
        return None
    aliases = people._conn.execute("""SELECT * FROM person_attachments
        WHERE person_id=? AND record_type='source_ref'""", (pid,)).fetchall()
    selected = None
    for row in aliases:
        fields = json.loads(row['fields_json'])
        if row['id'] == support['source_id']:
            selected = fields
        same_message = (fields.get('message_id') == support['message_id']
            or fields.get('id') in {"gmail:"+support['message_id'], "gmail:message:"+support['message_id']})
        same_account = not fields.get('account') or fields['account'].casefold() == saved['account'].casefold()
        if same_message and same_account and (row['restricted'] or fields.get('sensitivity') == 'restricted') and row['id'] not in allowed:
            raise ValueError('legacy CRM evidence includes an unavailable private source')
    if selected is None or selected.get('account') and selected['account'].casefold() != saved['account'].casefold():
        return None
    return saved


def legacy_decision(people: Any, pid: str, *, kind: str, value: dict,
                    support: list[dict], evidence: Any,
                    allowed: set[str] | None = None) -> tuple[bool, list[str]]:
    """Return seen/pending ids without copying legacy content into a new record."""
    permitted = set(allowed or ())
    if not people._conn.execute('SELECT 1 FROM crm_interpretations WHERE person_id=? LIMIT 1', (pid,)).fetchone() and not people._conn.execute(
        "SELECT 1 FROM crm_proposals WHERE person_id=? AND intent_key LIKE 'legacy:%' LIMIT 1", (pid,)).fetchone():
        return False, []
    if kind == 'description':
        support = evidence.manifest  # The old identity covered the whole conversation.
        identity = {'slot': value['slot']}
        message = None
    elif kind == 'task_create':
        identity = {'intent': sorted(set(re.findall(r'[a-z0-9]+', str(value.get('title') or '').casefold())) -
            {'send', 'share', 'email', 'provide', 'the', 'a', 'an', 'to', 'for', 'please', 'me', 'us'})}
        message = support[0]['message_id']
    elif kind == 'task_update':
        identity = {'task_id': value['task_id'], 'changes': value['changes']}
        message = support[0]['message_id']
    else:
        return False, []
    if not support:
        return False, []
    saved = [_saved_mail(people, pid, item, evidence, permitted) for item in support]
    if any(item is None for item in saved):
        return False, []
    fingerprints = [item['fingerprint'] for item in saved]
    fingerprint = _fingerprint({'kind': kind, 'intent': identity, 'message': message,
        'evidence': sorted(fingerprints) if kind == 'description' else fingerprints[0]})
    seen = people._conn.execute('SELECT 1 FROM crm_interpretations WHERE person_id=? AND fingerprint=?',
        (pid, fingerprint)).fetchone() is not None
    rows = people._conn.execute("""SELECT proposal_id,state,source_refs FROM crm_proposals
        WHERE person_id=? AND intent_key LIKE 'legacy:%' AND support_source_fingerprint=?""",
        (pid, fingerprint)).fetchall()
    for row in rows:
        source_ids(people, pid, json.loads(row['source_refs']), permitted)
    return seen or bool(rows), [row['proposal_id'] for row in rows if row['state'] == 'pending']
