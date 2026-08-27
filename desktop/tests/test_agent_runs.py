import asyncio
import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from coworker.agent_runs import redact_sensitive_assignments
from coworker.provider import FakeProvider, ToolCall
from coworker.inbox import Inbox
from coworker.server import TOKEN_HEADER, create_app
from coworker.store import ConversationStore
from coworker.turn import run_turn


TOKEN = "test-token-agent-runs"


def _drain(ws):
    events = []
    while True:
        event = ws.receive_json()
        events.append(event)
        if event["type"] in {"turn_end", "turn_stopped", "error"}:
            return events


def _nested_keys(value):
    if isinstance(value, dict):
        return {
            str(key).lower()
            for key in value
        } | {
            nested
            for item in value.values()
            for nested in _nested_keys(item)
        }
    if isinstance(value, list):
        return {nested for item in value for nested in _nested_keys(item)}
    return set()


def test_agent_run_round_trip_is_idempotent_and_merges_semantic_checkpoints(tmp_path):
    store = ConversationStore(tmp_path)

    first = store.start_agent_run(
        run_id="run-alpha",
        session_id="thread-alpha",
        parent_run_id=None,
        trigger="chat",
        original_goal="Find three strong candidates.",
        provider_model_id="fake",
    )
    duplicate = store.start_agent_run(
        run_id="run-alpha",
        session_id="thread-alpha",
        parent_run_id=None,
        trigger="chat",
        original_goal="Find three strong candidates.",
        provider_model_id="fake",
    )

    assert duplicate == first
    assert [item["kind"] for item in store.list_agent_run_checkpoints("run-alpha")] == [
        "run_started"
    ]

    store.checkpoint_agent_run(
        "run-alpha",
        kind="model_completed",
        payload={"text_length": 42, "tool_call_count": 1},
        skills_loaded=["candidate-search"],
        source_refs=[{"id": "source-1", "title": "First source"}],
        artifact_refs=[{"artifact_type": "shortlist", "title": "Top three"}],
        usage_delta={"model_calls": 1},
    )
    store.checkpoint_agent_run(
        "run-alpha",
        kind="tool_completed",
        payload={"id": "call-1", "name": "load_skill", "ok": True},
        state="waiting_approval",
        skills_loaded=["candidate-search", "outreach"],
        source_refs=[
            {"id": "source-1", "title": "Duplicate source"},
            {"id": "source-2", "title": "Second source"},
        ],
        artifact_refs=[{"artifact_type": "shortlist", "title": "Top three"}],
        usage_delta={"tool_calls": 1, "model_calls": 2},
    )
    store.checkpoint_agent_run(
        "run-alpha",
        kind="approval_resolved",
        payload={"id": "call-1", "resolution": "allowed"},
        state="running",
    )
    terminal = store.checkpoint_agent_run(
        "run-alpha",
        kind="terminal",
        payload={"status": "ok", "text_length": 17},
        state="complete",
        terminal_result={"status": "ok", "text": "Three candidates."},
    )
    duplicate_terminal = store.checkpoint_agent_run(
        "run-alpha",
        kind="terminal",
        payload={"status": "ok", "text_length": 17},
        state="complete",
        terminal_result={"status": "ok", "text": "Three candidates."},
    )

    assert terminal == duplicate_terminal
    run = store.get_agent_run("run-alpha")
    assert run is not None
    assert run == store.list_agent_runs(session_id="thread-alpha")[0]
    assert run["run_id"] == "run-alpha"
    assert run["session_id"] == "thread-alpha"
    assert run["trigger"] == "chat"
    assert run["original_goal"] == "Find three strong candidates."
    assert run["provider_model_id"] == "fake"
    assert run["current_state"] == "complete"
    assert run["checkpoint_sequence"] == 5
    assert run["skills_loaded"] == ["candidate-search", "outreach"]
    assert run["source_refs"] == [
        {
            "id": "source-1",
            "title": "First source",
            "url": None,
            "provider": "",
            "stale": False,
            "truncated": False,
        },
        {
            "id": "source-2",
            "title": "Second source",
            "url": None,
            "provider": "",
            "stale": False,
            "truncated": False,
        },
    ]
    assert run["artifact_refs"] == [
        {
            "id": "",
            "artifact_type": "shortlist",
            "title": "Top three",
            "external_url": None,
            "stale": False,
            "truncated": False,
        }
    ]
    assert run["usage"] == {"model_calls": 3, "tool_calls": 1}
    assert run["terminal_result"] == {
        "status": "ok",
        "text_length": len("Three candidates."),
    }
    assert run["created_at"]
    assert run["started_at"]
    assert run["updated_at"]
    assert run["finished_at"]
    checkpoints = store.list_agent_run_checkpoints("run-alpha")
    assert [item["sequence"] for item in checkpoints] == [1, 2, 3, 4, 5]
    assert [item["kind"] for item in checkpoints] == [
        "run_started",
        "model_completed",
        "tool_completed",
        "approval_resolved",
        "terminal",
    ]


