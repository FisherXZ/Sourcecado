"""Durable tracked mail, collection progress, and pending analysis in people.db.

Bodies are sensitive, untrusted evidence. They stay out of timeline payloads
and operational logs; future analysis reads this store through person ownership.
"""

import hashlib
import json
from typing import Any
from urllib.parse import quote

from coworker.people import PersonStore, _new_event_id

MAIL_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS gmail_messages (
        account TEXT NOT NULL, message_id TEXT NOT NULL,
        person_id TEXT NOT NULL REFERENCES people(person_id), thread_id TEXT NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
        body TEXT NOT NULL, body_state TEXT NOT NULL
            CHECK(body_state IN ('present', 'partial', 'missing')),
        fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(account, message_id)
    )""",
    """CREATE TABLE IF NOT EXISTS gmail_collection_state (
        id INTEGER PRIMARY KEY CHECK(id = 1), account TEXT,
        status TEXT NOT NULL CHECK(status IN ('never', 'ok', 'partial', 'failed')),
        last_attempt_at TEXT, last_success_at TEXT, warning TEXT,
        outbound_after TEXT, outbound_checked_at TEXT,
        inbound_page_token TEXT, inbound_target_history_id TEXT, resync_history_id TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS crm_analysis_state (
        person_id TEXT PRIMARY KEY REFERENCES people(person_id),
        latest_evidence_revision INTEGER NOT NULL DEFAULT 0 CHECK(latest_evidence_revision >= 0),
        analyzed_revision INTEGER NOT NULL DEFAULT 0
            CHECK(analyzed_revision >= 0 AND analyzed_revision <= latest_evidence_revision)
    )""",
    "CREATE INDEX IF NOT EXISTS gmail_messages_person ON gmail_messages(person_id)",
)


def source_ref(message_id: str, thread_id: str, account: str | None = None) -> dict[str, Any]:
    # authuser selects the account without assuming it is Gmail's first tab.
    query = f"?authuser={quote(account, safe='')}" if account else ""
    return {"provider": "Gmail", "message_id": message_id, "thread_id": thread_id,
        "url": f"https://mail.google.com/mail/u/0/{query}#all/{quote(thread_id, safe='')}"}


class MailRepository:
    def __init__(self, people: PersonStore):
        self.people = people

    def message(self, account: str, message_id: str) -> dict[str, Any] | None:
        with self.people._lock:
            row = self.people._conn.execute(
                "SELECT * FROM gmail_messages WHERE account = ? AND message_id = ?",
                (account, message_id)).fetchone()
        return dict(row) if row else None

    def analysis_state(self, person_id: str) -> dict[str, Any]:
        with self.people._lock:
            row = self.people._conn.execute(
                "SELECT * FROM crm_analysis_state WHERE person_id = ?",
                (person_id,)).fetchone()
        return self.analysis_projection(dict(row) if row else None)

    @staticmethod
    def analysis_projection(row: dict[str, Any] | None) -> dict[str, Any]:
        result = dict(row) if row else {"latest_evidence_revision": 0, "analyzed_revision": 0}
        result["status"] = "pending" if result["latest_evidence_revision"] != result["analyzed_revision"] else "current" if result["latest_evidence_revision"] else "not_analyzed"
        if result.get("failure") and result["status"] == "pending":
            result["status"] = "failed" if result.get("attempts", 0) >= 3 else "retrying"
        result.pop("usage_json", None)
        return result

    def incomplete_body_count(self, account: str) -> int:
        with self.people._lock:
            return self.people._conn.execute("""SELECT count(*) FROM gmail_messages m
                JOIN people p ON p.person_id = m.person_id
                WHERE m.account = ? AND m.body_state != 'present' AND p.deleted_at IS NULL""", (account,)).fetchone()[0]

    def sync_state(self) -> dict[str, Any]:
        with self.people._lock:
            row = self.people._conn.execute("SELECT * FROM gmail_collection_state WHERE id = 1").fetchone()
        return dict(row) if row else {"status": "never", "account": None, "last_attempt_at": None,
            "last_success_at": None, "warning": None, "outbound_after": None,
            "outbound_checked_at": None, "inbound_page_token": None, "inbound_target_history_id": None, "resync_history_id": None}

    def public_sync_state(self) -> dict[str, Any]:
        state = self.sync_state()
        return {key: state.get(key) for key in ("status", "last_attempt_at", "last_success_at", "warning", "outbound_checked_at")}

    def finish_check(self, *, status: str, account: str | None, warning: str | None = None,
        outbound_after: str | None = None, checked_at: str | None = None,
        inbound_page_token: str | None = None, inbound_target_history_id: str | None = None,
        resync_history_id: str | None = None, advance_inbound: bool = False,
        inbound_cursor: str | None = None) -> None:
        now = checked_at or self.people._now()
        prior = self.sync_state()
        # A failed attempt records a warning, never advances a replay boundary.
        if status == "failed":
            account = prior["account"]
            outbound_after = prior["outbound_after"]
            inbound_page_token = prior["inbound_page_token"]
            inbound_target_history_id = prior["inbound_target_history_id"]
            resync_history_id = prior.get("resync_history_id")
        with self.people.crm_transaction() as conn:
            conn.execute("""INSERT INTO gmail_collection_state VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET account=excluded.account, status=excluded.status,
                last_attempt_at=excluded.last_attempt_at, last_success_at=excluded.last_success_at,
                warning=excluded.warning, outbound_after=excluded.outbound_after,
                outbound_checked_at=excluded.outbound_checked_at,
                inbound_page_token=excluded.inbound_page_token,
                inbound_target_history_id=excluded.inbound_target_history_id,
                resync_history_id=excluded.resync_history_id""",
                (account, status, now, now if status == "ok" else prior["last_success_at"], warning,
                 outbound_after, now if status == "ok" else prior["outbound_checked_at"],
                 inbound_page_token, inbound_target_history_id, resync_history_id))
            if advance_inbound and status != "failed":
                conn.execute("""INSERT INTO reply_sync VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET history_id=excluded.history_id, updated_at=excluded.updated_at""",
                    (inbound_cursor, now))

    def save_message(self, person_id: str, message: dict[str, Any], *, direction: str,
        account: str, content: dict[str, Any]) -> bool:
        mid, thread = str(message.get("id") or ""), str(message.get("thread_id") or "")
        if not mid or not thread:
            raise ValueError("Gmail message and thread identity are required")
        payload = {"direction": direction, "message_id": mid, "thread_id": thread,
            "from": message.get("from"), "to": message.get("to"), "subject": message.get("subject"),
            "rfc_message_id": message.get("rfc_message_id"), "in_reply_to": message.get("in_reply_to"),
            "snippet": str(message.get("snippet") or "")[:1000], "received_at": message.get("received_at"),
            "source_ref": source_ref(mid, thread, account), "untrusted": True, "read_only": True,
            "body_state": content["body_state"]}
        fingerprint = hashlib.sha256(json.dumps({"metadata": payload, "content": content},
            sort_keys=True).encode()).hexdigest()
        now = self.people._now()
        with self.people.crm_transaction() as conn:
            self.people._load_person_row(person_id)
            row = conn.execute("SELECT * FROM gmail_messages WHERE account = ? AND message_id = ?",
                (account, mid)).fetchone()
            if row and row["person_id"] != person_id:
                raise ValueError("Gmail message ownership conflict")
            if row and row["fingerprint"] == fingerprint:
                return False
            event = conn.execute("SELECT * FROM events WHERE person_id = ? AND external_key = ?",
                (person_id, f"gmail:message:{mid}")).fetchone()
            if event and event["kind"] == "send":
                # Enrich the original approval receipt; never replace its authority.
                payload = {**json.loads(event["payload"]), **payload}
            conn.execute("""INSERT INTO gmail_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account, message_id) DO UPDATE SET body=excluded.body,
                body_state=excluded.body_state, fingerprint=excluded.fingerprint, updated_at=excluded.updated_at""",
                (account, mid, person_id, thread, direction, content["body"], content["body_state"], fingerprint, now))
            if event:
                conn.execute("UPDATE events SET payload = ? WHERE event_id = ?", (json.dumps(payload), event["event_id"]))
            else:
                headline = str(message.get("subject") or "No subject")
                conn.execute("""INSERT INTO events(event_id, person_id, external_key, source, kind,
                    summary, payload, actor, tool) VALUES (?, ?, ?, 'gmail', 'mail', ?, ?, 'assistant', 'gmail_read')""",
                    (_new_event_id(), person_id, f"gmail:message:{mid}",
                     f"{'Received from' if direction == 'inbound' else 'Sent to'} {message.get('from' if direction == 'inbound' else 'to')}: {headline}", json.dumps(payload)))
            conn.execute("""INSERT INTO crm_analysis_state(person_id, latest_evidence_revision) VALUES (?, 1)
                ON CONFLICT(person_id) DO UPDATE SET latest_evidence_revision=latest_evidence_revision+1,
                attempts=0,attempted_revision=0,failure=NULL,next_attempt_at=NULL""", (person_id,))
        return True