def test_agent_run_persistence_sanitizes_every_json_boundary(tmp_path):
    store = ConversationStore(tmp_path)
    private_goal = (
        "Prepare a safe summary.\n"
        "token=goal-token-value\n"
        "access_token=goal-access-token-value\n"
        "Authorization: Bearer goal-authorization-value\n"
        "Authorization: Basic goal-basic-value\n"
        "Keep this goal line intact."
    )
    first = store.start_agent_run(
        run_id="run-private",
        session_id="thread-private",
        trigger="chat",
        original_goal=private_goal,
        provider_model_id="fake",
    )
    duplicate = store.start_agent_run(
        run_id="run-private",
        session_id="thread-private",
        trigger="chat",
        original_goal=private_goal,
        provider_model_id="fake",
    )
    assert duplicate == first
    sensitive_fields = {
        "password": "password-field-value",
        "api_key": "api-underscore-value",
        "api-key": "api-hyphen-value",
        "cookie": "cookie-field-value",
        "authorization": "Bearer authorization-field-value",
        "header": "header-field-value",
        "token": "token-field-value",
        "secret": "secret-field-value",
        "credential": "credential-field-value",
        "body": "body-field-value",
        "raw_body": "raw-body-field-value",
        "raw_source": "raw-source-field-value",
        "raw_payload": "raw-payload-field-value",
    }
    assignment_values = {
        "token=embedded-token-value",
        "refresh_token=embedded-refresh-token-value",
        "client_secret=embedded-client-secret-value",
        "api_key=embedded-api-key-value",
        "Authorization: Bearer embedded-authorization-value",
        "password=embedded-password-value",
    }

    store.checkpoint_agent_run(
        "run-private",
        kind="tool_completed",
        payload={
            **sensitive_fields,
            "summary": (
                "Useful checkpoint summary\n"
                "token=embedded-token-value\n"
                "refresh_token=embedded-refresh-token-value\n"
                "client_secret=embedded-client-secret-value\n"
                "api_key=embedded-api-key-value\n"
                "Authorization: Bearer embedded-authorization-value\n"
                "password=embedded-password-value"
            ),
        },
        skills_loaded=[
            "ordinary-sourcing-skill",
            "skill access_token=skill-access-token-value",
            "skill client_secret=skill-client-secret-value",
            "skill Authorization: Bearer skill-bearer-value",
        ],
        source_refs=[
            {
                "id": "source-safe",
                "title": (
                    "Candidate notes\n"
                    "access_token=source-access-token-value\n"
                    "Authorization: Basic source-basic-value"
                ),
                "url": "https://example.test/file?api-key=source-api-key-value",
                "body": "source-body-value",
                "raw_source": "source-raw-value",
                "authorization": "Bearer source-authorization-value",
            }
        ],
        artifact_refs=[
            {
                "id": "artifact-safe",
                "artifact_type": "shortlist",
                "title": "Shortlist client_secret=artifact-client-secret-value",
                "external_url": "https://example.test/artifact",
                "raw_payload": "artifact-raw-value",
                "cookie": "artifact-cookie-value",
            }
        ],
    )
    store.checkpoint_agent_run(
        "run-private",
        kind="terminal",
        payload={"status": "ok", "text_length": 80},
        state="complete",
        terminal_result={
            "status": "ok",
            "text": (
                "Ordinary user-facing text stays intact.\n"
                "Authorization: Bearer terminal-authorization-value\n"
                "Authorization: Basic terminal-basic-value\n"
                "refresh_token=terminal-refresh-token-value\n"
                "The final line also stays intact."
            ),
            "error": (
                "password=terminal-password-value "
                "API_KEY=terminal-api-assignment-value"
            ),
            "api_key": "terminal-api-key-value",
            "body": "terminal-body-value",
        },
    )

    run = store.get_agent_run("run-private")
    checkpoints = store.list_agent_run_checkpoints("run-private")
    durable = {"run": run, "checkpoints": checkpoints}
    keys = _nested_keys(durable)
    assert not keys.intersection(sensitive_fields)
    assert run["source_refs"][0]["id"] == "source-safe"
    assert run["source_refs"][0]["title"].startswith("Candidate notes\n")
    assert run["artifact_refs"][0]["artifact_type"] == "shortlist"
    assert run["skills_loaded"] == [
        "ordinary-sourcing-skill",
        "skill access_token=[REDACTED]",
        "skill client_secret=[REDACTED]",
        "skill Authorization:[REDACTED]",
    ]
    assert run["terminal_result"]["status"] == "ok"
    assert run["terminal_result"]["text_length"] == len(
        "Ordinary user-facing text stays intact.\n"
        "Authorization: Bearer terminal-authorization-value\n"
        "Authorization: Basic terminal-basic-value\n"
        "refresh_token=terminal-refresh-token-value\n"
        "The final line also stays intact."
    )
    assert run["terminal_result"]["error"] == (
        "password=[REDACTED] API_KEY=[REDACTED]"
    )
    assert run["original_goal"].startswith("Prepare a safe summary.\n")
    assert run["original_goal"].endswith("\nKeep this goal line intact.")
    assert checkpoints[1]["payload"]["summary"].startswith(
        "Useful checkpoint summary\n"
    )
    serialized = json.dumps(durable)
    for value in sensitive_fields.values():
        assert value not in serialized
    for assignment in assignment_values:
        assert assignment not in serialized
    for secret_value in (
        "source-access-token-value",
        "source-basic-value",
        "source-api-key-value",
        "artifact-client-secret-value",
        "terminal-authorization-value",
        "terminal-basic-value",
        "terminal-refresh-token-value",
        "terminal-password-value",
        "terminal-api-assignment-value",
        "goal-token-value",
        "goal-access-token-value",
        "goal-authorization-value",
        "goal-basic-value",
        "skill-access-token-value",
        "skill-client-secret-value",
        "skill-bearer-value",
    ):
        assert secret_value not in serialized


def test_agent_run_uses_field_specific_safe_projections_for_untrusted_metadata(
    tmp_path,
):
    provider_secrets = {
        # Assemble provider-shaped fixtures at runtime so repository push
        # protection never mistakes the test source itself for a live secret.
        "openai": "sk-" + "proj-abcdefghijklmnopqrstuvwxyz123456",
        "google": "AI" + "zaSyA1234567890abcdefghijklmnopqrst",
        "aws": "AK" + "IAIOSFODNN7EXAMPLE",
        "github": "gh" + "p_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
        "slack": "xo" + "xb-123456789012-123456789012-abcdefghijklmnopqrstuvwx",
        "jwt": (
            "eyJhbGciOiJIUzI1NiJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0.signatureABC"
        ),
    }
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "cHJpdmF0ZS1rZXktbWF0ZXJpYWw=\n"
        "-----END PRIVATE KEY-----"
    )
    unsafe_text = (
        "Basic research and Bearer market analysis stay readable.\n"
        f"Standalone {provider_secrets['openai']} {provider_secrets['google']} "
        f"{provider_secrets['aws']} {provider_secrets['github']} "
        f"{provider_secrets['slack']} {provider_secrets['jwt']}\n"
        f"{pem}\n"
        '{"Authorization":"Bearer json-header-secret"}\n'
        "Authorization: Basic basic-header-secret\n"
        "https://example.test/path?AWSAccessKeyId=aws-query-secret"
        "&Signature=signature-secret&key=key-secret&code=code-secret"
    )
    terminal_error = (
        "Basic research-methodology stays readable.\n"
        f"Provider {provider_secrets['openai']}\n"
        '{"Authorization":"Bearer terminal-json-secret"}'
    )
    store = ConversationStore(tmp_path)
    store.start_agent_run(
        run_id="run-field-safe",
        session_id="thread-field-safe",
        trigger="chat",
        original_goal=unsafe_text,
        provider_model_id="fake",
    )
    store.checkpoint_agent_run(
        "run-field-safe",
        kind="tool_completed",
        payload={"summary": unsafe_text},
        source_refs=[
            {
                "id": "source-safe",
                "title": f"Source {provider_secrets['github']}",
                "url": (
                    "https://user:password@example.test/source/path"
                    "?AWSAccessKeyId=source-key&Signature=source-signature#private"
                ),
                "provider": "Google Drive",
                "stale": False,
                "truncated": True,
                "preview": "free-form source preview must not persist",
                "body": "free-form source body must not persist",
                "unexpected": "free-form source metadata must not persist",
            },
            {
                "id": "source-unsafe-url",
                "title": "Unsafe URL",
                "url": "ftp://user:password@example.test/private?code=secret",
                "provider": "Web",
            },
        ],
        artifact_refs=[
            {
                "id": "artifact-safe",
                "artifact_type": "shortlist",
                "title": f"Artifact {provider_secrets['slack']}",
                "external_url": (
                    "http://user:password@files.test/artifacts/one"
                    "?key=artifact-key&code=artifact-code#private"
                ),
                "stale": True,
                "truncated": False,
                "preview": "free-form artifact preview must not persist",
                "raw_payload": "free-form artifact payload must not persist",
                "unexpected": "free-form artifact metadata must not persist",
            }
        ],
    )
    store.checkpoint_agent_run(
        "run-field-safe",
        kind="terminal",
        payload={"status": "failed"},
        state="failed",
        terminal_result={
            "status": "error",
            "message_id": "message-safe",
            "text_length": 123,
            "text": "free-form final model text must not persist",
            "error": terminal_error,
            "class": "provider_error",
            "unexpected": "free-form terminal metadata must not persist",
        },
    )

    run = store.get_agent_run("run-field-safe")
    checkpoints = store.list_agent_run_checkpoints("run-field-safe")
    assert run["source_refs"] == [
        {
            "id": "source-safe",
            "title": "Source [REDACTED]",
            "url": "https://example.test/source/path",
            "provider": "Google Drive",
            "stale": False,
            "truncated": True,
        },
        {
            "id": "source-unsafe-url",
            "title": "Unsafe URL",
            "url": None,
            "provider": "Web",
            "stale": False,
            "truncated": False,
        },
    ]
    assert run["artifact_refs"] == [
        {
            "id": "artifact-safe",
            "artifact_type": "shortlist",
            "title": "Artifact [REDACTED]",
            "external_url": "http://files.test/artifacts/one",
            "stale": True,
            "truncated": False,
        }
    ]
    assert run["terminal_result"] == {
        "status": "error",
        "message_id": "message-safe",
        "text_length": 123,
        "error": (
            "Basic research-methodology stays readable.\n"
            "Provider [REDACTED]\n"
            '{"Authorization":"[REDACTED]"}'
        ),
        "class": "provider_error",
    }
    assert len(run["terminal_result"]["error"]) <= 512
    assert "Basic research" in run["original_goal"]
    assert "Bearer market" in run["original_goal"]
    durable = json.dumps({"run": run, "checkpoints": checkpoints})
    for secret in (
        *provider_secrets.values(),
        "cHJpdmF0ZS1rZXktbWF0ZXJpYWw=",
        "json-header-secret",
        "terminal-json-secret",
        "basic-header-secret",
        "aws-query-secret",
        "signature-secret",
        "key-secret",
        "code-secret",
        "user:password",
        "source-key",
        "source-signature",
        "artifact-key",
        "artifact-code",
        "free-form source preview",
        "free-form source body",
        "free-form source metadata",
        "free-form artifact preview",
        "free-form artifact payload",
        "free-form artifact metadata",
        "free-form final model text",
        "free-form terminal metadata",
    ):
        assert secret not in durable


@pytest.mark.parametrize(
    "ordinary_text",
    [
        "Basic research-methodology",
        "Bearer market-analysis-report",
        "code: Python",
        "signature: Kind regards",
        "key=value",
        "First ordinary line\nSecond ordinary line\nThird ordinary line",
    ],
)
def test_ordinary_long_prose_survives_sanitization_and_persistence_exactly(
    tmp_path, ordinary_text
):
    assert redact_sensitive_assignments(ordinary_text) == ordinary_text
    store = ConversationStore(tmp_path)
    store.start_agent_run(
        run_id="run-ordinary-prose",
        session_id="thread-ordinary-prose",
        trigger="chat",
        original_goal=ordinary_text,
        provider_model_id="fake",
    )
    store.checkpoint_agent_run(
        "run-ordinary-prose",
        kind="tool_completed",
        payload={"summary": ordinary_text},
        source_refs=[
            {
                "id": "source-ordinary",
                "title": ordinary_text,
                "provider": ordinary_text,
            }
        ],
        artifact_refs=[
            {
                "id": "artifact-ordinary",
                "artifact_type": "note",
                "title": ordinary_text,
            }
        ],
    )
    store.checkpoint_agent_run(
        "run-ordinary-prose",
        kind="terminal",
        payload={"status": "failed"},
        state="failed",
        terminal_result={"status": "error", "error": ordinary_text},
    )

    run = store.get_agent_run("run-ordinary-prose")
    checkpoints = store.list_agent_run_checkpoints("run-ordinary-prose")
    assert run["original_goal"] == ordinary_text
    assert run["source_refs"][0]["title"] == ordinary_text
    assert run["source_refs"][0]["provider"] == ordinary_text
    assert run["artifact_refs"][0]["title"] == ordinary_text
    assert run["terminal_result"]["error"] == ordinary_text
    assert checkpoints[1]["payload"]["summary"] == ordinary_text


@pytest.mark.parametrize(
    ("secret_text", "expected"),
    [
        (
            "signature=9f86d081884c7d659a2feaa0c55ad015",
            "signature=[REDACTED]",
        ),
        (
            "code=4/0AdQt8qgeWmYx9n24rNQ7sLcP6vB3k",
            "code=[REDACTED]",
        ),
        (
            "key=0123456789abcdef0123456789abcdef",
            "key=[REDACTED]",
        ),
        (
            "AWSAccessKeyId=" + "AK" + "IAIOSFODNN7EXAMPLE",
            "AWSAccessKeyId=[REDACTED]",
        ),
    ],
)
def test_high_entropy_named_credentials_are_redacted_without_broad_word_rules(
    secret_text, expected
):
    assert redact_sensitive_assignments(secret_text) == expected


def test_structured_aws_key_is_sensitive_but_ordinary_signature_and_code_are_not(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    store.start_agent_run(
        run_id="run-structured-keys",
        session_id="thread-structured-keys",
        trigger="chat",
        original_goal="Keep structured metadata safe.",
        provider_model_id="fake",
    )
    store.checkpoint_agent_run(
        "run-structured-keys",
        kind="tool_completed",
        payload={
            "AWSAccessKeyId": "AK" + "IAIOSFODNN7EXAMPLE",
            "signature": "Kind regards",
            "code": "Python",
            "key": "value",
        },
    )

    payload = store.list_agent_run_checkpoints("run-structured-keys")[1]["payload"]
    assert payload == {
        "signature": "Kind regards",
        "code": "Python",
        "key": "value",
    }


def test_agent_run_raw_goal_fingerprint_preserves_secret_identity_without_storage(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    identity = {
        "run_id": "run-secret-goal",
        "session_id": "thread-secret-goal",
        "trigger": "chat",
        "provider_model_id": "fake",
        "parent_run_id": None,
    }
    raw_goal = "Use token=goal-secret-one to search."

    first = store.start_agent_run(**identity, original_goal=raw_goal)
    duplicate = store.start_agent_run(**identity, original_goal=raw_goal)

    assert duplicate == first
    with pytest.raises(ValueError, match="conflicting Agent Run metadata"):
        store.start_agent_run(
            **identity,
            original_goal="Use token=goal-secret-two to search.",
        )
    persisted = store.get_agent_run("run-secret-goal")
    assert persisted["original_goal"] == "Use token=[REDACTED] to search."
    assert "original_goal_fingerprint" not in persisted
    assert [
        item["kind"]
        for item in store.list_agent_run_checkpoints("run-secret-goal")
    ] == ["run_started"]
    durable = json.dumps(
        {
            "run": persisted,
            "checkpoints": store.list_agent_run_checkpoints("run-secret-goal"),
        }
    )
    assert "goal-secret-one" not in durable
    assert "goal-secret-two" not in durable
    with sqlite3.connect(tmp_path / "club.db") as db:
        fingerprint = db.execute(
            "SELECT original_goal_fingerprint FROM agent_runs WHERE run_id = ?",
            ("run-secret-goal",),
        ).fetchone()[0]
    assert fingerprint == hashlib.sha256(raw_goal.encode("utf-8")).hexdigest()


def test_existing_agent_runs_schema_migrates_and_backfills_goal_fingerprint(tmp_path):
    stored_goal = "Review token=[REDACTED] safely."
    with sqlite3.connect(tmp_path / "club.db") as db:
        db.execute(
            """
            CREATE TABLE agent_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_run_id TEXT,
                trigger TEXT NOT NULL,
                original_goal TEXT NOT NULL,
                current_state TEXT NOT NULL,
                provider_model_id TEXT,
                checkpoint_sequence INTEGER NOT NULL DEFAULT 0,
                skills_loaded TEXT NOT NULL DEFAULT '[]',
                source_refs TEXT NOT NULL DEFAULT '[]',
                artifact_refs TEXT NOT NULL DEFAULT '[]',
                usage TEXT NOT NULL DEFAULT '{}',
                terminal_result TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        db.execute(
            """
            INSERT INTO agent_runs (
                run_id, session_id, parent_run_id, trigger, original_goal,
                current_state, provider_model_id, checkpoint_sequence,
                skills_loaded, source_refs, artifact_refs, usage,
                terminal_result, created_at, started_at, updated_at, finished_at
            ) VALUES (
                'run-legacy-agent', 'thread-legacy-agent', NULL, 'chat', ?,
                'complete', 'fake', 0, '[]', '[]', '[]', '{}',
                '{"status":"ok","text":"done"}', ?, ?, ?, ?
            )
            """,
            (
                stored_goal,
                "2026-08-26T12:00:00+00:00",
                "2026-08-26T12:00:00+00:00",
                "2026-08-26T12:01:00+00:00",
                "2026-08-26T12:01:00+00:00",
            ),
        )

    store = ConversationStore(tmp_path)
    expected = hashlib.sha256(stored_goal.encode("utf-8")).hexdigest()
    with sqlite3.connect(tmp_path / "club.db") as db:
        columns = {
            row[1] for row in db.execute("PRAGMA table_info(agent_runs)").fetchall()
        }
        assert "original_goal_fingerprint" in columns
        fingerprint = db.execute(
            """
            SELECT original_goal_fingerprint
            FROM agent_runs WHERE run_id = 'run-legacy-agent'
            """
        ).fetchone()[0]

    assert fingerprint == expected
    existing = store.start_agent_run(
        run_id="run-legacy-agent",
        session_id="thread-legacy-agent",
        parent_run_id=None,
        trigger="chat",
        original_goal=stored_goal,
        provider_model_id="fake",
    )
    assert existing["original_goal"] == stored_goal
    assert "original_goal_fingerprint" not in existing
    reopened = ConversationStore(tmp_path)
    assert reopened.get_agent_run("run-legacy-agent") == existing


def test_goal_fingerprint_provenance_distinguishes_raw_and_irrecoverable_rows(
    tmp_path,
):
    raw_goal = "Use token=raw-goal-secret for sourcing."
    raw_stored_goal = "Use token=[REDACTED] for sourcing."
    legacy_stored_goal = "Use access_token=[REDACTED] for sourcing."
    with sqlite3.connect(tmp_path / "club.db") as db:
        db.execute(
            """
            CREATE TABLE agent_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_run_id TEXT,
                trigger TEXT NOT NULL,
                original_goal TEXT NOT NULL,
                original_goal_fingerprint TEXT,
                current_state TEXT NOT NULL,
                provider_model_id TEXT,
                checkpoint_sequence INTEGER NOT NULL DEFAULT 0,
                skills_loaded TEXT NOT NULL DEFAULT '[]',
                source_refs TEXT NOT NULL DEFAULT '[]',
                artifact_refs TEXT NOT NULL DEFAULT '[]',
                usage TEXT NOT NULL DEFAULT '{}',
                terminal_result TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        for values in (
            (
                "run-raw-provenance",
                "thread-raw-provenance",
                raw_stored_goal,
                hashlib.sha256(raw_goal.encode("utf-8")).hexdigest(),
            ),
            (
                "run-legacy-provenance",
                "thread-legacy-provenance",
                legacy_stored_goal,
                hashlib.sha256(legacy_stored_goal.encode("utf-8")).hexdigest(),
            ),
        ):
            db.execute(
                """
                INSERT INTO agent_runs (
                    run_id, session_id, parent_run_id, trigger, original_goal,
                    original_goal_fingerprint, current_state, provider_model_id,
                    checkpoint_sequence, skills_loaded, source_refs,
                    artifact_refs, usage, terminal_result, created_at,
                    started_at, updated_at, finished_at
                ) VALUES (?, ?, NULL, 'chat', ?, ?, 'complete', 'fake',
                          0, '[]', '[]', '[]', '{}', NULL, ?, ?, ?, ?)
                """,
                (
                    *values,
                    "2026-08-26T12:00:00+00:00",
                    "2026-08-26T12:00:00+00:00",
                    "2026-08-26T12:01:00+00:00",
                    "2026-08-26T12:01:00+00:00",
                ),
            )

    store = ConversationStore(tmp_path)
    with sqlite3.connect(tmp_path / "club.db") as db:
        columns = {
            row[1] for row in db.execute("PRAGMA table_info(agent_runs)").fetchall()
        }
        assert "original_goal_fingerprint_source" in columns
        provenance = dict(
            db.execute(
                """
                SELECT run_id, original_goal_fingerprint_source
                FROM agent_runs ORDER BY run_id
                """
            ).fetchall()
        )

    assert provenance == {
        "run-legacy-provenance": "legacy_sanitized",
        "run-raw-provenance": "raw",
    }
    assert "original_goal_fingerprint_source" not in store.get_agent_run(
        "run-raw-provenance"
    )
    assert store.start_agent_run(
        run_id="run-raw-provenance",
        session_id="thread-raw-provenance",
        parent_run_id=None,
        trigger="chat",
        original_goal=raw_goal,
        provider_model_id="fake",
    )["run_id"] == "run-raw-provenance"
    with pytest.raises(ValueError, match="conflicting Agent Run metadata"):
        store.start_agent_run(
            run_id="run-raw-provenance",
            session_id="thread-raw-provenance",
            parent_run_id=None,
            trigger="chat",
            original_goal=raw_goal.replace("raw-goal-secret", "different-secret"),
            provider_model_id="fake",
        )
    assert store.start_agent_run(
        run_id="run-legacy-provenance",
        session_id="thread-legacy-provenance",
        parent_run_id=None,
        trigger="chat",
        original_goal=legacy_stored_goal,
        provider_model_id="fake",
    )["run_id"] == "run-legacy-provenance"
    with pytest.raises(ValueError, match="start a new run"):
        store.start_agent_run(
            run_id="run-legacy-provenance",
            session_id="thread-legacy-provenance",
            parent_run_id=None,
            trigger="chat",
            original_goal="Use access_token=unknown-original-secret for sourcing.",
            provider_model_id="fake",
        )


def test_agent_run_privacy_migration_marker_skips_second_history_scan(tmp_path):
    store = ConversationStore(tmp_path)
    store.start_agent_run(
        run_id="run-migration-marker",
        session_id="thread-migration-marker",
        trigger="chat",
        original_goal="Ordinary goal",
        provider_model_id="fake",
    )
    store.checkpoint_agent_run(
        "run-migration-marker",
        kind="terminal",
        payload={"status": "ok"},
        state="complete",
        terminal_result={"status": "ok", "text_length": 0},
    )
    with sqlite3.connect(tmp_path / "club.db") as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "schema_migrations" in tables
        markers = {
            row[0]
            for row in db.execute("SELECT name FROM schema_migrations").fetchall()
        }
        assert "agent_run_privacy_v3" in markers
        db.execute(
            """
            CREATE TRIGGER reject_agent_run_rescan
            BEFORE UPDATE ON agent_runs
            BEGIN
                SELECT RAISE(FAIL, 'agent run history rescan attempted');
            END
            """
        )

    reopened = ConversationStore(tmp_path)

    assert reopened.get_agent_run("run-migration-marker")["current_state"] == "complete"


def test_agent_run_privacy_migration_rolls_back_marker_and_retries(tmp_path):
    with sqlite3.connect(tmp_path / "club.db") as db:
        db.executescript(
            """
            CREATE TABLE agent_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_run_id TEXT,
                trigger TEXT NOT NULL,
                original_goal TEXT NOT NULL,
                current_state TEXT NOT NULL,
                provider_model_id TEXT,
                checkpoint_sequence INTEGER NOT NULL DEFAULT 0,
                skills_loaded TEXT NOT NULL DEFAULT '[]',
                source_refs TEXT NOT NULL DEFAULT '[]',
                artifact_refs TEXT NOT NULL DEFAULT '[]',
                usage TEXT NOT NULL DEFAULT '{}',
                terminal_result TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE agent_run_checkpoints (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence)
            );
            INSERT INTO agent_runs (
                run_id, session_id, trigger, original_goal, current_state,
                provider_model_id, checkpoint_sequence, skills_loaded,
                source_refs, artifact_refs, usage, created_at, started_at,
                updated_at
            ) VALUES (
                'run-migration-retry', 'thread-migration-retry', 'chat',
                'Use token=rollback-secret', 'complete', 'fake', 0, '[]',
                '[]', '[]', '{}', '2026-08-26T12:00:00+00:00',
                '2026-08-26T12:00:00+00:00', '2026-08-26T12:00:00+00:00'
            );
            CREATE TRIGGER block_agent_run_privacy_update
            BEFORE UPDATE ON agent_runs
            BEGIN
                SELECT RAISE(FAIL, 'privacy migration blocked');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="privacy migration blocked"):
        ConversationStore(tmp_path)
    with sqlite3.connect(tmp_path / "club.db") as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "schema_migrations" in tables
        assert db.execute(
            """
            SELECT 1 FROM schema_migrations
            WHERE name = 'agent_run_privacy_v3'
            """
        ).fetchone() is None
        db.execute("DROP TRIGGER block_agent_run_privacy_update")

    recovered = ConversationStore(tmp_path)

    assert recovered.get_agent_run("run-migration-retry")["original_goal"] == (
        "Use token=[REDACTED]"
    )
    with sqlite3.connect(tmp_path / "club.db") as db:
        assert db.execute(
            """
            SELECT 1 FROM schema_migrations
            WHERE name = 'agent_run_privacy_v3'
            """
        ).fetchone() == (1,)


def test_agent_runs_schema_indexes_session_and_created_at(tmp_path):
    ConversationStore(tmp_path)

    with sqlite3.connect(tmp_path / "club.db") as db:
        indexes = {
            row[1] for row in db.execute("PRAGMA index_list(agent_runs)").fetchall()
        }
        assert "agent_runs_session_created" in indexes
        columns = [
            row[2]
            for row in db.execute(
                "PRAGMA index_info(agent_runs_session_created)"
            ).fetchall()
        ]
    assert columns == ["session_id", "created_at"]


def test_existing_agent_run_migration_scrubs_persisted_projection_and_checkpoints(
    tmp_path,
):
    raw_goal = (
        "Review the shortlist.\n"
        "access_token=legacy-goal-secret\n"
        "Keep this line."
    )
    usage_json = '{"model_calls":7,"tool_calls":3}'
    with sqlite3.connect(tmp_path / "club.db") as db:
        db.executescript(
            """
            CREATE TABLE agent_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_run_id TEXT,
                trigger TEXT NOT NULL,
                original_goal TEXT NOT NULL,
                current_state TEXT NOT NULL,
                provider_model_id TEXT,
                checkpoint_sequence INTEGER NOT NULL DEFAULT 0,
                skills_loaded TEXT NOT NULL DEFAULT '[]',
                source_refs TEXT NOT NULL DEFAULT '[]',
                artifact_refs TEXT NOT NULL DEFAULT '[]',
                usage TEXT NOT NULL DEFAULT '{}',
                terminal_result TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE agent_run_checkpoints (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence)
            );
            """
        )
        db.execute(
            """
            INSERT INTO agent_runs (
                run_id, session_id, parent_run_id, trigger, original_goal,
                current_state, provider_model_id, checkpoint_sequence,
                skills_loaded, source_refs, artifact_refs, usage,
                terminal_result, created_at, started_at, updated_at, finished_at
            ) VALUES (?, ?, NULL, 'chat', ?, 'complete', 'fake', 2,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-legacy-private",
                "thread-legacy-private",
                raw_goal,
                json.dumps(
                    [
                        "ordinary-legacy-skill",
                        "load client_secret=legacy-skill-secret",
                    ]
                ),
                json.dumps(
                    [
                        {
                            "id": "legacy-source",
                            "title": "Notes refresh_token=legacy-source-secret",
                            "raw_body": "legacy-source-body",
                        }
                    ]
                ),
                json.dumps(
                    [
                        {
                            "id": "legacy-artifact",
                            "artifact_type": "draft",
                            "title": (
                                "Draft Authorization: Basic "
                                "legacy-artifact-credential"
                            ),
                            "raw_payload": "legacy-artifact-payload",
                        }
                    ]
                ),
                usage_json,
                json.dumps(
                    {
                        "status": "ok",
                        "text": "Done api_key=legacy-terminal-secret",
                        "password": "legacy-terminal-password",
                    }
                ),
                "2026-08-26T12:00:00+00:00",
                "2026-08-26T12:00:00+00:00",
                "2026-08-26T12:01:00+00:00",
                "2026-08-26T12:01:00+00:00",
            ),
        )
        db.execute(
            """
            INSERT INTO agent_run_checkpoints
                (run_id, sequence, kind, payload, created_at)
            VALUES (?, 1, 'tool_completed', ?, ?)
            """,
            (
                "run-legacy-private",
                json.dumps(
                    {
                        "name": "drive_read",
                        "summary": (
                            "Authorization: Bearer legacy-checkpoint-secret"
                        ),
                        "body": "legacy-checkpoint-body",
                    }
                ),
                "2026-08-26T12:00:30+00:00",
            ),
        )
        db.execute(
            """
            INSERT INTO agent_run_checkpoints
                (run_id, sequence, kind, payload, created_at)
            VALUES (?, 2, 'terminal', 'not-json', ?)
            """,
            ("run-legacy-private", "2026-08-26T12:01:00+00:00"),
        )

    expected_fingerprint = hashlib.sha256(raw_goal.encode("utf-8")).hexdigest()
    secret_values = (
        "legacy-goal-secret",
        "legacy-skill-secret",
        "legacy-source-secret",
        "legacy-source-body",
        "legacy-artifact-credential",
        "legacy-artifact-payload",
        "legacy-terminal-secret",
        "legacy-terminal-password",
        "legacy-checkpoint-secret",
        "legacy-checkpoint-body",
    )
    store = ConversationStore(tmp_path)
    run = store.get_agent_run("run-legacy-private")
    checkpoints = store.list_agent_run_checkpoints("run-legacy-private")

    assert run["original_goal"] == (
        "Review the shortlist.\naccess_token=[REDACTED]\nKeep this line."
    )
    assert run["skills_loaded"] == [
        "ordinary-legacy-skill",
        "load client_secret=[REDACTED]",
    ]
    assert run["source_refs"] == [
        {
            "id": "legacy-source",
            "title": "Notes refresh_token=[REDACTED]",
            "url": None,
            "provider": "",
            "stale": False,
            "truncated": False,
        }
    ]
    assert run["artifact_refs"] == [
        {
            "id": "legacy-artifact",
            "artifact_type": "draft",
            "title": "Draft Authorization:[REDACTED]",
            "external_url": None,
            "stale": False,
            "truncated": False,
        }
    ]
    assert run["terminal_result"] == {
        "status": "ok",
        "text_length": len("Done api_key=legacy-terminal-secret"),
    }
    assert run["usage"] == {"model_calls": 7, "tool_calls": 3}
    assert run["current_state"] == "complete"
    assert run["checkpoint_sequence"] == 2
    assert checkpoints[0]["payload"] == {
        "name": "drive_read",
        "summary": "Authorization:[REDACTED]",
    }
    assert checkpoints[1]["payload"] == {}
    returned = json.dumps({"run": run, "checkpoints": checkpoints})
    assert all(secret not in returned for secret in secret_values)

    with sqlite3.connect(tmp_path / "club.db") as db:
        persisted_row = db.execute(
            """
            SELECT original_goal, original_goal_fingerprint, skills_loaded,
                   source_refs, artifact_refs, usage, terminal_result,
                   created_at, started_at, updated_at, finished_at,
                   current_state, checkpoint_sequence
            FROM agent_runs WHERE run_id = 'run-legacy-private'
            """
        ).fetchone()
        persisted_checkpoints = db.execute(
            """
            SELECT sequence, kind, payload, created_at
            FROM agent_run_checkpoints
            WHERE run_id = 'run-legacy-private'
            ORDER BY sequence
            """
        ).fetchall()
    persisted = repr((persisted_row, persisted_checkpoints))
    assert all(secret not in persisted for secret in secret_values)
    assert persisted_row[1] == expected_fingerprint
    assert persisted_row[5] == usage_json

    assert store.start_agent_run(
        run_id="run-legacy-private",
        session_id="thread-legacy-private",
        parent_run_id=None,
        trigger="chat",
        original_goal=raw_goal,
        provider_model_id="fake",
    ) == run
    with pytest.raises(ValueError, match="conflicting Agent Run metadata"):
        store.start_agent_run(
            run_id="run-legacy-private",
            session_id="thread-legacy-private",
            parent_run_id=None,
            trigger="chat",
            original_goal=raw_goal.replace(
                "legacy-goal-secret", "different-goal-secret"
            ),
            provider_model_id="fake",
        )

    reopened = ConversationStore(tmp_path)
    assert reopened.get_agent_run("run-legacy-private") == run
    assert reopened.list_agent_run_checkpoints("run-legacy-private") == checkpoints
    with sqlite3.connect(tmp_path / "club.db") as db:
        second_row = db.execute(
            """
            SELECT original_goal, original_goal_fingerprint, skills_loaded,
                   source_refs, artifact_refs, usage, terminal_result,
                   created_at, started_at, updated_at, finished_at,
                   current_state, checkpoint_sequence
            FROM agent_runs WHERE run_id = 'run-legacy-private'
            """
        ).fetchone()
        second_checkpoints = db.execute(
            """
            SELECT sequence, kind, payload, created_at
            FROM agent_run_checkpoints
            WHERE run_id = 'run-legacy-private'
            ORDER BY sequence
            """
        ).fetchall()
    assert (second_row, second_checkpoints) == (
        persisted_row,
        persisted_checkpoints,
    )


def test_agent_run_start_rejects_conflicting_immutable_metadata(tmp_path):
    store = ConversationStore(tmp_path)
    identity = {
        "run_id": "run-immutable",
        "session_id": "thread-immutable",
        "parent_run_id": "run-parent",
        "trigger": "chat",
        "original_goal": "Find candidates.",
        "provider_model_id": "fake",
    }
    original = store.start_agent_run(**identity)

    for field, conflicting in (
        ("parent_run_id", "different-parent"),
        ("trigger", "chat_queue"),
        ("original_goal", "Send outreach."),
        ("provider_model_id", "different-model"),
    ):
        with pytest.raises(ValueError, match="conflicting Agent Run metadata"):
            store.start_agent_run(**{**identity, field: conflicting})

    assert store.start_agent_run(**identity) == original
    assert store.get_agent_run("run-immutable") == original
    assert [
        item["kind"]
        for item in store.list_agent_run_checkpoints("run-immutable")
    ] == ["run_started"]


def test_store_reopen_interrupts_only_running_agent_runs(tmp_path):
    store = ConversationStore(tmp_path)
    for run_id in ("run-running", "run-waiting", "run-question", "run-complete"):
        store.start_agent_run(
            run_id=run_id,
            session_id="thread-alpha",
            trigger="chat",
            original_goal=run_id,
            provider_model_id="fake",
        )
    store.checkpoint_agent_run(
        "run-waiting",
        kind="waiting_approval",
        payload={"id": "approval-1", "name": "gmail_send"},
        state="waiting_approval",
    )
    store.checkpoint_agent_run(
        "run-question",
        kind="user_input",
        payload={"text_length": 0},
        state="waiting_question",
    )
    store.checkpoint_agent_run(
        "run-complete",
        kind="terminal",
        payload={"status": "ok", "text_length": 4},
        state="complete",
        terminal_result={"status": "ok", "text": "done"},
    )

    reopened = ConversationStore(tmp_path)

    assert reopened.get_agent_run("run-running")["current_state"] == "interrupted"
    assert reopened.get_agent_run("run-running")["finished_at"] is None
    assert [
        item["kind"]
        for item in reopened.list_agent_run_checkpoints("run-running")
    ] == ["run_started", "process_interrupted"]
    assert reopened.get_agent_run("run-waiting")["current_state"] == "waiting_approval"
    assert reopened.get_agent_run("run-waiting")["finished_at"] is None
    assert reopened.get_agent_run("run-question")["current_state"] == "waiting_question"
    assert reopened.get_agent_run("run-question")["finished_at"] is None
    assert reopened.get_agent_run("run-complete")["current_state"] == "complete"
    assert [
        item["kind"]
        for item in reopened.list_agent_run_checkpoints("run-complete")
    ] == ["run_started", "terminal"]


def test_user_input_checkpoint_waits_for_durable_transcript_append(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    original_append = store.append

    def fail_user_append(session_id, message):
        if message.get("role") == "user":
            raise RuntimeError("user transcript append failed")
        return original_append(session_id, message)

    monkeypatch.setattr(store, "append", fail_user_append)
    result = asyncio.run(
        run_turn(
            text="Find candidates",
            sid="thread-user-order",
            store=store,
            provider=FakeProvider(deltas=("unused",)),
            persona=None,
            skills=None,
            inbox=inbox,
            openai_tools=[],
            execute_kwargs={},
        )
    )

    assert result["status"] == "error"
    assert store.load("thread-user-order") == []
    assert "user_input" not in {
        item["kind"]
        for item in store.list_agent_run_checkpoints(result["run_id"])
    }


def test_terminal_checkpoint_precedes_failed_terminal_event_projection(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    original_append_event = store.append_event

    def fail_terminal_event(session_id, event):
        if event.get("type") in {"turn_end", "turn_stopped", "error"}:
            raise RuntimeError("terminal event append failed")
        return original_append_event(session_id, event)

    monkeypatch.setattr(store, "append_event", fail_terminal_event)
    result = asyncio.run(
        run_turn(
            text="Find candidates",
            sid="thread-terminal-order",
            store=store,
            provider=FakeProvider(deltas=("Done",)),
            persona=None,
            skills=None,
            inbox=inbox,
            openai_tools=[],
            execute_kwargs={},
        )
    )

    assert result["status"] == "error"
    run = store.list_agent_runs(session_id="thread-terminal-order")[0]
    assert run["current_state"] == "complete"
    assert store.list_agent_run_checkpoints(run["run_id"])[-1]["kind"] == "terminal"
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (run["run_id"],),
        ).fetchone() == (None, None)


def test_approval_checkpoint_waits_for_canonical_inbox_resolution(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="approval-order-call",
                        name="gmail_draft",
                        arguments={
                            "to": "person@example.test",
                            "subject": "Hello",
                            "body": "Draft body",
                        },
                    )
                ]
            }
        ]
    )

    def fail_cancel(_item_id):
        raise RuntimeError("inbox cancel failed")

    async def choose_cancel(_item_id):
        return "cancel"

    monkeypatch.setattr(inbox, "cancel", fail_cancel)
    result = asyncio.run(
        run_turn(
            text="Draft outreach",
            sid="thread-approval-order",
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=inbox,
            openai_tools=[],
            execute_kwargs={},
            wait_permission=choose_cancel,
        )
    )

    assert result["status"] == "error"
    checkpoints = store.list_agent_run_checkpoints(result["run_id"])
    assert "waiting_approval" in {item["kind"] for item in checkpoints}
    assert "approval_resolved" not in {item["kind"] for item in checkpoints}


def test_websocket_chat_persists_canonical_agent_run_matching_event_identity(tmp_path):
    application = create_app(
        token=TOKEN,
        provider=FakeProvider(deltas=("Three candidates are ready.",)),
        state=tmp_path,
    )
    sid = application.state.store.open_session_id()

    with TestClient(application).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json(
            {
                "type": "chat",
                "text": "Find three strong candidates.",
                "session_id": sid,
            }
        )
        events = _drain(ws)

    run_id = events[0]["run_id"]
    assert {event["run_id"] for event in events} == {run_id}
    run = application.state.store.get_agent_run(run_id)
    assert run is not None
    assert run["session_id"] == sid
    assert run["trigger"] == "chat"
    assert run["original_goal"] == "Find three strong candidates."
    assert run["provider_model_id"] == "fake"
    assert run["current_state"] == "complete"
    assert run["usage"] == {"model_calls": 1}
    assert run["terminal_result"] == {
        "status": "ok",
        "message_id": events[-1]["message_id"],
        "text_length": len("Three candidates are ready."),
    }
    checkpoints = application.state.store.list_agent_run_checkpoints(run_id)
    assert [item["kind"] for item in checkpoints] == [
        "run_started",
        "user_input",
        "model_pending",
        "model_completed",
        "terminal",
    ]
    assert checkpoints[1]["payload"] == {
        "text_length": len("Find three strong candidates.")
    }
    assert checkpoints[-1]["payload"] == {
        "status": "ok",
        "state": "complete",
        "text_length": len("Three candidates are ready."),
    }


def test_tool_run_aggregates_skill_source_and_artifact_refs_without_delta_checkpoints(
    tmp_path, monkeypatch
):
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-skill",
                        name="load_skill",
                        arguments={"name": "candidate-search"},
                    ),
                    ToolCall(
                        id="call-drive",
                        name="drive_search",
                        arguments={"query": "Codeology"},
                    ),
                ]
            },
            {"deltas": ("Shortlist ready.",)},
        ]
    )

    def fake_execute(name, arguments, **_kwargs):
        if name == "load_skill":
            return True, {
                "name": "candidate-search",
                "description": "Search candidates",
                "instructions": "raw skill instructions must not enter checkpoints",
            }
        return True, {
            "sources": [
                {
                    "id": "source-drive-1",
                    "title": "Candidate notes",
                    "url": "https://example.test/source",
                    "provider": "Google Drive",
                    "raw_body": "private source body",
                }
            ],
            "artifacts": [
                {
                    "id": "artifact-shortlist-1",
                    "artifact_type": "shortlist",
                    "title": "Candidate shortlist",
                    "external_url": "https://example.test/shortlist",
                    "raw_payload": "private artifact payload",
                }
            ],
        }

    monkeypatch.setattr("coworker.turn.execute", fake_execute)
    application = create_app(token=TOKEN, provider=provider, state=tmp_path)
    sid = application.state.store.open_session_id()
    persistence_order = []
    original_append = application.state.store.append
    original_append_event = application.state.store.append_event
    original_checkpoint = application.state.store.agent_runs.checkpoint_leased

    def tracked_append(session_id, message):
        original_append(session_id, message)
        if message.get("role") in {"assistant", "tool"}:
            persistence_order.append(f"message:{message['role']}")

    def tracked_append_event(session_id, event):
        original_append_event(session_id, event)
        if event.get("type") == "tool_finished":
            persistence_order.append("event:tool_finished")

    def tracked_checkpoint(lease, kind, continuation, **kwargs):
        checkpoint = original_checkpoint(lease, kind, continuation, **kwargs)
        if kind in {"model_completed", "tool_completed"}:
            persistence_order.append(f"checkpoint:{kind}")
        return checkpoint

    monkeypatch.setattr(application.state.store, "append", tracked_append)
    monkeypatch.setattr(
        application.state.store, "append_event", tracked_append_event
    )
    monkeypatch.setattr(
        application.state.store.agent_runs, "checkpoint_leased", tracked_checkpoint
    )
    with TestClient(application).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json(
            {"type": "chat", "text": "Build a shortlist", "session_id": sid}
        )
        events = _drain(ws)

    run_id = events[0]["run_id"]
    run = application.state.store.get_agent_run(run_id)
    assert run["skills_loaded"] == ["candidate-search"]
    assert run["source_refs"] == [
        {
            "id": "source-drive-1",
            "title": "Candidate notes",
            "url": "https://example.test/source",
            "provider": "Google Drive",
            "stale": False,
            "truncated": False,
        }
    ]
    assert run["artifact_refs"] == [
        {
            "id": "artifact-shortlist-1",
            "artifact_type": "shortlist",
            "title": "Candidate shortlist",
            "external_url": "https://example.test/shortlist",
            "stale": False,
            "truncated": False,
        }
    ]
    assert run["usage"] == {"model_calls": 2, "tool_calls": 2}
    checkpoints = application.state.store.list_agent_run_checkpoints(run_id)
    assert [item["kind"] for item in checkpoints] == [
        "run_started",
        "user_input",
        "model_pending",
        "model_completed",
        "tool_pending",
        "tool_completed",
        "tool_pending",
        "tool_completed",
        "model_pending",
        "model_completed",
        "terminal",
    ]
    durable_agent_run = json.dumps({"run": run, "checkpoints": checkpoints})
    assert "assistant_delta" not in durable_agent_run
    assert "raw skill instructions" not in durable_agent_run
    assert "private source body" not in durable_agent_run
    assert "private artifact payload" not in durable_agent_run
    assert persistence_order == [
        "message:assistant",
        "checkpoint:model_completed",
        "event:tool_finished",
        "message:tool",
        "checkpoint:tool_completed",
        "event:tool_finished",
        "message:tool",
        "checkpoint:tool_completed",
        "message:assistant",
        "checkpoint:model_completed",
    ]


def test_failed_run_preserves_only_a_safe_error_in_terminal_result(tmp_path):
    class FailingProvider:
        model_id = "failing"

        async def astream(self, *, messages, tools):
            if False:
                yield
            raise RuntimeError("provider unavailable token=provider-secret")

    application = create_app(
        token=TOKEN, provider=FailingProvider(), state=tmp_path
    )
    sid = application.state.store.open_session_id()

    with TestClient(application).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "Find candidates", "session_id": sid})
        events = _drain(ws)

    run = application.state.store.get_agent_run(events[0]["run_id"])
    assert run["current_state"] == "failed"
    assert run["terminal_result"] == {
        "status": "error",
        "message_id": events[-1]["message_id"],
        "text_length": 0,
        "error": "provider unavailable token=[REDACTED]",
        "class": "run_error",
    }
    durable_agent_run = json.dumps(
        {
            "run": run,
            "checkpoints": application.state.store.list_agent_run_checkpoints(
                run["run_id"]
            ),
        }
    )
    assert "provider-secret" not in durable_agent_run


def test_scheduled_receipt_links_to_schedule_triggered_agent_run(tmp_path):
    application = create_app(
        token=TOKEN,
        provider=FakeProvider(deltas=("Weekly review complete.",)),
        state=tmp_path,
    )
    job = application.state.store.add_job(
        "0 9 * * 1", "Review priority sourcing work."
    )

    response = TestClient(application).post(
        f"/v1/schedule/{job['id']}/run", headers={TOKEN_HEADER: TOKEN}
    )

    assert response.status_code == 200
    receipt = response.json()["run"]
    assert receipt["agent_run_id"]
    run = application.state.store.get_agent_run(receipt["agent_run_id"])
    assert run is not None
    assert run["trigger"] == "schedule"
    assert run["session_id"] == receipt["session_id"] == f"sched-{job['id']}"
    assert run["original_goal"] == "Review priority sourcing work."
    assert run["current_state"] == "complete"
